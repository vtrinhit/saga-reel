"""Task 9: ffmpeg chạy an toàn, ffprobe, đăng ký media, phục vụ media + thumbnail."""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import io
import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from sagareel.app import create_app
from sagareel.config import load_settings
from sagareel.core.errors import AppError
from sagareel.media import ffmpeg
from sagareel.media.probe import probe
from sagareel.services import media

# --- Fixture ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory) -> Path:
    """Video 64×112, 1 s, có tiếng — sinh một lần cho cả module."""
    out = tmp_path_factory.mktemp("samples") / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=64x112:d=1:r=30", "-f", "lavfi", "-i", "sine=d=1",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True,
    )
    return out


def _png(path: Path, size: tuple[int, int] = (720, 1280), color=(200, 40, 40)) -> Path:
    Image.new("RGB", size, color).save(path, "PNG")
    return path


@pytest.fixture
def session(db):
    with db.session() as s:
        yield s


@pytest.fixture
def client_with_media(settings, tmp_path, sample_video, make_local_client):
    with make_local_client(create_app(settings)) as c:
        with c.app.state.services.db.session() as s:
            vid = shutil.copy(sample_video, tmp_path / "clip.mp4")
            video = media.register(s, Path(vid), kind="video", origin="fake", settings=settings)
            image = media.register(s, _png(tmp_path / "pic.png"), kind="image", origin="upload", project_id=7,
                                   settings=settings)
            ids = (video.id, image.id)
        yield SimpleNamespace(client=c, settings=settings, video_id=ids[0], image_id=ids[1])


# --- ffmpeg.run --------------------------------------------------------------------------------------------------


async def test_run_reports_progress_and_creates_file(tmp_path):
    out = tmp_path / "a.mp4"; seen = []
    with ffmpeg.atomic_output(out) as part:
        await ffmpeg.run(["-f","lavfi","-i","testsrc2=s=64x112:d=1:r=30","-f","lavfi","-i","sine=d=1",
                          "-shortest","-c:v","libx264","-c:a","aac", str(part)], total_ms=1000, on_progress=seen.append)
    info = probe(out)
    assert out.exists() and info.width == 64 and info.has_audio and 900 <= info.duration_ms <= 1100 and seen
    assert all(0 <= p <= 100 for p in seen) and seen[-1] == 100
    assert seen == sorted(seen)
    assert not list(tmp_path.glob("*.part*"))


def test_atomic_output_keeps_extension_last(tmp_path):
    with ffmpeg.atomic_output(tmp_path / "sub" / "x.mp4") as part:
        assert part.name.endswith(".part.mp4") and part.parent.is_dir()
        part.write_bytes(b"ok")
    assert (tmp_path / "sub" / "x.mp4").read_bytes() == b"ok"


def test_atomic_output_removes_part_on_error(tmp_path):
    with pytest.raises(RuntimeError):
        with ffmpeg.atomic_output(tmp_path / "x.mp4") as part:
            part.write_bytes(b"half")
            raise RuntimeError("boom")
    assert list(tmp_path.iterdir()) == []


def test_command_inserts_hygiene_flags():
    cmd = ffmpeg.build_command(["-i", "in.mp4", "out.mp4"])
    assert cmd[1:8] == ["-nostdin", "-hide_banner", "-y", "-progress", "pipe:1", "-nostats", "-i"]
    assert cmd[-1] == "out.mp4"


def test_progress_pct_uses_microseconds_and_clamps():
    # ffmpeg báo `out_time_ms` bằng micro giây dù tên là ms.
    assert ffmpeg.progress_pct({"out_time_us": "500000"}, 1000) == 50.0
    assert ffmpeg.progress_pct({"out_time_ms": "250000"}, 1000) == 25.0
    assert ffmpeg.progress_pct({"out_time_us": "5000000"}, 1000) == 100.0
    assert ffmpeg.progress_pct({"out_time_us": "-20"}, 1000) is None
    assert ffmpeg.progress_pct({"out_time_us": "N/A"}, 1000) is None
    assert ffmpeg.progress_pct({"out_time_us": "1"}, None) is None


