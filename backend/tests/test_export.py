"""Task F: export.check, export.create (job export.package), bìa, phụ đề, metadata, manifest, zip.

Module của task khác (`render.pipeline`, `render.layout`, `services.audio.{mix,subtitles}`) được thay bằng bản giả trong
`sys.modules` (fixture `fakes`); nối thật kiểm ở task e2e S.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import subprocess
import sys
import types
import zipfile
from types import SimpleNamespace

import pytest
from PIL import Image
from sagareel.core.errors import Forbidden
from sagareel.db.models import Clip, Render, Scene, Sequence, Sound, TimelineItem
from sagareel.media import probe
from sagareel.services.export import covers, package

BOX = {"x": 100, "y": 400, "w": 800, "h": 120}


def call(client, name: str, payload: dict | None = None):
    return client.post(f"/api/ops/{name}", json=payload or {})


def wait_job(client, job_id: int, timeout_s: int = 120) -> dict:
    body = call(client, "job.wait", {"ids": [job_id], "timeout_s": timeout_s}).json()
    assert not body["timed_out"], body
    return body["items"][0]


@dataclasses.dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str
    box: dict = dataclasses.field(default_factory=lambda: dict(BOX))


def _ts(ms: int, sep: str) -> str:
    s, ms = divmod(ms, 1000)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}{sep}{ms:03d}"


@pytest.fixture
def seq_id(owner_ctx, project) -> int:
    """Tập `ep1` (3 clip, 2 cảnh, tổng 1000 ms) + một nhạc chưa có giấy phép."""
    with owner_ctx.uow(op_name="test.create") as s:
        seq = Sequence(project_id=project, key="ep1", number=1, title="Tập một: Khởi đầu", synopsis="Cô bé gặp cáo.",
                       script="Ngày xưa…")
        s.add(seq)
        s.flush()
        sc1, sc2 = (Scene(sequence_id=seq.id, key=k, rank=r, title=t) for k, r, t in
                    (("s1", "a0", "Mở đầu"), ("s2", "a1", "Gặp cáo")))
        s.add_all([sc1, sc2])
        s.flush()
        for i, (sc, ms) in enumerate(((sc1, 400), (sc2, 300), (sc2, 300))):
            s.add(Clip(sequence_id=seq.id, scene_id=sc.id, rank=f"b{i}", key=f"c{i}", duration_ms=ms))
        snd = Sound(project_id=project, media_id=1, kind="music", title="Nhạc nền", source="tự soạn", license="")
        s.add(snd)
        s.flush()
        s.add(TimelineItem(sequence_id=seq.id, track="music", sound_id=snd.id))
        return seq.id


@pytest.fixture
def fakes(monkeypatch, owner_ctx, project, make_clip_tiny):
    """Thay `render.pipeline`, `render.layout`, `audio.mix`, `audio.subtitles` bằng bản giả; trả bộ ghi lệnh gọi."""
    state = SimpleNamespace(renders=[], loud={"integrated_lufs": -14.1, "true_peak_db": -1.2}, fail=False,
                            media_id=make_clip_tiny(owner_ctx.services.db, project))

    pipeline = types.ModuleType("sagareel.render.pipeline")

    async def render(sequence_id, variant="draft", platform=None, lang=None, preview_seconds=None):
        state.renders.append((sequence_id, variant, platform, lang))
        if state.fail:
            raise RuntimeError("giả lập hỏng ffmpeg")
        return state.media_id

    pipeline.render = render
    # collect_cues dựng lại timeline như lúc burn-in: `pipeline.load` + `pipeline.make_plan` (bản giả tối giản).
    pipeline.load = lambda services, sequence_id, variant, platform, lang: SimpleNamespace(
        lang=lang, options=SimpleNamespace(subtitles=True, subtitle_style={}))
    pipeline.make_plan = lambda loaded: SimpleNamespace(timeline=SimpleNamespace(dialogue=[
        SimpleNamespace(id=1, text="Xin chào", start_ms=0, end_ms=400, subtitle=True, src={}),
        SimpleNamespace(id=2, text="Tạm biệt", start_ms=600, end_ms=1000, subtitle=True, src={})]))

    layout = types.ModuleType("sagareel.render.layout")
    layout.compute = lambda sequence_id: SimpleNamespace(dialogue=[
        {"text": "Xin chào", "start_ms": 0, "dur_ms": 400}, {"text": "Tạm biệt", "start_ms": 600, "dur_ms": 400}])

    mix = types.ModuleType("sagareel.services.audio.mix")
    mix.measure_loudness = lambda path: state.loud

    subs = types.ModuleType("sagareel.services.audio.subtitles")
    subs.build_cues = lambda lines, style, w, h, zone: [Cue(x["start_ms"], x["end_ms"], x["text"]) for x in lines]
    subs.to_srt = lambda cues: "\n\n".join(f"{i}\n{_ts(c.start_ms, ',')} --> {_ts(c.end_ms, ',')}\n{c.text}"
                                           for i, c in enumerate(cues, 1)) + "\n"
    subs.to_vtt = lambda cues: "WEBVTT\n\n" + "\n\n".join(
        f"{_ts(c.start_ms, '.')} --> {_ts(c.end_ms, '.')}\n{c.text}" for c in cues) + "\n"

    for name, mod in (("sagareel.render.pipeline", pipeline), ("sagareel.render.layout", layout),
                      ("sagareel.services.audio.mix", mix), ("sagareel.services.audio.subtitles", subs)):
        monkeypatch.setitem(sys.modules, name, mod)
    return state


def fast(client, key: str, **patch) -> None:
    """Preset nền tảng nhanh cho test (`veryfast`) + tuỳ chọn sửa thêm khoá cấp cao."""
    got = call(client, "preset.get", {"kind": "platform", "key": key}).json()
    data = got["data"]
    data["encode"]["preset"] = "veryfast"
    data.update(patch)
    r = call(client, "preset.update", {"id": got["id"], "data": data})
    assert r.status_code == 200, r.text


def export(client, seq_id: int, platforms: list[str], **kw) -> dict:
    r = call(client, "export.create", {"sequence_ids": [seq_id], "platforms": platforms, **kw})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"])
    return {"job": job, **r.json()}


def outdir(settings, rel: str):
    return settings.outputs_dir / rel


# --- export.create: gói đầy đủ --------------------------------------------------------------------------------------


def test_package_contents_tiktok(client, settings, seq_id, fakes):
    fast(client, "tiktok")
    out = export(client, seq_id, ["tiktok"])
    assert out["job"]["status"] == "succeeded", out["job"]
    assert fakes.renders == [(seq_id, "final", "tiktok", "vi")]  # gọi pipeline.render với nền tảng + ngôn ngữ
    pkg = call(client, "export.get", {"id": out["export_ids"][0]}).json()
    assert pkg["status"] == "done" and pkg["folder"] == "exports/demo/ep1/tiktok-vi"
    folder = outdir(settings, pkg["folder"])
    names = {p.name for p in folder.iterdir()}
    assert {"video.mp4", "cover-1.jpg", "subtitles.srt", "subtitles.vtt", "metadata.json", "metadata.txt"} <= names
    assert not (folder.parent / "tiktok-vi.part").exists()

    info = probe.probe(folder / "video.mp4")
    assert (info.width, info.height) == (1080, 1920) and info.vcodec == "h264" and info.acodec == "aac"
    assert round(info.fps) == 30

    md = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert md["ai_generated"] is True and md["episode_label"] == "Tập 1" and md["lang"] == "vi"
    assert md["source"] == "template" and md["title"] == "Tập một: Khởi đầu"  # LLM chưa cấu hình → mẫu
    assert md["music_credits"] == ["Nhạc nền - nguồn: tự soạn"] and md["ai_label"]["required"] is True
    assert md["cover_timestamp_ms"] is not None  # TikTok: chọn khung qua timestamp
    assert md["hashtags"] and all(h.startswith("#") for h in md["hashtags"])
    txt = (folder / "metadata.txt").read_text(encoding="utf-8")
    assert "TIÊU ĐỀ" in txt and "Nhạc nền" in txt and "Nội dung do AI tạo" in txt
    srt = (folder / "subtitles.srt").read_text(encoding="utf-8")
    assert "Xin chào" in srt and "Tạm biệt" in srt and (folder / "subtitles.vtt").read_text(encoding="utf-8").startswith("WEBVTT")

    codes = [e["code"] for e in pkg["checks"]["errors"]]
    assert codes == ["music_license_missing"]  # nhạc chưa có giấy phép bị báo
    assert all(w["code"] != "loudness_off_target" for w in pkg["checks"]["warnings"])

    man = json.loads(outdir(settings, "exports/demo/_batches/" + out["batch"] + "/manifest.json").read_text(encoding="utf-8"))
    assert man["packages"][0]["folder"] == pkg["folder"] and man["packages"][0]["status"] == "done"
    rows = list(csv.DictReader(outdir(settings, f"exports/demo/_batches/{out['batch']}/manifest.csv")
                               .open(encoding="utf-8-sig", newline="")))
    assert rows[0]["platform"] == "tiktok" and rows[0]["errors"] == "1"


def test_covers_have_title_inside_safe_zone_and_size_limits(client, settings, seq_id, fakes):
    fast(client, "fb-reels")
    out = export(client, seq_id, ["fb-reels"])
    folder = outdir(settings, "exports/demo/ep1/fb-reels-vi")
    assert out["job"]["status"] == "succeeded"
    covs = sorted(folder.glob("cover-*.jpg"))
    assert 1 <= len(covs) <= 3
    for c in covs:
        im = Image.open(c)
        assert im.size == (1080, 1920) and c.stat().st_size <= 10 * 1024 * 1024
    # Tiêu đề vẽ trong vùng an toàn: dải trên 269 px và dải dưới 672 px không bị đổi so với khung gốc (không chữ).
    md = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert [c["file"] for c in md["covers"]] == [c.name for c in covs]


def test_draw_title_stays_inside_safe_zone():
    img = Image.new("RGB", (1080, 1920), (40, 90, 160))
    zone = {"top": 269, "bottom": 672, "left": 65, "right": 65}
    covers.draw_title(img, "Cô bé quàng khăn đỏ và con cáo gian xảo", zone)  # >5 từ: chỉ 5 từ đầu
    bg = Image.new("RGB", (1080, 1920), (40, 90, 160))
    from PIL import ImageChops

    box = ImageChops.difference(img, bg).getbbox()
    assert box is not None
    assert box[1] >= zone["top"] - 2 and box[3] <= 1920 - zone["bottom"] + 2
    assert box[0] >= zone["left"] - 2 and box[2] <= 1080 - zone["right"] + 2


def test_save_jpeg_respects_max_mb(tmp_path):
    import os

    img = Image.frombytes("RGB", (1080, 1920), os.urandom(1080 * 1920 * 3))  # nhiễu: JPEG rất nặng
    out = tmp_path / "c.jpg"
    covers.save_jpeg(img, out, 2.0)
    assert out.stat().st_size <= 2 * 1024 * 1024


# --- Đổi tỉ lệ, chia phần -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["blur_pad", "crop"])
def test_reframe_to_landscape(client, settings, seq_id, fakes, mode):
    fast(client, "yt-long")
    out = export(client, seq_id, ["yt-long"], options={"reframe": mode, "focus_x": 0.3, "focus_y": 0.2})
    assert out["job"]["status"] == "succeeded", out["job"]
    info = probe.probe(outdir(settings, "exports/demo/ep1/yt-long-vi/video.mp4"))
    assert (info.width, info.height) == (1920, 1080)
    # video dọc 9:16 trên khung 16:9 + hộp phụ đề: YouTube dài không có vùng an toàn → không cảnh báo
    pkg = call(client, "export.get", {"id": out["export_ids"][0]}).json()
    assert all(w["code"] != "subtitle_outside_safe_zone" for w in pkg["checks"]["warnings"])


def test_split_into_parts(client, settings, seq_id, fakes):
    fast(client, "fb-reels", duration_s={"min": 0.1, "max": 0.5, "recommended": [0.1, 0.4]})
    out = export(client, seq_id, ["fb-reels"])
    assert out["job"]["status"] == "succeeded", out["job"]
    folder = outdir(settings, "exports/demo/ep1/fb-reels-vi")
    parts = sorted(folder.glob("video-part*.mp4"))
    assert [p.name for p in parts] == ["video-part1.mp4", "video-part2.mp4"] and not (folder / "video.mp4").exists()
    for p in parts:
        info = probe.probe(p)
        assert (info.width, info.height) == (1080, 1920) and 400 <= info.duration_ms <= 600
    md = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert [p["title"] for p in md["parts"]] == ["Tập một: Khởi đầu (Phần 1/2)", "Tập một: Khởi đầu (Phần 2/2)"]
    assert md["parts"][1]["episode_label"] == "Tập 1 / Phần 2" and md["parts"][0]["file"] == "video-part1.mp4"
    s1 = (folder / "subtitles-part1.srt").read_text(encoding="utf-8")
    s2 = (folder / "subtitles-part2.srt").read_text(encoding="utf-8")
    assert "Xin chào" in s1 and "Tạm biệt" not in s1
    assert "Tạm biệt" in s2 and "00:00:00,100 --> 00:00:00,500" in s2  # dời về gốc phần 2 (600→100 ms)


def test_over_max_without_split_is_reported(client, settings, seq_id, fakes):
    fast(client, "fb-reels", duration_s={"min": 0.1, "max": 0.5, "recommended": [0.1, 0.4]})
    out = export(client, seq_id, ["fb-reels"], options={"split_parts": False})
    pkg = call(client, "export.get", {"id": out["export_ids"][0]}).json()
    assert "duration_over_max" in [e["code"] for e in pkg["checks"]["errors"]]
    assert (outdir(settings, pkg["folder"]) / "video.mp4").is_file()


# --- Metadata qua LLM ------------------------------------------------------------------------------------------------


def test_metadata_from_llm_caption_task(client, settings, seq_id, fakes):
    reply = json.dumps({"title": "Tiêu đề do AI", "caption": "Mô tả do AI viết.", "hashtags": ["phim ngắn", "cổ tích"]},
                       ensure_ascii=False)
    r = call(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                         "options": {"replies": {"caption": reply}}})
    assert r.status_code == 200, r.text
    fast(client, "yt-shorts")
    out = export(client, seq_id, ["yt-shorts"], options={"covers": 1})
    folder = outdir(settings, "exports/demo/ep1/yt-shorts-vi")
    md = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert md["source"] == "llm" and md["title"] == "Tiêu đề do AI"
    assert md["hashtags"] == ["#PhimNgắn", "#CổTích"] and md["description"] == "Mô tả do AI viết."
    assert len(list(folder.glob("cover-*.jpg"))) == 1 and out["job"]["status"] == "succeeded"


def test_llm_garbage_falls_back_to_template(client, settings, seq_id, fakes):
    call(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                     "options": {"replies": {"caption": "{\"title\": 5}"}}})
    fast(client, "yt-shorts")
    export(client, seq_id, ["yt-shorts"], options={"covers": 1})
    md = json.loads((outdir(settings, "exports/demo/ep1/yt-shorts-vi") / "metadata.json").read_text(encoding="utf-8"))
    assert md["source"] == "template"


# --- Lô, lỗi, nối lại, supersede ------------------------------------------------------------------------------------


def test_batch_platform_x_lang_and_supersede(client, settings, seq_id, fakes):
    fast(client, "tiktok")
    fast(client, "fb-reels")
    out = export(client, seq_id, ["tiktok", "fb-reels"], langs=["vi", "en"], options={"covers": 1})
    assert out["job"]["status"] == "succeeded" and len(out["export_ids"]) == 4
    assert {(r[2], r[3]) for r in fakes.renders} == {("tiktok", "vi"), ("tiktok", "en"), ("fb-reels", "vi"), ("fb-reels", "en")}
    for sub in ("tiktok-vi", "tiktok-en", "fb-reels-vi", "fb-reels-en"):
        assert (outdir(settings, "exports/demo/ep1") / sub / "video.mp4").is_file()
    again = export(client, seq_id, ["tiktok"], langs=["vi"], options={"covers": 1})
    old = call(client, "export.get", {"id": out["export_ids"][0]}).json()
    new = call(client, "export.get", {"id": again["export_ids"][0]}).json()
    assert old["status"] == "superseded" and new["status"] == "done"
    listed = call(client, "export.list", {"project_id": new["project_id"]}).json()
    assert [r["id"] for r in listed].count(old["id"]) == 0 and len(listed) == 4
    assert not list(outdir(settings, "exports/demo/ep1").glob("*.part"))  # thư mục tạm đã đổi tên


def test_failed_render_marks_package_and_keeps_old_content(client, settings, seq_id, fakes):
    fast(client, "tiktok")
    first = export(client, seq_id, ["tiktok"], options={"covers": 1})
    folder = outdir(settings, "exports/demo/ep1/tiktok-vi")
    before = (folder / "video.mp4").read_bytes()
    fakes.fail = True
    out = export(client, seq_id, ["tiktok"], options={"covers": 1})
    assert out["job"]["status"] == "failed" and out["job"]["error_code"] == "export_failed"
    pkg = call(client, "export.get", {"id": out["export_ids"][0]}).json()
    assert pkg["status"] == "failed" and "giả lập hỏng ffmpeg" in pkg["metadata"]["error"]
    assert (folder / "video.mp4").read_bytes() == before and first["job"]["status"] == "succeeded"
    assert not list(folder.parent.glob("*.part"))


async def test_resume_skips_done_packages(services, owner_ctx, project, seq_id, fakes):
    class Run:
        progress_calls: list = []

        def progress(self, pct=None, stage=None, eta_s=None):
            self.progress_calls.append((pct, stage))

        def log(self, level, msg):
            pass

    project_info, units = package.load_targets(services, [seq_id], ["tiktok"], ["vi"])
    ids = package.create_rows(services, project_info, units, package.ExportOptions(covers=1), "batchx")
    first = await package.run_batch(services, Run(), ids, "batchx")
    assert first["failed"] == 0 and len(fakes.renders) == 1
    again = await package.run_batch(services, Run(), ids, "batchx")  # chạy lại sau khi ngắt: gói đã xong → bỏ qua
    assert len(fakes.renders) == 1 and again["packages"][0]["status"] == "done"
    assert Run.progress_calls and Run.progress_calls[-1][0] <= 100


# --- dry_run, check, zip ---------------------------------------------------------------------------------------------


def test_create_dry_run_lists_packages_without_side_effects(client, settings, seq_id, fakes):
    r = call(client, "export.create", {"sequence_ids": [seq_id], "platforms": ["tiktok", "yt-long"], "dry_run": True})
    body = r.json()
    assert r.status_code == 200 and body["dry_run"] is True and len(body["packages"]) == 2
    assert body["packages"][0]["folder"] == "exports/demo/ep1/tiktok-vi" and "errors" in body["packages"][0]
    assert call(client, "export.list", {}).json() == [] and fakes.renders == []
    assert not (settings.outputs_dir / "exports").exists()


def test_create_validations(client, seq_id, fakes):
    assert call(client, "export.create", {"sequence_ids": [seq_id], "platforms": ["myspace"]}).json()["error_code"] == "unknown_platform"
    assert call(client, "export.create", {"sequence_ids": [seq_id], "platforms": []}).json()["error_code"] == "export_no_platforms"
    assert call(client, "export.create", {"sequence_ids": [999], "platforms": ["tiktok"]}).status_code == 404
    assert call(client, "export.create", {"sequence_ids": [], "platforms": ["tiktok"]}).status_code == 422


def test_create_defaults_to_sequence_target_platforms(client, owner_ctx, seq_id, fakes):
    with owner_ctx.uow(op_name="test.patch") as s:
        s.get(Sequence, seq_id).target_platforms = ["tiktok"]
    body = call(client, "export.create", {"sequence_ids": [seq_id], "dry_run": True}).json()
    assert [p["platform"] for p in body["packages"]] == ["tiktok"]


def test_check_without_render_estimates(client, seq_id, fakes):
    rep = call(client, "export.check", {"sequence_id": seq_id, "platform": "fb-reels"}).json()
    codes = [w["code"] for w in rep["warnings"]]
    assert codes[0] == "no_final_render" and rep["info"]["estimated"] is True and rep["info"]["duration_ms"] == 1000
    assert [e["code"] for e in rep["errors"]] == ["duration_under_min", "music_license_missing"]  # 1 s < tối thiểu 3 s
    assert "loudness_not_measured" not in codes  # chưa có bản dựng → không giả vờ đo


def test_check_with_render_measures_loudness_and_boxes(client, owner_ctx, seq_id, fakes):
    with owner_ctx.services.db.session() as s:
        s.add(Render(sequence_id=seq_id, variant="final", platform="fb-reels", lang="vi", status="done",
                     media_id=fakes.media_id, duration_ms=1000))
    fakes.loud = {"integrated_lufs": -20.5, "true_peak_db": -3.0}
    low = {"x": 100, "y": 1500, "w": 800, "h": 120, "start_ms": 2000}
    rep = call(client, "export.check", {"sequence_id": seq_id, "platform": "fb-reels", "cue_boxes": [low, BOX]}).json()
    w = [x["code"] for x in rep["warnings"]]
    assert "loudness_off_target" in w and "subtitle_outside_safe_zone" in w and "no_final_render" not in w
    assert rep["info"]["estimated"] is False and rep["info"]["size"] > 0
    assert "metadata" not in rep  # kiểm tra không rò dữ liệu thừa
    bad = call(client, "export.check", {"sequence_id": seq_id, "platform": "yt-shorts",
                                        "metadata": {"title": "x" * 150, "hashtags": ["#a"] * 70}}).json()
    assert {"title_too_long", "hashtags_too_many"} <= {e["code"] for e in bad["errors"]}


def test_check_unknown_platform_and_sequence(client, seq_id, fakes):
    assert call(client, "export.check", {"sequence_id": seq_id, "platform": "x"}).json()["error_code"] == "unknown_platform"
    assert call(client, "export.check", {"sequence_id": 9999, "platform": "tiktok"}).status_code == 404


def test_zip_stream_and_open_folder(client, settings, seq_id, fakes):
    fast(client, "tiktok")
    out = export(client, seq_id, ["tiktok"], options={"covers": 1})
    eid = out["export_ids"][0]
    r = client.get(f"/api/export/{eid}/zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith("attachment")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.testzip() is None
    names = set(zf.namelist())
    assert {"video.mp4", "cover-1.jpg", "metadata.json", "metadata.txt", "subtitles.srt"} <= names
    folder = outdir(settings, "exports/demo/ep1/tiktok-vi")
    assert zf.read("video.mp4") == (folder / "video.mp4").read_bytes()
    assert json.loads(zf.read("metadata.json"))["lang"] == "vi"
    op = call(client, "export.open_folder", {"id": eid}).json()
    assert op["path"] == str(folder.resolve()) and op["zip_url"] == f"/api/export/{eid}/zip"
    assert client.get("/api/export/9999/zip").status_code == 404


def test_zip_not_ready_for_pending(client, owner_ctx, project, seq_id, fakes):
    info, units = package.load_targets(owner_ctx.services, [seq_id], ["tiktok"], None)
    eid = package.create_rows(owner_ctx.services, info, units, package.ExportOptions(), "b")[0]
    r = client.get(f"/api/export/{eid}/zip")
    assert r.status_code == 409 and r.json()["error_code"] == "export_not_ready"
    assert call(client, "export.open_folder", {"id": eid}).json()["error_code"] == "export_folder_missing"


@pytest.mark.parametrize("principal", [None, SimpleNamespace(role="viewer")])
async def test_zip_forbidden_for_non_owner(principal):
    """Spec §23.2: người xem không tải file gốc → zip chỉ cho chủ; không đọc DB trước khi từ chối."""
    from sagareel.api.routes_export import export_zip

    request = SimpleNamespace(state=SimpleNamespace(principal=principal), app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(Forbidden):
        await export_zip(request, 1)


def test_iter_zip_streams_in_chunks(tmp_path):
    (tmp_path / "a.txt").write_text("xin chào " * 1000, encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(bytes(range(256)) * 4096)
    chunks = list(package.iter_zip(tmp_path, chunk=4096))
    assert len(chunks) > 3  # stream từng đợt, không gom cả file
    zf = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
    assert zf.read("sub/b.bin") == bytes(range(256)) * 4096 and zf.read("a.txt").decode("utf-8").startswith("xin chào")


# --- Chọn khung bìa + khớp hợp đồng ----------------------------------------------------------------------------------


def test_cover_frame_ranking_prefers_sharp_stable_frames():
    flat = Image.new("RGB", (96, 170), (10, 10, 10))
    busy = Image.effect_noise((96, 170), 60).convert("RGB")
    cut = Image.new("RGB", (96, 170), (250, 250, 250))
    ranked = covers.rank_samples([flat, busy, cut, busy], [0.0, 1.0, 2.0, 3.0], 4.0)
    assert ranked[0][1] in (1.0, 3.0) and all(t != 2.0 for _, t in ranked[:2])  # khung ngay sau cú cắt gắt bị loại
    assert covers.pick_times(ranked, 2, 4.0, marks_ms=[500]) [0] == 500
    assert len(covers.pick_times(ranked, 3, 4.0)) <= 3


def test_ffprobe_available_for_tests():
    assert subprocess.run(["ffprobe", "-version"], capture_output=True).returncode == 0


def test_package_folder_rejects_traversal(services):
    import pytest as _pytest
    from sagareel.core.errors import AppError
    from sagareel.services.export import package

    for bad in ["../x", "..", "a/b", "a\b", ""]:
        with _pytest.raises(AppError):
            package.package_folder(services, "demo", "ep1", "tiktok", bad or "/")
    assert package.package_folder(services, "demo", "ep1", "tiktok", "vi").name == "tiktok-vi"


def test_cue_box_real_subtitles_respects_safe_zone():
    """Hộp chữ tính từ Cue thật của D (không có `box`): nằm trong vùng an toàn khi build_cues nâng lề; style đặt
    lề quá thấp mà không nâng → bị báo."""
    from sagareel.services.audio import subtitles as subs
    from sagareel.services.export import checks, package, platforms

    spec = {"safe_zone_px": {"top": 160, "bottom": 480, "left": 60, "right": 140},
            "safe_zone_ref": {"width": 1080, "height": 1920}}
    st = subs.load_style("9x16")
    lines = [{"text": "Con chào bà ạ, con về rồi.", "start_ms": 0, "end_ms": 1500}]
    cues = subs.build_cues(lines, st, 1080, 1920, platforms.safe_zone(spec, 1080, 1920))
    boxes = [package.cue_box(subs, st, c, 1080, 1920) for c in cues]
    assert boxes and all(b["y"] + b["h"] <= 1920 - 480 and b["x"] >= 60 for b in boxes)
    assert checks.check_subtitle_boxes(spec, boxes, (1080, 1920)) == []
    low = subs.load_style("9x16", overrides={"margin_v": 40})
    bad = [package.cue_box(subs, low, c, 1080, 1920) for c in subs.build_cues(lines, low, 1080, 1920, None)]
    assert checks.check_subtitle_boxes(spec, bad, (1080, 1920))[0]["code"] == "subtitle_outside_safe_zone"
