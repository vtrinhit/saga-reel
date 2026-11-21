"""Task K (M6): phân tích kênh mẫu + clone từ video — ffmpeg thật trên video tí hon (3 đoạn màu = 3 cảnh), LLM giả."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from sagareel.db.models import Clip, DialogueLine, Media, Preset, ProviderConnection
from sagareel.services import media
from sagareel.services.channel import clone
from sagareel.services.channel import scenes as sc

FF = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]


def make_three_colors(path: Path, seg: float = 2.0, colors=("red", "blue", "green")) -> Path:
    args = list(FF)
    for c in colors:
        args += ["-f", "lavfi", "-i", f"color=c={c}:s=96x160:d={seg}:r=15"]
    n = len(colors)
    args += ["-filter_complex", f"concat=n={n}:v=1:a=0,format=yuv420p", "-c:v", "libx264", str(path)]
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return path


def call(client, name, payload=None):
    return client.post(f"/api/ops/{name}", json=payload or {})


def ok(client, name, payload=None):
    r = call(client, name, payload)
    assert r.status_code == 200, r.text
    return r.json()


def wait(client, job_id):
    body = ok(client, "job.wait", {"ids": [job_id], "timeout_s": 90})
    assert not body["timed_out"], body
    return body["items"][0]


@pytest.fixture
def svc(client):
    return client.app.state.services


def _upload(svc, path: Path, project=None) -> int:
    with svc.db.session() as s:
        return media.register(s, path, kind="video", origin="upload", settings=svc.settings, project_id=project).id


def _llm(svc, replies: dict) -> None:
    with svc.db.session() as s:
        row = s.scalars(select(ProviderConnection).where(ProviderConnection.name == "fake-llm")).first()
        if row is None:
            s.add(ProviderConnection(kind="llm", adapter="fake_llm", name="fake-llm", concurrency=2,
                                     options={"replies": replies}))
        else:
            row.options = {"replies": replies}


ANALYSIS = {
    "style": {"palette": ["warm amber", "teal"], "lighting": "low-key", "lens": "35mm",
              "look_words": ["film grain", "teal and orange"], "rendering": "cinematic"},
    "pacing": {"feel": "fast", "notes": "cắt nhanh"},
    "hook": {"pattern": "shock", "description": "Mở bằng cận cảnh bất ngờ"},
    "structure": {"beats": ["hook", "setup", "twist"], "formula": "hook-twist"},
    "subtitles": {"present": True, "style": "chữ trắng viền đen", "position": "bottom"},
    "audio": {"music": "lo-fi", "voice": "kể chuyện", "sfx": "whoosh"},
    "genre_suggestion": {"transition": "cut", "end": "loop", "dialogue_style": "narration"},
    "style_block": "cinematic teal and orange, soft film grain",
    "negative_block": "text, watermark",
    "characters": [{"name": "Cô gái áo đỏ", "description": "Tóc đen dài"}],
    "locations": [{"name": "Hẻm đêm", "description": "Đèn neon"}],
}


# --- scenes ----------------------------------------------------------------------------------------------------------


def test_detect_three_scenes_and_min_scene(tmp_path):
    v = make_three_colors(tmp_path / "v.mp4")
    cuts = asyncio.run(sc.detect_cuts(v, 0.3))
    assert len(cuts) == 2 and abs(cuts[0] - 2.0) < 0.2 and abs(cuts[1] - 4.0) < 0.2
    scenes = sc.build_scenes(cuts, 6.0, 1.0)
    assert len(scenes) == 3 and abs(scenes[1].duration_s - 2.0) < 0.2
    assert len(sc.build_scenes(cuts, 6.0, 3.0)) == 1  # cảnh ngắn hơn min bị gộp
    assert len(sc.build_scenes([], 6.0)) == 1
    assert len(sc.build_scenes([5.8], 6.0, 1.0)) == 1  # cảnh cuối quá ngắn gộp lại


def test_pick_times_and_contact_sheet(tmp_path):
    scenes = [sc.Scene(i * 2.0, i * 2.0 + 2.0) for i in range(30)]
    times = sc.pick_times(60.0, scenes, 99)
    assert len(times) <= 16 and times == sorted(times)
    assert sum(1 for t in times if t < 3.0) == 3  # 3 khung hook
    v = make_three_colors(tmp_path / "v.mp4")
    frames = [asyncio.run(sc.extract_frame(v, t, tmp_path / f"f{i}.jpg")) for i, t in enumerate((0.5, 2.5, 4.5))]
    sheet = sc.contact_sheet(frames, tmp_path / "sheet.jpg", cols=2)
    from PIL import Image

    with Image.open(sheet) as im:
        assert im.width == 640 and im.height >= 2 * 300


def test_engine_duration_and_transcript_split():
    assert clone.engine_and_duration(5.0) == ("ai_video", 6000)
    assert clone.engine_and_duration(9.0) == ("ai_video", 10000)
    assert clone.engine_and_duration(14.0) == ("ai_video", 15000)
    assert clone.engine_and_duration(2.0) == ("still_motion", 2000)
    assert clone.engine_and_duration(30.0) == ("still_motion", 30000)
    assert clone.engine_and_duration(2.0, "ai_video") == ("ai_video", 6000)
    scenes = [sc.Scene(0, 2), sc.Scene(2, 4), sc.Scene(4, 6)]
    parts = clone.split_transcript("Một câu. Hai câu. Ba câu.", scenes)
    assert parts == ["Một câu.", "Hai câu.", "Ba câu."]
    assert clone.split_transcript("", scenes) == ["", "", ""]
    assert clone.split_transcript("Chỉ một câu thôi.", scenes).count("") == 2


# --- study + analyze + apply ------------------------------------------------------------------------------------------


def test_study_crud_validation_and_delete(client, svc, tmp_path, make_clip_tiny):
    vid = _upload(svc, make_three_colors(tmp_path / "a.mp4"))
    r = call(client, "channel.study_create", {"title": "K", "media_ids": []})
    assert r.status_code == 422 and r.json()["error_code"] == "study_media_required"
    r = call(client, "channel.study_create", {"title": "K", "media_ids": [999999]})
    assert r.status_code == 404
    st = ok(client, "channel.study_create", {"title": "Kênh A", "media_ids": [vid, vid], "captions": "tiêu đề"})
    assert st["media_ids"] == [vid] and st["status"] == "pending" and st["source"] == "upload"
    assert ok(client, "channel.study_get", {"id": st["id"]})["title"] == "Kênh A"
    assert [x["id"] for x in ok(client, "channel.study_list")["items"]] == [st["id"]]
    r = call(client, "channel.analyze", {"study_id": st["id"]})
    assert r.status_code == 409 and r.json()["error_code"] == "llm_not_configured"
    r = call(client, "channel.apply", {"study_id": st["id"]})
    assert r.status_code == 409 and r.json()["error_code"] == "study_not_analyzed"
    prev = ok(client, "channel.study_delete_preview", {"id": st["id"]})
    ok(client, "channel.study_delete", {"confirm_token": prev["confirm_token"]})
    assert ok(client, "channel.study_list")["items"] == []


def test_analyze_and_apply(client, svc, project, tmp_path):
    v1 = _upload(svc, make_three_colors(tmp_path / "a.mp4"), project)
    v2 = _upload(svc, make_three_colors(tmp_path / "b.mp4", seg=1.5, colors=("white", "black", "red", "blue")), project)
    _llm(svc, {"analyze_channel": json.dumps(ANALYSIS)})
    st = ok(client, "channel.study_create", {"title": "Kênh Mẫu", "media_ids": [v1, v2], "project_id": project})
    job = wait(client, ok(client, "channel.analyze", {"study_id": st["id"]})["id"])
    assert job["status"] == "succeeded", job
    got = ok(client, "channel.study_get", {"id": st["id"]})
    assert got["status"] == "done"
    a, b = got["result"]["videos"]
    assert a["metrics"]["scenes"] == 3 and a["metrics"]["cuts"] == 2 and abs(a["metrics"]["avg_shot_s"] - 2.0) < 0.1
    assert b["metrics"]["scenes"] == 4
    assert len(a["frame_times_s"]) <= 16 and a["analysis"]["hook"]["pattern"] == "shock"
    agg = got["result"]["aggregate"]
    assert agg["videos"] == 2 and agg["subtitles"]["present_ratio"] == 1.0
    assert agg["style"]["palette"][0] == "warm amber" and agg["genre_suggestion"]["transition"] == "cut"
    with svc.db.session() as s:  # lưới keyframe là media phái sinh
        sheet = s.get(Media, a["sheet_media_id"])
        assert sheet.origin == "derived" and sheet.kind == "image"

    out = ok(client, "channel.apply", {"study_id": st["id"], "make": {"library_suggestions": True}})
    assert out["project_id"] == project and out["created_project"] is False  # ghi vào DỰ ÁN, không tạo preset
    assert set(out["applied"]) == {"genre", "style_block", "negative_block"}
    assert {x["kind"] for x in out["library_suggestions"]} == {"character", "location"}
    with svc.db.session() as s:
        from sagareel.db.models import Project

        p = s.get(Project, project)
        assert p.genre["clips"][0] >= 1 and p.style_block.startswith("cinematic")
        assert not s.scalars(select(Preset).where(Preset.kind.in_(["genre", "style_pack"]))).all()
    only = ok(client, "channel.apply", {"study_id": st["id"], "make": {"genre": False, "style": False,
                                                                       "library_suggestions": False}})
    assert only["applied"] == [] and only["library_suggestions"] == []
    with svc.db.session() as s:
        assert s.scalars(select(Media).where(Media.id.in_([a["media_id"], b["media_id"]]))).all()


def test_analyze_invalid_json_marks_failed(client, svc, project, tmp_path):
    v = _upload(svc, make_three_colors(tmp_path / "a.mp4"), project)
    _llm(svc, {"analyze_channel": "[1, 2]"})
    st = ok(client, "channel.study_create", {"title": "Hỏng", "media_ids": [v], "project_id": project})
    job = wait(client, ok(client, "channel.analyze", {"study_id": st["id"]})["id"])
    assert job["status"] == "failed" and job["error_code"] == "llm_invalid_json"
    assert ok(client, "channel.study_get", {"id": st["id"]})["status"] == "failed"


# --- clone --------------------------------------------------------------------------------------------------------------

CLONE_REPLY = {
    "title": "Cảnh", "image_prompt": "An walks through a neon alley", "motion_prompt": "slow push in",
    "shot_size": "mcu", "angle": "eye", "camera_move": "push_in", "lens": "bogus", "composition": "center",
    "eyeline": "screen_left", "screen_dir": "L>R", "first_frame_desc": "f", "last_frame_desc": "l",
    "sfx_desc": "steps", "present": ["cô gái áo đỏ"],
}


@pytest.fixture
def clone_setup(client, svc, project, tmp_path):
    asset = ok(client, "asset.create", {"project_id": project, "kind": "character", "name": "An", "key": "AN",
                                        "description": "Cô gái tóc đen"})
    seq = ok(client, "sequence.create", {"project_id": project, "title": "Tập 1"})
    vid = _upload(svc, make_three_colors(tmp_path / "src.mp4"), project)
    return {"asset": asset, "seq": seq["id"], "video": vid, "project": project}


def test_clone_dry_run_and_validation(client, clone_setup):
    base = {"media_id": clone_setup["video"], "sequence_id": clone_setup["seq"],
            "replace_map": {"cô gái áo đỏ": "AN"}, "transcript": "Một. Hai. Ba."}
    plan = ok(client, "clone.from_video", {**base, "dry_run": True})
    assert plan["scene_count"] == 3 and plan["llm_calls"] == 3
    assert [s["engine"] for s in plan["scenes"]] == ["still_motion"] * 3  # 2 s < 4.5 s
    assert [s["dialogue"] for s in plan["scenes"]] == ["Một.", "Hai.", "Ba."]
    ai = ok(client, "clone.from_video", {**base, "dry_run": True, "engine": "ai_video"})
    assert [s["duration_ms"] for s in ai["scenes"]] == [6000] * 3
    r = call(client, "clone.from_video", {**base, "replace_map": {"x": "NOPE"}, "dry_run": True})
    assert r.status_code == 422 and r.json()["error_code"] == "asset_not_found"
    r = call(client, "clone.from_video", {**base, "media_id": 999999, "dry_run": True})
    assert r.status_code == 404
    r = call(client, "clone.from_video", base)
    assert r.status_code == 409 and r.json()["error_code"] == "llm_not_configured"


def test_clone_creates_clips(client, svc, clone_setup):
    _llm(svc, {"clone_scene": json.dumps(CLONE_REPLY)})
    out = ok(client, "clone.from_video", {
        "media_id": clone_setup["video"], "sequence_id": clone_setup["seq"],
        "replace_map": {"cô gái áo đỏ": "AN"}, "transcript": "Câu một. Câu hai. Câu ba."})
    job = wait(client, out["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["scenes"] == 3 and job["result"]["engines"] == {"still_motion": 3}
    with svc.db.session() as s:
        rows = s.scalars(select(Clip).where(Clip.sequence_id == clone_setup["seq"]).order_by(Clip.rank)).all()
        assert len(rows) == 3
        c = rows[0]
        assert c.engine == "still_motion" and 1800 <= c.duration_ms <= 2200
        assert c.image_prompt.startswith("An walks") and c.motion_prompt == "slow push in"
        assert (c.shot_size, c.camera_move, c.screen_dir) == ("mcu", "push_in", "L→R") and c.lens == ""
        assert c.assets == [{"asset_id": clone_setup["asset"]["id"], "role": "character"}]
        assert c.custom["clone"]["scene"] == 1 and c.prompt_mode == "synced"
        lines = s.scalars(select(DialogueLine).order_by(DialogueLine.clip_id)).all()
        assert [x.text for x in lines] == ["Câu một.", "Câu hai.", "Câu ba."]
        # khung hình gốc không thành media; thư mục tạm đã xoá
        assert s.scalars(select(Media).where(Media.origin == "derived")).first() is None
    assert not (svc.settings.cache_dir / "clone" / f"job{job['id']}").exists()


def test_clone_ai_video_snaps_durations(client, svc, tmp_path, clone_setup):
    long_video = _upload(svc, make_three_colors(tmp_path / "long.mp4", seg=5.0, colors=("red", "blue")),
                         clone_setup["project"])
    _llm(svc, {"clone_scene": json.dumps(CLONE_REPLY)})
    job = wait(client, ok(client, "clone.from_video", {"media_id": long_video, "sequence_id": clone_setup["seq"]})["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["engines"] == {"ai_video": 2}
    with svc.db.session() as s:
        assert [c.duration_ms for c in s.scalars(select(Clip).order_by(Clip.rank))] == [6000, 6000]
