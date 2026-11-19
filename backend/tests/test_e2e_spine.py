"""Task S — xương sống end-to-end trên module THẬT của W1 (A compiler, B clip/take/tạo lại, D âm thanh, E dựng, F xuất).

Chỉ adapter provider là giả (`fake_image`, `fake_video`, `fake_tts`); ffmpeg/libass/ebur128 chạy thật trên media tí hon.
Không monkeypatch nội bộ A/B/D/E/F: mọi bước đi qua op (REST cục bộ) + worker thật của app.

project → asset + look + sheet → tập 3 clip (ai_video, still_motion, link=continue) + 2 câu thoại → clip.regenerate
part=all (ảnh → video/still_motion → continue từ khung cuối, + TTS qua audio.generate) → render animatic + final
(tiktok) → export tiktok (bản cuối dùng lại cache) → kiểm file, thời lượng ≈ layout, phụ đề burn-in, loudness, metadata.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageChops
from sagareel.db.models import Job, Render, Take
from sagareel.services.audio import mix
from sqlalchemy import select

FAKE_IMG = {"kind": "image", "adapter": "fake_image", "name": "fake-img", "options": {"short_side": 96}}
FAKE_VID = {"kind": "video", "adapter": "fake_video", "name": "fake-vid", "options": {"short_side": 96}}
FAKE_TTS = {"kind": "tts", "adapter": "fake_tts", "name": "fake-tts"}
LINES = ("Con chào bà ạ, con về rồi.", "Vào nhà đi con, trời sắp mưa.")


def ok(client, name: str, payload: dict | None = None) -> dict | list:
    r = client.post(f"/api/ops/{name}", json=payload or {})
    assert r.status_code == 200, f"{name}: {r.text}"
    return r.json()


def wait(client, ids: list[int], timeout_s: int = 300) -> list[dict]:
    body = ok(client, "job.wait", {"ids": ids, "timeout_s": timeout_s})
    assert not body["timed_out"], body
    return body["items"]


def drain(client, timeout_s: float = 300) -> None:
    """Chờ tới khi không còn job queued/running (kể cả job con được đưa vào hàng đợi sau khi job cha xong)."""
    db = client.app.state.services.db
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with db.session() as s:
            pending = list(s.scalars(select(Job.id).where(Job.status.in_(("queued", "running")))))
        if not pending:
            return
        wait(client, pending[:1], timeout_s=int(deadline - time.monotonic()) + 1)
    raise AssertionError("job không kết thúc")


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                          str(path)], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return json.loads(out.stdout)


def frame(path: Path, at_s: float, out: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at_s:.3f}", "-i",
                    str(path), "-frames:v", "1", str(out)], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return Image.open(out).convert("L")


def band_diff(a: Image.Image, b: Image.Image, y0: float, y1: float, threshold: int = 96) -> float:
    """Tỉ lệ điểm ảnh lệch mạnh (> `threshold`/255) giữa hai khung trong dải chiều cao [y0, y1) (tỉ lệ khung).
    Chữ trắng viền đen tạo lệch lớn; nhiễu mã hoá giữa hai lần dựng chỉ lệch vài mức xám."""
    w, h = a.size
    box = (0, int(h * y0), w, int(h * y1))
    hist = ImageChops.difference(a.crop(box), b.crop(box)).histogram()
    return sum(hist[threshold:]) / max(1, sum(hist))


def test_spine_project_to_tiktok_package(client, settings):
    started = time.perf_counter()
    svc = client.app.state.services

    # --- Kết nối giả + cài đặt -----------------------------------------------------------------------------------
    for spec in (FAKE_IMG, FAKE_VID, FAKE_TTS):
        ok(client, "provider.create", spec)
    ok(client, "settings.set_global", {"values": {"tts.connection": "fake-tts", "tts.voice": "fake-vi-female"}})
    tiktok = ok(client, "preset.get", {"kind": "platform", "key": "tiktok"})
    data = tiktok["data"]
    data["encode"]["preset"] = "veryfast"  # cấu hình (không phải monkeypatch): mã hoá nhanh cho test
    ok(client, "preset.update", {"id": tiktok["id"], "data": data})

    # --- Project + Library -----------------------------------------------------------------------------------------
    proj = ok(client, "project.create", {"name": "Truyện bà cháu", "key": "baochau", "aspect": "9:16",
                                         "target_platforms": ["tiktok"]})
    pid = proj["id"]
    ok(client, "project.update", {"id": pid, "values": {"resolution": "240p"}})  # dựng nhỏ cho nhanh
    asset = ok(client, "asset.create", {"project_id": pid, "kind": "character", "name": "Bé An",
                                        "description": "Bé gái 8 tuổi, tóc ngắn, áo vàng"})
    look_id = asset["looks"][0]["id"]
    sheet = ok(client, "sheet.generate", {"look_id": look_id, "views": ["anchor", "closeup"],
                                          "connection": "fake-img"})
    assert sheet.get("job_ids") or sheet.get("jobs") or sheet
    drain(client)
    sheets = ok(client, "sheet.list", {"look_id": look_id})
    look = ok(client, "asset.get", {"id": asset["id"]})["looks"][0]
    anchor = look["anchor_media_id"]
    assert anchor and (look.get("sheet") or {}).get("closeup"), (look, sheets)

    # --- Tập: 3 clip + 2 câu thoại ---------------------------------------------------------------------------------
    seq = ok(client, "sequence.create", {"project_id": pid, "title": "Tập 1: Về quê"})
    sid = seq["id"]
    who = [{"asset_id": asset["id"]}]
    c1 = ok(client, "clip.create", {"sequence_id": sid, "fields": {
        "image_prompt": "Bé An đứng trước cổng nhà bà", "motion_prompt": "Bé An vẫy tay", "engine": "ai_video",
        "duration_ms": 2500, "assets": who, "shot_size": "ms"}})
    c2 = ok(client, "clip.create", {"sequence_id": sid, "fields": {
        "image_prompt": "Sân nhà bà buổi chiều", "engine": "still_motion", "motion_preset": "slow_zoom_in",
        "duration_ms": 2000, "assets": who}})
    c3 = ok(client, "clip.create", {"sequence_id": sid, "fields": {
        "motion_prompt": "Bé An chạy vào nhà", "engine": "ai_video", "link": "continue", "duration_ms": 2000,
        "assets": who}})
    l1 = ok(client, "dialogue.create", {"clip_id": c1["id"], "text": LINES[0]})
    l2 = ok(client, "dialogue.create", {"clip_id": c2["id"], "text": LINES[1]})

    # --- Tạo ảnh/video/âm thanh (B → A/E/D) -------------------------------------------------------------------------
    ids = [c1["id"], c2["id"], c3["id"]]
    pre = ok(client, "sequence.estimate", {"id": sid, "overrides": {"connection": "fake-img",
                                                                    "video_connection": "fake-vid"}})
    assert pre["generation"]["clip_ids"] == ids and pre["render"]["clips"] == 3, pre
    assert pre["generation"]["estimate"]["parts"]["image"]["jobs"] == 2  # clip continue không cần ảnh
    gen = ok(client, "clip.regenerate", {"ids": ids, "part": "all",
                                         "overrides": {"connection": "fake-img", "video_connection": "fake-vid"}})
    assert gen["estimate"]["parts"]["audio"]["lines"] == 2 and not gen["skipped"], gen
    drain(client)
    with svc.db.session() as s:
        rows = {t.id: t for t in s.scalars(select(Take).where(Take.id.in_(gen["take_ids"])))}
        assert all(t.status == "succeeded" for t in rows.values()), {t.id: (t.status, t.inputs) for t in rows.values()}
        by_clip = {(t.owner_id, t.part): t for t in rows.values()}
        img1 = by_clip[(c1["id"], "image")]
        assert anchor in img1.inputs["ref_media_ids"]  # A: ref mỏ neo của look
        assert "Bé gái 8 tuổi" in img1.inputs["prompt"]  # A: mô tả khoá chèn nguyên văn
        assert (c3["id"], "image") not in by_clip  # continue: không tạo ảnh
        vid3 = by_clip[(c3["id"], "video")]
        assert vid3.inputs["image_source"].startswith("last_frame")  # khung cuối của clip trước
        assert by_clip[(c2["id"], "video")].inputs["engine"] == "still_motion"  # E: still_motion thật
    lines = ok(client, "dialogue.list", {"sequence_id": sid})
    lines = lines["items"] if isinstance(lines, dict) else lines
    assert all(x["audio_take_id"] for x in lines) and len(lines) == 2

    # --- Dựng animatic + bản cuối ----------------------------------------------------------------------------------
    est = ok(client, "render.estimate", {"sequence_id": sid, "variant": "final", "platform": "tiktok"})
    assert est
    wait(client, [ok(client, "render.start", {"sequence_id": sid, "variant": "animatic"})["id"]])
    wait(client, [ok(client, "render.start", {"sequence_id": sid, "variant": "final", "platform": "tiktok"})["id"]])
    renders = ok(client, "render.list", {"sequence_id": sid})["items"]
    assert {r["variant"]: r["status"] for r in renders} == {"animatic": "succeeded", "final": "succeeded"}, renders
    final = next(r for r in renders if r["variant"] == "final")
    timeline_ms = final["meta"]["timeline_ms"]
    # Nối cut/continue (không chồng): layout = tổng độ dài video đang chọn của các clip (ai_video dài theo provider:
    # 6 s; still_motion = duration_ms của clip), làm tròn khung.
    clips_now = ok(client, "clip.list", {"sequence_id": sid})["items"]
    with svc.db.session() as s:
        from sagareel.db.models import Media

        src_ms = [s.get(Media, s.get(Take, c["video_take_id"]).media_id).duration_ms for c in clips_now]
    assert abs(timeline_ms - sum(src_ms)) <= 34 * len(src_ms), (timeline_ms, src_ms)

    # --- Xuất TikTok -----------------------------------------------------------------------------------------------
    exp = ok(client, "export.create", {"sequence_ids": [sid], "platforms": ["tiktok"]})
    wait(client, [exp["job_id"]], timeout_s=600)
    pkg = ok(client, "export.get", {"id": exp["export_ids"][0]})
    assert pkg["status"] == "done", pkg
    with svc.db.session() as s:  # bản cuối được dùng lại từ cache (không dựng lại)
        assert len(s.scalars(select(Render).where(Render.sequence_id == sid, Render.variant == "final")).all()) == 1
    folder = settings.outputs_dir / pkg["folder"]
    names = {p.name for p in folder.iterdir()}
    assert {"video.mp4", "subtitles.srt", "subtitles.vtt", "metadata.json", "metadata.txt"} <= names, names

    video = folder / "video.mp4"
    info = ffprobe(video)
    v = next(x for x in info["streams"] if x["codec_type"] == "video")
    assert (int(v["width"]), int(v["height"])) == (1080, 1920) and v["codec_name"] == "h264"
    assert any(x["codec_type"] == "audio" for x in info["streams"])
    assert abs(float(info["format"]["duration"]) * 1000 - timeline_ms) <= 150  # ≈ layout

    loud = mix.measure_loudness(video)
    assert -15.0 <= loud["integrated_lufs"] <= -13.0, loud

    md = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert md["ai_generated"] is True
    srt = (folder / "subtitles.srt").read_text(encoding="utf-8")
    for text in LINES:
        assert text.split(",")[0].split()[0] in srt
    assert not (pkg.get("checks") or {}).get("errors"), pkg.get("checks")

    # --- Phụ đề burn-in: so với bản cuối không phụ đề ----------------------------------------------------------------
    for line in (l1, l2):
        ok(client, "dialogue.update", {"id": line["id"], "values": {"subtitle": False}})
    wait(client, [ok(client, "render.start", {"sequence_id": sid, "variant": "final", "platform": "tiktok"})["id"]])
    with svc.db.session() as s:
        rows = s.scalars(select(Render).where(Render.sequence_id == sid, Render.variant == "final")
                         .order_by(Render.id)).all()
        assert len(rows) == 2 and all(r.status == "succeeded" for r in rows)
        from sagareel.services import media as media_svc

        with_subs, without = (media_svc.file_path(settings, media_svc.get(s, r.media_id)) for r in rows)
    a = frame(with_subs, 0.6, settings.data_dir / "f_subs.png")
    b = frame(without, 0.6, settings.data_dir / "f_plain.png")
    sub_band = band_diff(a, b, 0.45, 0.75)  # phụ đề: mép dưới ≈ 1920 − 700 (MarginV) của khung
    top_band = band_diff(a, b, 0.0, 0.3)
    assert sub_band > 0.005 and sub_band > 20 * top_band, (sub_band, top_band)

    elapsed = time.perf_counter() - started
    print(f"\n[e2e spine] {elapsed:.1f} s")
