"""Lời thoại + TTS (Task D): CRUD theo rank, bulk_set, lịch sử; audio.generate → job audio.tts mỗi câu (adapter giả),
take dialogue/audio có mốc từ, cache theo khoá giọng, bản dịch; khớp thời lượng voice_drives/clip_drives/none."""

from __future__ import annotations

import dataclasses

import pytest
from _fixtures_d import invoke, make_sequence, rest, setup_fake_tts, wait_jobs
from sagareel.core.errors import AppError
from sagareel.db.models import Asset, Clip, DialogueLine, Media, Take
from sagareel.services import history, media
from sagareel.services.audio import dialogue, mix, timing
from sqlalchemy import select


@pytest.fixture
def seq(owner_ctx, project):
    return make_sequence(owner_ctx.services.db, project, 3)


def texts(ctx, clip_id):
    return [x["text"] for x in dialogue.list_lines(ctx, clip_id=clip_id)]


# --- CRUD -----------------------------------------------------------------------------------------------------------


def test_create_order_move_and_undo(owner_ctx, project, seq):
    _, clips = seq
    a = invoke("dialogue.create", {"clip_id": clips[0], "text": "Một"}, owner_ctx)
    b = invoke("dialogue.create", {"clip_id": clips[0], "text": "Ba"}, owner_ctx)
    invoke("dialogue.create", {"clip_id": clips[0], "text": "Hai", "after_id": a["id"]}, owner_ctx)
    assert texts(owner_ctx, clips[0]) == ["Một", "Hai", "Ba"]
    invoke("dialogue.move", {"ids": [b["id"]]}, owner_ctx)
    assert texts(owner_ctx, clips[0]) == ["Ba", "Một", "Hai"]
    invoke("dialogue.move", {"ids": [b["id"]], "clip_id": clips[1]}, owner_ctx)
    assert texts(owner_ctx, clips[1]) == ["Ba"]
    upd = invoke("dialogue.update", {"id": a["id"], "values": {"text": "Một!", "emotion": "vui"}}, owner_ctx)
    assert upd["text"] == "Một!"
    history.undo(dataclasses.replace(owner_ctx, project_id=project))
    assert texts(owner_ctx, clips[0]) == ["Một", "Hai"]
    with pytest.raises(AppError) as ei:
        invoke("dialogue.update", {"id": a["id"], "values": {"source": "robot"}}, owner_ctx)
    assert ei.value.code == "validation_failed"
    with pytest.raises(AppError) as ei:
        invoke("dialogue.update", {"id": a["id"], "values": {"voice_override": {"rate": "fast"}}}, owner_ctx)
    assert ei.value.code == "validation_failed"


def test_bulk_set_and_two_step_delete(owner_ctx, project, seq):
    _, clips = seq
    x = invoke("dialogue.create", {"clip_id": clips[0], "text": "Cũ một"}, owner_ctx)
    y = invoke("dialogue.create", {"clip_id": clips[0], "text": "Cũ hai"}, owner_ctx)
    out = invoke("dialogue.bulk_set", {"clip_id": clips[0], "lines": [
        {"text": "Mới đầu"}, {"id": y["id"], "text": "Cũ hai sửa"}, {"text": "Mới cuối", "source": "grok"}]},
        owner_ctx)
    assert [i["text"] for i in out["items"]] == ["Mới đầu", "Cũ hai sửa", "Mới cuối"]
    assert texts(owner_ctx, clips[0]) == ["Mới đầu", "Cũ hai sửa", "Mới cuối"]
    with pytest.raises(AppError):
        dialogue.update(owner_ctx, x["id"], {"text": "đã xoá"})
    history.undo(dataclasses.replace(owner_ctx, project_id=project))
    assert texts(owner_ctx, clips[0]) == ["Cũ một", "Cũ hai"]
    prev = invoke("dialogue.delete_preview", {"ids": [x["id"]]}, owner_ctx)
    invoke("dialogue.delete", {"confirm_token": prev["confirm_token"]}, owner_ctx)
    assert texts(owner_ctx, clips[0]) == ["Cũ hai"]
    with pytest.raises(AppError) as ei:
        invoke("dialogue.bulk_set", {"clip_id": clips[1], "lines": [{"id": y["id"], "text": "khác clip"}]},
               owner_ctx)
    assert ei.value.code == "invalid_reference"


# --- TTS ------------------------------------------------------------------------------------------------------------


def _takes(db, dialogue_id):
    with db.session() as s:
        return s.scalars(select(Take).where(Take.owner_type == "dialogue", Take.owner_id == dialogue_id)
                         .order_by(Take.id)).all()


