"""Task 9: ffmpeg chạy an toàn, ffprobe, đăng ký media, phục vụ media + thumbnail."""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sagareel.app import create_app
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
def client_with_media(settings, tmp_path, sample_video):
    with TestClient(create_app(settings)) as c:
        with c.app.state.services.db.session() as s:
            vid = shutil.copy(sample_video, tmp_path / "clip.mp4")
            video = media.register(s, Path(vid), kind="video", origin="fake")
            image = media.register(s, _png(tmp_path / "pic.png"), kind="image", origin="upload", project_id=7)
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
    part = tmp_path / "t.part.mp4"
    with pytest.raises(ffmpeg.FFmpegError) as ei:
        # Không bọc atomic_output: run() tự xoá file `.part` đầu ra khi thất bại.
        await ffmpeg.run(["-re", "-f", "lavfi", "-i", "testsrc2=s=64x112:d=60:r=30",
                          "-c:v", "libx264", str(part)], timeout=0.5)
    assert ei.value.code == "ffmpeg_timeout"
    assert not part.exists()


async def test_task_cancellation_stops_process_and_removes_part(tmp_path):
    part = tmp_path / "c.part.mp4"
    task = asyncio.create_task(ffmpeg.run(["-re", "-f", "lavfi", "-i", "testsrc2=s=64x112:d=60:r=30",
                                           "-c:v", "libx264", str(part)]))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not part.exists()


async def test_progress_callback_error_stops_process(tmp_path):
    def boom(pct: float) -> None:
        raise RuntimeError("callback hỏng")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="callback hỏng"):
        await ffmpeg.run(["-re", "-f", "lavfi", "-i", "testsrc2=s=64x112:d=60:r=30", "-c:v", "libx264",
                          str(tmp_path / "e.part.mp4")], total_ms=60_000, on_progress=boom)
    assert time.monotonic() - started < 5
    assert not list(tmp_path.iterdir())


async def test_failure_raises_with_stderr_tail(tmp_path):
    with pytest.raises(ffmpeg.FFmpegError) as ei:
        await ffmpeg.run(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "o.part.mp4")])
    assert ei.value.code == "ffmpeg_failed"
    assert "missing.mp4" in ei.value.stderr_tail
    assert not list(tmp_path.iterdir())


# --- probe -----------------------------------------------------------------------------------------------------


def test_probe_video(sample_video):
    info = probe(sample_video)
    assert (info.width, info.height) == (64, 112)
    assert info.vcodec == "h264" and info.acodec == "aac" and info.has_audio
    assert info.fps == pytest.approx(30, abs=0.5)
    assert info.size == sample_video.stat().st_size


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


def test_register_dedupes_by_sha(session, tmp_path, sample_video):
    a = Path(shutil.copy(sample_video, tmp_path / "a.mp4"))
    b = Path(shutil.copy(sample_video, tmp_path / "b.MP4"))
    m1 = media.register(session, a, kind="video", origin="fake", meta={"prompt": "  giữ nguyên  "})
    m2 = media.register(session, b, kind="video", origin="fake")
    assert m1.id == m2.id
    assert not a.exists() and not b.exists()  # move=True: nguồn được chuyển / bản trùng bị bỏ
    rel = Path(m1.path)
    assert rel.parts[0] == "media" and rel.name == f"{m1.sha256}.mp4" and rel.parts[3] == m1.sha256[:2]
    stored = tmp_path / m1.path  # data dir của fixture `db` = thư mục chứa file DB
    assert stored.is_file() and stored.stat().st_size == m1.size
    assert (m1.mime, m1.width, m1.height) == ("video/mp4", 64, 112)
    assert 900 <= m1.duration_ms <= 1100
    assert m1.meta["prompt"] == "  giữ nguyên  " and m1.meta["probe"]["has_audio"] is True


def test_register_copy_keeps_source(session, tmp_path):
    src = _png(tmp_path / "keep.png", (64, 112))
    m = media.register(session, src, kind="image", origin="upload", project_id=3, move=False)
    assert src.exists() and (tmp_path / m.path).is_file()
    assert (m.mime, m.width, m.height, m.project_id, m.duration_ms) == ("image/png", 64, 112, 3, None)


def test_register_explicit_data_dir(session, tmp_path):
    data = tmp_path / "elsewhere"
    m = media.register(session, _png(tmp_path / "x.png", (8, 8)), kind="image", origin="upload", data_dir=data)
    assert (data / m.path).is_file()


def test_register_rejects_unknown_kind(session, tmp_path):
    with pytest.raises(ValueError):
        media.register(session, _png(tmp_path / "x.png", (8, 8)), kind="model", origin="upload")


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