async def test_cancel_kills_process(tmp_path):
    out = tmp_path / "long.mp4"
    cancel = asyncio.Event()
    asyncio.get_running_loop().call_later(0.2, cancel.set)
    started = time.monotonic()
    with pytest.raises(ffmpeg.FFmpegError) as ei:
        with ffmpeg.atomic_output(out) as part:
            await ffmpeg.run(["-re", "-f", "lavfi", "-i", "testsrc2=s=64x112:d=60:r=30",
                              "-c:v", "libx264", str(part)], total_ms=60_000, cancel=cancel)
    assert ei.value.code == "ffmpeg_cancelled"
    assert time.monotonic() - started < 5
    assert list(tmp_path.iterdir()) == []


async def test_timeout_kills_process_and_removes_part(tmp_path):
    with pytest.raises(ffmpeg.FFmpegError) as ei:
        with ffmpeg.atomic_output(tmp_path / "t.mp4") as part:
            await ffmpeg.run(["-re", "-f", "lavfi", "-i", "testsrc2=s=64x112:d=60:r=30",
                              "-c:v", "libx264", str(part)], timeout=0.5)
    assert ei.value.code == "ffmpeg_timeout"
    assert list(tmp_path.iterdir()) == []


async def test_task_cancellation_stops_process_and_removes_part(tmp_path):
    async def job() -> None:
        with ffmpeg.atomic_output(tmp_path / "c.mp4") as part:
            await ffmpeg.run(["-re", "-f", "lavfi", "-i", "testsrc2=s=64x112:d=60:r=30", "-c:v", "libx264",
                              str(part)])

    task = asyncio.create_task(job())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(tmp_path.iterdir()) == []


async def test_progress_callback_error_stops_process(tmp_path):
    def boom(pct: float) -> None:
        raise RuntimeError("callback hỏng")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="callback hỏng"):
        with ffmpeg.atomic_output(tmp_path / "e.mp4") as part:
            await ffmpeg.run(["-re", "-f", "lavfi", "-i", "testsrc2=s=64x112:d=60:r=30", "-c:v", "libx264",
                              str(part)], total_ms=60_000, on_progress=boom)
    assert time.monotonic() - started < 5
    assert not list(tmp_path.iterdir())


async def test_failure_raises_with_stderr_tail(tmp_path):
    with pytest.raises(ffmpeg.FFmpegError) as ei:
        await ffmpeg.run(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "o.part.mp4")])
    assert ei.value.code == "ffmpeg_failed"
    assert "missing.mp4" in ei.value.stderr_tail
    assert not list(tmp_path.iterdir())


async def test_run_never_deletes_files_by_name(tmp_path):
    # run() không đoán đầu ra theo tên: file `.part` có sẵn mà ffmpeg chưa kịp mở vẫn còn nguyên.
    keep = tmp_path / "keep.part.wav"
    keep.write_bytes(b"data")
    with pytest.raises(ffmpeg.FFmpegError):
        await ffmpeg.run(["-f", "lavfi", "-i", "sine=d=0.2", "-c:a", "no_such_codec", str(keep)])
    assert keep.read_bytes() == b"data"


async def test_iter_lines_survives_huge_line():
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * 200_000 + b"\r\nnext\n")
    reader.feed_data(b"y" * 70_000)
    reader.feed_data(b"\nlast")
    reader.feed_eof()
    lines = [line async for line in ffmpeg.iter_lines(reader, max_line=1000)]
    assert lines == [b"x" * 1000, b"next", b"y" * 1000, b"last"]


async def test_stderr_tail_keeps_draining_after_huge_line():
    reader = asyncio.StreamReader()
    reader.feed_data(b"e" * 300_000 + b"\n" + "cuối\n".encode())
    reader.feed_eof()
    tail: deque[str] = deque(maxlen=5)
    await ffmpeg._read_tail(reader, tail)
    assert tail[-1] == "cuối" and len(tail[0]) == ffmpeg.MAX_LINE_BYTES