def test_generate_tts_takes_cache_and_translations(client, owner_ctx, project, seq, settings):
    setup_fake_tts(client)
    seq_id, clips = seq
    db = owner_ctx.services.db
    l1 = dialogue.create(owner_ctx, clips[0], "Con ơi, về ăn cơm đi.", translations={"en": "Come home to eat."})
    l2 = dialogue.create(owner_ctx, clips[1], "Dạ, con về liền.")
    dialogue.create(owner_ctx, clips[1], "Ồ!", source="grok")

    dry = rest(client, "audio.generate", {"sequence_id": seq_id, "dry_run": True})
    assert dry["estimate"]["lines"] == 2 and dry["to_generate"] == [l1["id"], l2["id"]]
    assert dry["skipped"][0]["reason"] == "source=grok"
    assert _takes(db, l1["id"]) == []

    res = rest(client, "audio.generate", {"sequence_id": seq_id})
    jobs = wait_jobs(client, res["job_ids"])
    assert len(jobs) == 2 and all(j["status"] == "succeeded" for j in jobs), jobs
    (t1,) = _takes(db, l1["id"])
    assert t1.part == "audio" and t1.status == "succeeded" and t1.lang == "vi"
    assert t1.inputs["voice"] == "fake-vi-female" and t1.inputs["connection"] == "fake-tts"
    assert [w[0] for w in t1.inputs["words"]] == ["Con", "ơi,", "về", "ăn", "cơm", "đi."]
    assert t1.inputs["timing_estimated"] is False
    assert len(t1.inputs["cache_key"]) == 64
    with db.session() as s:
        assert s.get(DialogueLine, l1["id"]).audio_take_id == t1.id
        m = s.get(Media, t1.media_id)
        assert m.kind == "audio" and m.duration_ms > 0
        path = media.file_path(settings, m)
    assert abs(mix.measure_loudness(path)["integrated_lufs"] + 16) <= 1.0

    # Gọi lại: không có gì đổi → không job, không take mới.
    again = rest(client, "audio.generate", {"sequence_id": seq_id})
    assert again["job_ids"] == [] and len(again["unchanged"]) == 2
    # Câu khác cùng chữ + cùng giọng → take mới cùng media (cache), không gọi provider.
    l3 = dialogue.create(owner_ctx, clips[2], "Con ơi, về ăn cơm đi.")
    third = rest(client, "audio.generate", {"clip_ids": [clips[2]]})
    assert third["job_ids"] == [] and third["cached"][0]["from_take_id"] == t1.id
    (t3,) = _takes(db, l3["id"])
    assert t3.media_id == t1.media_id and t3.parent_take_id == t1.id and t3.id != t1.id
    # Đổi tốc độ đọc → khoá cache khác → đọc lại; take cũ vẫn còn.
    dialogue.update(owner_ctx, l1["id"], {"voice_override": {"rate": "+10%"}})
    re_ = rest(client, "audio.generate", {"clip_ids": [clips[0]]})
    assert len(re_["job_ids"]) == 1
    wait_jobs(client, re_["job_ids"])
    takes = _takes(db, l1["id"])
    assert len(takes) == 2 and takes[1].inputs["rate"] == "+10%"
    with db.session() as s:
        assert s.get(DialogueLine, l1["id"]).audio_take_id == takes[1].id

    # Bản dịch: take lang=en, không đổi audio_take_id của ngôn ngữ chính.
    en = rest(client, "audio.generate", {"clip_ids": [clips[0]], "lang": "en"})
    wait_jobs(client, en["job_ids"])
    en_take = [t for t in _takes(db, l1["id"]) if t.lang == "en"]
    assert len(en_take) == 1 and en_take[0].inputs["text"] == "Come home to eat."
    with db.session() as s:
        assert s.get(DialogueLine, l1["id"]).audio_take_id == takes[1].id
    skipped = rest(client, "audio.generate", {"clip_ids": [clips[1]], "lang": "en", "dry_run": True})
    assert {x["reason"] for x in skipped["skipped"]} == {"empty_text", "source=grok"}


