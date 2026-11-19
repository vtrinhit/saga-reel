"""Hồ sơ giọng (Task D): tạo/sửa (kiểm dạng rate/pitch/volume)/liệt kê/xoá hai bước + lịch sử; kho âm thanh; mục
timeline neo clip (§23.1) và chuyển neo khi xoá clip (`timeline.reanchor_for_deleted_clips`)."""

from __future__ import annotations

import dataclasses

import pytest
from _fixtures_d import invoke, make_sequence
from sagareel.core.errors import AppError
from sagareel.db.models import Asset, Clip, Media, TimelineItem
from sagareel.services import entities, history
from sagareel.services.audio import timeline, voices
from sqlalchemy import select


def test_voice_crud_validation_list_delete(owner_ctx, project):
    g = invoke("voice.create", {"name": "Giọng chung", "voice": "vi-VN-HoaiMyNeural"}, owner_ctx)
    v = invoke("voice.create", {"name": "Bà Tư", "project_id": project, "voice": "vi-VN-NamMinhNeural",
                                "rate": "-10%", "pitch": "-2Hz"}, owner_ctx)
    assert v["key"] == "ba-tu" and v["rate"] == "-10%" and g["project_id"] is None
    dup = invoke("voice.create", {"name": "Bà Tư", "project_id": project}, owner_ctx)
    assert dup["key"] == "ba-tu-2"
    with pytest.raises(AppError) as ei:
        invoke("voice.create", {"name": "Sai", "rate": "nhanh"}, owner_ctx)
    assert ei.value.code == "validation_failed"
    with pytest.raises(AppError):
        invoke("voice.update", {"id": v["id"], "values": {"per_lang": {"en": {"pitch": "10"}}}}, owner_ctx)
    out = invoke("voice.update", {"id": v["id"], "values": {"volume": "+5%"}}, owner_ctx)
    assert out["volume"] == "+5%"
    items = invoke("voice.list", {"project_id": project}, owner_ctx)["items"]
    assert {i["name"] for i in items} == {"Giọng chung", "Bà Tư"}
    assert [i["name"] for i in invoke("voice.list", {}, owner_ctx)["items"]] == ["Giọng chung"]

    with owner_ctx.services.db.session() as s:
        s.add(Asset(project_id=project, kind="character", key="BT", name="Bà Tư", voice_profile_id=v["id"]))
    prev = invoke("voice.delete_preview", {"ids": [v["id"]]}, owner_ctx)
    assert prev["impact"]["used_by_assets"][0]["key"] == "BT"
    invoke("voice.delete", {"confirm_token": prev["confirm_token"]}, owner_ctx)

    def keys():
        return {i["key"] for i in invoke("voice.list", {"project_id": project}, owner_ctx)["items"]}

    assert keys() == {"giong-chung", "ba-tu-2"}
    history.undo(dataclasses.replace(owner_ctx, project_id=project))
    assert keys() == {"giong-chung", "ba-tu", "ba-tu-2"}


def test_resolve_falls_back_to_settings(owner_ctx, project):
    with owner_ctx.services.db.session() as s:
        p = entities.get(s, "project", project)
        v = voices.resolve(s, owner_ctx.services, project=p)
    assert v["connection"] == "edge-tts" and v["voice"] == "vi-VN-HoaiMyNeural" and v["profile_id"] is None


# --- Kho âm thanh + timeline ------------------------------------------------------------------------------------------


def _audio_media(db) -> int:
    with db.session() as s:
        m = Media(kind="audio", path="x.mp3", mime="audio/mpeg", size=1, sha256="a" * 64, origin="fake", meta={},
                  duration_ms=5000)
        s.add(m)
        s.flush()
        return m.id


def test_sound_requires_source_and_license(owner_ctx, project):
    mid = _audio_media(owner_ctx.services.db)
    with pytest.raises(AppError) as ei:
        invoke("sound.add", {"media_id": mid, "kind": "music", "title": "Nhạc", "source": "", "license": "CC0"},
               owner_ctx)
    assert ei.value.code == "validation_failed"
    s1 = invoke("sound.add", {"media_id": mid, "kind": "music", "title": "Nhạc buồn", "source": "freesound",
                              "license": "CC0", "project_id": project}, owner_ctx)
    invoke("sound.add", {"media_id": mid, "kind": "sfx", "title": "Gõ cửa", "source": "tự thu",
                         "license": "own"}, owner_ctx)
    assert [x["id"] for x in invoke("sound.list", {"project_id": project, "kind": "music"}, owner_ctx)["items"]] \
        == [s1["id"]]
    assert len(invoke("sound.list", {"project_id": project}, owner_ctx)["items"]) == 2
    assert [x["title"] for x in invoke("sound.list", {"q": "cửa"}, owner_ctx)["items"]] == ["Gõ cửa"]