# --- probe -----------------------------------------------------------------------------------------------------


def test_probe_video(sample_video):
    info = probe(sample_video)
    assert (info.width, info.height) == (64, 112)
    assert info.vcodec == "h264" and info.acodec == "aac" and info.has_audio
    assert info.fps == pytest.approx(30, abs=0.5)
    assert info.size == sample_video.stat().st_size


def test_probe_skips_attached_picture_in_real_mp4(tmp_path):
    """MP4 có thêm luồng ảnh bìa mjpeg (như GrokTele) — thông số là của luồng video thật."""
    out, cover = tmp_path / "cover.mp4", _png(tmp_path / "cover.png", (32, 32))
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=64x112:d=1:r=24", "-f", "lavfi", "-i", "sine=d=1", "-i", str(cover),
         "-map", "0:v", "-map", "1:a", "-map", "2:v", "-c:v:0", "libx264", "-pix_fmt:v:0", "yuv420p",
         "-c:a", "aac", "-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic", str(out)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True,
    )
    covers = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_name:stream_disposition"
         "=attached_pic", "-of", "csv=p=0", str(out)], check=True, capture_output=True, text=True).stdout.split()
    assert sorted(covers) == ["h264,0", "mjpeg,1"]  # file mẫu thật sự có luồng ảnh bìa
    info = probe(out)
    assert (info.width, info.height, info.vcodec, info.acodec) == (64, 112, "h264", "aac")
    assert info.fps == pytest.approx(24, abs=0.5)