def test_voice_resolution_profile_per_lang_and_override(client, owner_ctx, project, seq):
    setup_fake_tts(client)
    _, clips = seq
    vp = rest(client, "voice.create", {"name": "An", "project_id": project, "connection": "fake-tts",
                                       "voice": "fake-vi-male", "rate": "-5%",
                                       "per_lang": {"en": {"voice": "fake-en-female"}}})
    with owner_ctx.services.db.session() as s:
        a = Asset(project_id=project, kind="character", key="AN", name="An", voice_profile_id=vp["id"])
        s.add(a)
        s.flush()
        asset_id = a.id
    line = dialogue.create(owner_ctx, clips[0], "Chào em.", speaker_asset_id=asset_id,
                           translations={"en": "Hi."})
    with owner_ctx.services.db.session() as s:
        plan = dialogue.plan_tts(s, owner_ctx.services, [clips[0]])
        plan_en = dialogue.plan_tts(s, owner_ctx.services, [clips[0]], "en")
    it = plan["items"][0]
    assert (it["voice"], it["rate"], it["connection"], it["profile_id"]) == ("fake-vi-male", "-5%", "fake-tts", vp["id"])
    assert plan_en["items"][0]["voice"] == "fake-en-female" and plan_en["items"][0]["text"] == "Hi."
    dialogue.update(owner_ctx, line["id"], {"voice_override": {"voice": "fake-vi-female", "pitch": "+5Hz"}})
    with owner_ctx.services.db.session() as s:
        it2 = dialogue.plan_tts(s, owner_ctx.services, [clips[0]])["items"][0]
    assert (it2["voice"], it2["pitch"], it2["rate"]) == ("fake-vi-female", "+5Hz", "-5%")
    assert it2["cache_key"] != it["cache_key"]
    # Người kể (không có speaker) dùng hồ sơ narrator của project.
    narrator = rest(client, "voice.create", {"name": "Người kể", "project_id": project, "voice": "fake-vi-female",
                                             "volume": "+20%"})
    rest(client, "project.update", {"id": project, "values": {"settings": {"narrator_voice_profile_id":
                                                                           narrator["id"]}}})
    dialogue.create(owner_ctx, clips[1], "Ngày xửa ngày xưa.")
    with owner_ctx.services.db.session() as s:
        it3 = dialogue.plan_tts(s, owner_ctx.services, [clips[1]])["items"][0]
    assert it3["volume"] == "+20%" and it3["connection"] == "fake-tts"


def test_voice_preview_job(client, owner_ctx):
    setup_fake_tts(client)
    job_id = rest(client, "voice.preview", {"text": "Xin chào các bạn"})["job_id"]
    (job,) = wait_jobs(client, [job_id])
    assert job["status"] == "succeeded", job
    assert job["result"]["take_id"] is None and len(job["result"]["media_ids"]) == 1
    assert [w[0] for w in job["result"]["words"]] == ["Xin", "chào", "các", "bạn"]


# --- Khớp thời lượng -----------------------------------------------------------------------------------------------


def test_duration_plan_modes_and_apply(client, owner_ctx, project):
    setup_fake_tts(client)
    db = owner_ctx.services.db
    seq_id, clips = make_sequence(db, project, 3, duration_ms=6000)
    with db.session() as s:
        s.get(Clip, clips[1]).engine = "still_motion"
        s.get(Clip, clips[2]).duration_ms = 1000
    # fake_tts: 70 ms/ký tự
    dialogue.create(owner_ctx, clips[0], "x" * 100)                      # 7,0 s (+ thở + đệm cắt) → ai_video 8 s
    dialogue.create(owner_ctx, clips[1], "y" * 30, offset_ms=500)         # 0,5 + 2,1 s
    dialogue.create(owner_ctx, clips[2], "z" * 20)                        # 1,4 s trong clip 1 s
    wait_jobs(client, rest(client, "audio.generate", {"sequence_id": seq_id})["job_ids"])

    plan = rest(client, "audio.duration_plan", {"sequence_id": seq_id})
    assert plan["mode"] == "voice_drives" and plan["ai_durations"] == []  # không có kết nối video: không ép mức
    by = {x["clip_id"]: x for x in plan["items"]}
    assert by[clips[0]]["voice_ms"] >= 7000 and by[clips[0]]["ai_duration_s"] == 8
    assert by[clips[1]]["proposed_ms"] == timing._round_up(by[clips[1]]["voice_ms"])
    assert by[clips[1]]["voice_ms"] >= 2600

    cd = rest(client, "audio.duration_plan", {"sequence_id": seq_id, "mode": "clip_drives"})
    c2 = {x["clip_id"]: x for x in cd["items"]}[clips[2]]
    assert c2["atempo"] == 1.25 and c2["warnings"][0]["code"] == "atempo_exceeds_limit"
    none = rest(client, "audio.duration_plan", {"sequence_id": seq_id, "mode": "none"})
    assert {x["clip_id"]: x for x in none["items"]}[clips[2]]["warnings"][0]["code"] == "voice_longer_than_clip"

    applied = rest(client, "audio.apply_duration_plan", {"sequence_id": seq_id})
    assert set(applied["changed_clip_ids"]) == set(clips)
    with db.session() as s:
        c0 = s.get(Clip, clips[0])
        assert c0.duration_ms == by[clips[0]]["proposed_ms"]
        assert c0.settings["audio_sync"]["ai_duration_s"] == 8
    history.undo(dataclasses.replace(owner_ctx, project_id=project))
    with db.session() as s:
        assert s.get(Clip, clips[0]).duration_ms == 6000