def test_timeline_add_update_remove_and_validation(owner_ctx, project):
    db = owner_ctx.services.db
    seq_id, clips = make_sequence(db, project, 3)
    other_seq, other_clips = make_sequence(db, project, 1, key="ep2")
    mid = _audio_media(db)
    snd = invoke("sound.add", {"media_id": mid, "kind": "music", "title": "Nhạc", "source": "x", "license": "CC0"},
                 owner_ctx)
    m = invoke("timeline.add", {"sequence_id": seq_id, "track": "music", "anchor_clip_id": clips[0],
                                "end_anchor": {"sequence_end": True}, "sound_id": snd["id"],
                                "params": {"loop": True}}, owner_ctx)
    assert m["media_id"] == mid and m["offset_ms"] == 0
    t = invoke("timeline.add", {"sequence_id": seq_id, "track": "title", "anchor_clip_id": clips[1],
                                "offset_ms": 500, "duration_ms": 2000, "params": {"text": "Tập 1"}}, owner_ctx)
    for bad in ({"track": "music"},  # thiếu sound/media
                {"track": "title", "params": {}},
                {"track": "sfx", "media_id": mid, "anchor_clip_id": other_clips[0]},
                {"track": "sfx", "media_id": mid, "end_anchor": {"foo": 1}}):
        with pytest.raises(AppError) as ei:
            invoke("timeline.add", {"sequence_id": seq_id, **bad}, owner_ctx)
        assert ei.value.code == "validation_failed", bad
    up = invoke("timeline.update", {"id": t["id"], "values": {"offset_ms": 800}}, owner_ctx)
    assert up["offset_ms"] == 800
    with pytest.raises(AppError):
        invoke("timeline.update", {"id": t["id"], "values": {"anchor_clip_id": other_clips[0]}}, owner_ctx)
    assert len(invoke("timeline.list", {"sequence_id": seq_id}, owner_ctx)["items"]) == 2
    prev = invoke("timeline.remove_preview", {"ids": [t["id"]]}, owner_ctx)
    with pytest.raises(AppError) as ei:
        invoke("timeline.remove", {}, owner_ctx)
    assert ei.value.code == "confirm_required"
    invoke("timeline.remove", {"confirm_token": prev["confirm_token"]}, owner_ctx)
    assert [x["track"] for x in invoke("timeline.list", {"sequence_id": seq_id}, owner_ctx)["items"]] == ["music"]
    history.undo(dataclasses.replace(owner_ctx, project_id=project))
    assert len(invoke("timeline.list", {"sequence_id": seq_id}, owner_ctx)["items"]) == 2
    assert other_seq != seq_id


def test_reanchor_for_deleted_clips(owner_ctx, project):
    db = owner_ctx.services.db
    seq_id, clips = make_sequence(db, project, 4, duration_ms=5000)
    mid = _audio_media(db)
    a = timeline.timeline_add(owner_ctx, seq_id, "sfx", anchor_clip_id=clips[1], offset_ms=300, media_id=mid)
    b = timeline.timeline_add(owner_ctx, seq_id, "sfx", anchor_clip_id=clips[3], offset_ms=100, media_id=mid)
    c = timeline.timeline_add(owner_ctx, seq_id, "music", anchor_clip_id=clips[0], media_id=mid,
                              end_anchor={"clip_id": clips[1]})
    # Task B: xoá clip 2 và 4 trong UoW của nó rồi gọi reanchor trên cùng session.
    u = entities.uow(owner_ctx, "Xoá clip", "clip.delete")
    with u as s:
        u.project_id = project
        now = entities.utcnow()
        for cid in (clips[1], clips[3]):
            s.get(Clip, cid).deleted_at = now
        changed = timeline.reanchor_for_deleted_clips(s, [clips[1], clips[3]])
    assert set(changed) == {a["id"], b["id"], c["id"]}
    with db.session() as s:
        ia, ib, ic = (s.get(TimelineItem, x["id"]) for x in (a, b, c))
        assert (ia.anchor_clip_id, ia.offset_ms) == (clips[2], 300)  # sang clip kế, giữ offset
        assert (ib.anchor_clip_id, ib.offset_ms) == (clips[2], 5100)  # không có clip kế → clip trước + độ dài
        assert ic.end_anchor == {"clip_id": clips[0]}
    # Một change set (của "Task B") → hoàn tác trả cả clip lẫn neo.
    history.undo(dataclasses.replace(owner_ctx, project_id=project))
    with db.session() as s:
        assert s.get(TimelineItem, a["id"]).anchor_clip_id == clips[1]
        assert s.get(Clip, clips[1]).deleted_at is None
    # Xoá hết clip → neo đầu tập, end_anchor → hết tập.
    with db.session() as s:
        for cid in clips:
            s.get(Clip, cid).deleted_at = entities.utcnow()
        timeline.reanchor_for_deleted_clips(s, clips)
    with db.session() as s:
        ic = s.get(TimelineItem, c["id"])
        assert ic.anchor_clip_id is None and ic.end_anchor == {"sequence_end": True}
        assert s.scalars(select(TimelineItem).where(TimelineItem.anchor_clip_id.in_(clips))).all() == []