def test_probe_skips_attached_picture_listed_first(tmp_path, monkeypatch):
    """ffmpeg luôn xếp ảnh bìa xuống cuối khi ghi MP4, nhưng file của bên khác có thể để ảnh bìa trước."""
    import json

    from sagareel.media import probe as probe_mod

    f = tmp_path / "grok.mp4"
    f.write_bytes(b"x" * 10)
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "width": 32, "height": 32,
         "avg_frame_rate": "0/0", "r_frame_rate": "90000/1", "disposition": {"attached_pic": 1}},
        {"index": 1, "codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280,
         "avg_frame_rate": "24/1", "r_frame_rate": "24/1", "disposition": {"attached_pic": 0}},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "disposition": {"attached_pic": 0}},
    ]
    stdout = json.dumps({"streams": streams, "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                                                         "duration": "6.0"}}).encode()
    monkeypatch.setattr(probe_mod.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr=b""))
    info = probe(f)
    assert (info.width, info.height, info.vcodec, info.fps) == (720, 1280, "h264", 24.0)
    assert info.duration_ms == 6000 and info.acodec == "aac"


def test_probe_image_has_no_duration(tmp_path):
    info = probe(_png(tmp_path / "p.png", (64, 112)))
    assert (info.width, info.height, info.duration_ms, info.has_audio) == (64, 112, 0, False)


def test_probe_bad_file_raises(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(ffmpeg.FFmpegError) as ei:
        probe(bad)
    assert ei.value.code == "probe_failed"


# --- register --------------------------------------------------------------------------------------------------


def test_register_dedupes_by_sha(session, settings, tmp_path, sample_video):
    a = Path(shutil.copy(sample_video, tmp_path / "a.mp4"))
    b = Path(shutil.copy(sample_video, tmp_path / "b.MP4"))
    m1 = media.register(session, a, kind="video", origin="fake", meta={"prompt": "  giữ nguyên  "}, settings=settings)
    m2 = media.register(session, b, kind="video", origin="fake", settings=settings)
    assert m1.id == m2.id
    assert not a.exists() and not b.exists()  # move=True: nguồn được chuyển / bản trùng bị bỏ
    rel = Path(m1.path)  # tương đối so với settings.data_dir
    assert rel.parts[0] == "media" and rel.name == f"{m1.sha256}.mp4" and rel.parts[3] == m1.sha256[:2]
    stored = settings.data_dir / m1.path
    assert stored.is_file() and stored.stat().st_size == m1.size
    assert stored.is_relative_to(settings.media_dir)
    assert (m1.mime, m1.width, m1.height) == ("video/mp4", 64, 112)
    assert 900 <= m1.duration_ms <= 1100
    assert m1.meta["prompt"] == "  giữ nguyên  " and m1.meta["probe"]["has_audio"] is True


def test_register_copy_keeps_source(session, settings, tmp_path):
    src = _png(tmp_path / "keep.png", (64, 112))
    m = media.register(session, src, kind="image", origin="upload", project_id=3, move=False, settings=settings)
    assert src.exists() and (settings.data_dir / m.path).is_file()
    assert (m.mime, m.width, m.height, m.project_id, m.duration_ms) == ("image/png", 64, 112, 3, None)


def test_register_audio(session, settings, tmp_path):
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", str(wav)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    m = media.register(session, wav, kind="audio", origin="fake", settings=settings)
    assert (m.mime, m.width, m.height) == ("audio/wav", None, None)
    assert 900 <= m.duration_ms <= 1100 and m.meta["probe"]["has_audio"] is True


def test_prepare_needs_no_session(settings, tmp_path, sample_video):
    src = Path(shutil.copy(sample_video, tmp_path / "p.mp4"))
    prepared = media.prepare(src, kind="video", media_dir=settings.media_dir, origin="fake",
                             data_dir=settings.data_dir)
    assert not src.exists() and prepared.stored_path.is_file()
    assert prepared.path == prepared.stored_path.relative_to(settings.data_dir).as_posix()
    assert (prepared.width, prepared.height, prepared.mime) == (64, 112, "video/mp4")


@pytest.mark.parametrize("move", [True, False])
def test_prepare_on_already_stored_file_keeps_it(settings, tmp_path, move):
    first = media.prepare(_png(tmp_path / "o.png", (8, 8)), kind="image", media_dir=settings.media_dir,
                          origin="upload", data_dir=settings.data_dir)
    data = first.stored_path.read_bytes()
    # Nhận lại file mồ côi đã nằm đúng chỗ trong kho (vd. sau khi `record` thất bại).
    again = media.prepare(first.stored_path, kind="image", media_dir=settings.media_dir, origin="upload",
                          data_dir=settings.data_dir, move=move)
    assert again.path == first.path and again.stored_path == first.stored_path
    assert first.stored_path.read_bytes() == data and again.size == len(data)


def test_record_does_no_file_io(session, settings, tmp_path):
    prepared = media.prepare(_png(tmp_path / "r.png", (16, 16)), kind="image", media_dir=settings.media_dir,
                             origin="upload")
    prepared.stored_path.unlink()  # record chỉ dùng DB: nguồn lẫn bản trong kho biến mất cũng không sao
    m = media.record(session, prepared, project_id=5)
    assert (m.path, m.sha256, m.project_id, m.width) == (prepared.path, prepared.sha256, 5, 16)
    assert media.record(session, prepared).id == m.id  # trùng sha → bản ghi cũ


def test_reconcile_drops_or_restores_duplicate_copy(session, settings, tmp_path):
    first = media.prepare(_png(tmp_path / "a.png", (8, 8)), kind="image", media_dir=settings.media_dir,
                          origin="upload", data_dir=settings.data_dir)
    m = media.record(session, first)
    # Bản trùng sha được đặt ở thư mục tháng khác (vd. đăng ký lại sau một tháng).
    other = settings.media_dir / "2099" / "01" / first.sha256[:2] / first.stored_path.name
    other.parent.mkdir(parents=True)
    shutil.copy(first.stored_path, other)
    dup = dataclasses.replace(first, stored_path=other, path=other.relative_to(settings.data_dir).as_posix())
    assert media.record(session, dup).id == m.id
    media.reconcile(dup, m)
    assert not other.exists() and first.stored_path.is_file()  # bản thừa bị xoá
    shutil.copy(first.stored_path, other)
    first.stored_path.unlink()  # file của bản ghi cũ bị mất → bản mới thế chỗ
    media.reconcile(dup, m)
    assert first.stored_path.is_file() and not other.exists()


def test_media_served_when_db_lives_outside_data_dir(tmp_path, sample_video, make_local_client):
    base = load_settings(data_dir=tmp_path / "data", env_file=tmp_path / ".env.local", outputs_dir=tmp_path / "out")
    s = dataclasses.replace(base, db_path=tmp_path / "elsewhere" / "db.sqlite")
    with make_local_client(create_app(s)) as c:
        with c.app.state.services.db.session() as sess:
            mid = media.register(sess, Path(shutil.copy(sample_video, tmp_path / "v.mp4")), kind="video",
                                 origin="fake", settings=s).id
        assert not (tmp_path / "elsewhere" / "media").exists()
        r = c.get(f"/media/{mid}")
        assert r.status_code == 200 and r.content == sample_video.read_bytes()


def test_place_other_os_error_is_reported_and_keeps_source(session, settings, tmp_path, monkeypatch):
    src = _png(tmp_path / "locked.png", (8, 8))

    def deny(a, b):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(media.os, "replace", deny)
    with pytest.raises(AppError) as ei:
        media.register(session, src, kind="image", origin="upload", settings=settings)
    monkeypatch.undo()
    assert ei.value.code == "media_store_failed" and src.exists()
    assert not [p for p in settings.media_dir.rglob("*") if p.is_file()]


def test_place_cross_device_falls_back_to_copy(session, settings, tmp_path, monkeypatch):
    src = _png(tmp_path / "far.png", (8, 8))
    real_replace = os.replace

    def cross_device(a, b):
        if Path(a) == src:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(a, b)

    monkeypatch.setattr(media.os, "replace", cross_device)
    m = media.register(session, src, kind="image", origin="upload", settings=settings)
    monkeypatch.undo()
    assert not src.exists() and (settings.data_dir / m.path).is_file()


def test_register_rejects_unknown_kind(session, settings, tmp_path):
    with pytest.raises(ValueError):
        media.register(session, _png(tmp_path / "x.png", (8, 8)), kind="model", origin="upload", settings=settings)


async def test_progress_callback_error_on_final_block_is_raised(tmp_path):
    def fail_at_end(pct: float) -> None:
        if pct >= 100:
            raise RuntimeError("lỗi ở 100%")

    with pytest.raises(RuntimeError, match="lỗi ở 100%"):
        with ffmpeg.atomic_output(tmp_path / "f.mp4") as part:
            await ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=s=64x112:d=0.2:r=30", "-c:v", "libx264", str(part)],
                             total_ms=200, on_progress=fail_at_end)
    assert not list(tmp_path.iterdir())


# --- routes ----------------------------------------------------------------------------------------------------


def test_media_range_request(client_with_media):
    mid = client_with_media.video_id
    r = client_with_media.client.get(f"/media/{mid}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_media_full_and_download(client_with_media):
    c, mid = client_with_media.client, client_with_media.video_id
    r = c.get(f"/media/{mid}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert "content-disposition" not in r.headers
    r = c.get(f"/media/{mid}", params={"download": 1})
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("principal", [None, SimpleNamespace(role="viewer"), SimpleNamespace(role="guest")])
async def test_download_fails_closed_without_owner(principal):
    """Tải file gốc chỉ cho chủ: không có principal hoặc vai trò khác → 403, không đọc DB."""
    from sagareel.api.routes_media import get_media
    from sagareel.core.errors import Forbidden

    request = SimpleNamespace(state=SimpleNamespace(principal=principal), app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(Forbidden):
        await get_media(request, 1, download=True)


def test_media_missing_is_404(client_with_media):
    r = client_with_media.client.get("/media/999999")
    assert r.status_code == 404 and r.json()["error_code"] == "not_found"


def test_thumb_width_snapped(client_with_media):
    c, mid = client_with_media.client, client_with_media.image_id
    r = c.get(f"/media/{mid}/thumb", params={"w": 300})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (320, 569)
    cached = list((client_with_media.settings.cache_dir / "thumbs").rglob("*.jpg"))
    assert len(cached) == 1 and cached[0].name.endswith("_320.jpg")
    assert c.get(f"/media/{mid}/thumb", params={"w": 320}).content == r.content  # dùng lại cache
    assert Image.open(io.BytesIO(c.get(f"/media/{mid}/thumb", params={"w": 5000}).content)).width == 720


def test_thumb_video_first_frame(client_with_media):
    r = client_with_media.client.get(f"/media/{client_with_media.video_id}/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (64, 112)  # không phóng to quá kích thước gốc
    assert not list((client_with_media.settings.cache_dir / "thumbs").rglob("*.part*"))


async def test_failing_video_thumb_leaves_no_part(settings, tmp_path, monkeypatch):
    async def half_written_then_fail(args, **kw):
        Path(args[-1]).write_bytes(b"\xff\xd8 partial")  # ffmpeg đã mở/ghi đầu ra rồi mới hỏng (timeout, huỷ…)
        raise ffmpeg.FFmpegError("ffmpeg chạy quá 60 giây nên đã bị dừng.", code="ffmpeg_timeout")

    monkeypatch.setattr(media.ffmpeg, "run", half_written_then_fail)
    out = media.thumb_cache_path(settings, "ab" * 32, 320)
    with pytest.raises(ffmpeg.FFmpegError):
        await media.video_thumb(tmp_path / "v.mp4", out, 320)
    assert not out.exists() and not list(out.parent.glob("*.part*"))


def test_thumb_bad_width_is_validation_error(client_with_media):
    r = client_with_media.client.get(f"/media/{client_with_media.image_id}/thumb", params={"w": 0})
    assert r.status_code == 422 and r.json()["error_code"] == "validation_failed"


# --- ops -------------------------------------------------------------------------------------------------------


def test_media_get_op(client_with_media):
    c, mid = client_with_media.client, client_with_media.image_id
    r = c.post("/api/ops/media.get", json={"id": mid})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == mid and body["kind"] == "image" and body["url"] == f"/media/{mid}"
    assert body["thumb_url"] == f"/media/{mid}/thumb" and "path" not in body
    r = c.post("/api/ops/media.get", json={"id": 424242})
    assert r.status_code == 404 and r.json()["error_code"] == "not_found"


def test_media_list_op_filters_and_paginates(client_with_media):
    c = client_with_media.client
    page = c.post("/api/ops/media.list", json={"limit": 1}).json()
    assert [m["id"] for m in page["items"]] == [client_with_media.image_id] and page["next_cursor"]
    page2 = c.post("/api/ops/media.list", json={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert [m["id"] for m in page2["items"]] == [client_with_media.video_id] and page2["next_cursor"] is None
    assert [m["id"] for m in c.post("/api/ops/media.list", json={"kind": "video"}).json()["items"]] == [
        client_with_media.video_id]
    assert [m["id"] for m in c.post("/api/ops/media.list", json={"project_id": 7}).json()["items"]] == [
        client_with_media.image_id]


def test_media_ops_are_viewer_readable():
    from sagareel.core.ops import REGISTRY
    from sagareel.ops import load_all

    load_all()
    for name in ("media.get", "media.list"):
        assert REGISTRY[name].read_only and REGISTRY[name].viewer


def test_media_requires_auth_for_remote(client_with_media):
    """Qua tunnel (header chuyển tiếp) chưa xác thực → 401 cho file gốc, bản tải về và thumbnail; Bearer thì được."""
    c, mid = client_with_media.client, client_with_media.video_id
    remote = {"CF-Connecting-IP": "1.2.3.4"}
    for path in (f"/media/{mid}", f"/media/{mid}?download=1", f"/media/{mid}/thumb"):
        r = c.get(path, headers=remote)
        assert r.status_code == 401 and r.json()["error_code"] == "auth_required", path
    tok = c.post("/api/ops/token.create", json={"name": "xem-media", "role": "owner"}).json()["token"]
    r = c.get(f"/media/{mid}", headers={**remote, "Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