def test_plan_clip_pure_rules():
    caps = [6, 10, 15]
    p = timing.plan_clip(mode="voice_drives", engine="ai_video", current_ms=6000, need_ms=16_500, ai_durations=caps)
    assert p["ai_duration_s"] == 15 and p["video_speed"] == pytest.approx(15000 / 16500, rel=1e-3)
    p = timing.plan_clip(mode="voice_drives", engine="ai_video", current_ms=6000, need_ms=25_000, ai_durations=caps)
    assert p["video_speed"] == 0.8 and p["hold_last_frame_ms"] == 25_000 - 18_750
    assert p["warnings"][0]["code"] == "voice_exceeds_max_duration"
    p = timing.plan_clip(mode="clip_drives", engine="ai_video", current_ms=6000, need_ms=7000)
    assert p["atempo"] == pytest.approx(7000 / 6000, rel=1e-3) and p["warnings"] == []
    # mức nhỏ nhất ≥ giọng + 0,3 s đệm cắt; caps rỗng → làm tròn lên giây
    assert timing.choose_ai_duration(5700, caps) == (6, True)
    assert timing.choose_ai_duration(5701, [10, 6]) == (10, True)
    assert timing.choose_ai_duration(16_000, caps) == (15, False)
    assert timing.choose_ai_duration(6000, []) == (7, True)



def test_translate_dub_and_list_audio_duration(client, owner_ctx, project, seq, monkeypatch):
    """Task BF: audio.translate_dub (LLM translate → translations → TTS theo ngôn ngữ); dialogue.list kèm độ dài
    âm thanh đang chọn."""
    import _fixtures_j

    fake = _fixtures_j.install(monkeypatch)
    fake.on_fn("translate", lambda v: {"target_language": "en", "items": [
        {"id": it["id"], "text": "EN " + it["text"]} for it in __import__("json").loads(v["input"])["items"]]})
    setup_fake_tts(client)
    seq_id, clips = seq
    l1 = dialogue.create(owner_ctx, clips[0], "Con ơi, về ăn cơm đi.")
    l2 = dialogue.create(owner_ctx, clips[1], "Dạ.", translations={"en": "Yes."})
    dry = rest(client, "audio.translate_dub", {"sequence_id": seq_id, "lang": "en", "dry_run": True})
    assert dry["estimate"]["to_translate"] == 1 and dry["estimate"]["llm_calls"] == 1 and not fake.calls
    assert dry["estimate"]["tts"]["lines"] == 2  # cả câu chưa dịch (l1) lẫn câu đã dịch (l2)
    started = rest(client, "audio.translate_dub", {"sequence_id": seq_id, "lang": "en"})
    out = wait_jobs(client, [started["job_id"]])[0]["result"]
    assert out["translated"] == 1 and out["missing"] == [] and isinstance(out["change_set_id"], int)
    with owner_ctx.services.db.session() as s:
        assert s.get(DialogueLine, l1["id"]).translations["en"] == "EN Con ơi, về ăn cơm đi."
        assert s.get(DialogueLine, l2["id"]).translations["en"] == "Yes."  # đã có → giữ
    wait_jobs(client, out["tts"]["job_ids"])
    with owner_ctx.services.db.session() as s:
        en = s.scalars(select(Take).where(Take.owner_type == "dialogue", Take.lang == "en")).all()
    assert len(en) == 2
    listed = rest(client, "take.list", {"owner_type": "dialogue", "owner_id": l2["id"]})  # "Yes." rất ngắn: -inf LUFS
    assert listed["items"]
    r = client.post("/api/ops/audio.translate_dub", json={"sequence_id": seq_id, "lang": "vi"})
    assert r.status_code == 422  # ngôn ngữ chính

    res = rest(client, "audio.generate", {"sequence_id": seq_id})
    wait_jobs(client, res["job_ids"])
    items = rest(client, "dialogue.list", {"sequence_id": seq_id})["items"]
    first = next(x for x in items if x["id"] == l1["id"])
    assert first["audio_media_id"] and first["audio_duration_ms"] and first["audio_duration_ms"] > 0
