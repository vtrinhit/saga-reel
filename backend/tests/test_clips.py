"""Task B — clip: tạo/chèn theo rank, danh sách cursor 1000+ clip, di chuyển chỉ ghi 1 dòng, enum shot, làm-cũ khi sửa,
tách/nhân bản, xoá hai bước (timeline neo sang clip kế)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from _fixtures_b import StatementCounter, add_take, seed_clips
from sagareel.core.errors import AppError
from sagareel.db.models import Clip, DialogueLine, Revision, TimelineItem
from sagareel.services import clips, sequences


@pytest.fixture
def seq(owner_ctx, project) -> int:
    return sequences.create(owner_ctx, project, "Tập 1")["id"]


def _db(ctx):
    return ctx.services.db


def _order(ctx, seq_id) -> list[int]:
    with _db(ctx).session() as s:
        return [c.id for c in clips.ordered(s, seq_id)]


def test_create_appends_inserts_and_prepends(owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {"image_prompt": "A"})
    b = clips.create(owner_ctx, seq, {"image_prompt": "B"})
    c = clips.create(owner_ctx, seq, {"image_prompt": "C"}, after_clip_id=a["id"])
    d = clips.create(owner_ctx, seq, {"image_prompt": "D"}, after_clip_id=0)
    assert _order(owner_ctx, seq) == [d["id"], a["id"], c["id"], b["id"]]
    assert a["key"] == "c0001" and b["key"] == "c0002"
    assert a["duration_ms"] == 6000 and a["prompt_mode"] == "synced"


def test_bulk_create_one_change_set_keys_and_custom_key(owner_ctx, seq):
    rows = clips.bulk_create(owner_ctx, seq, [{"key": "X1", "title": "một"}, {"title": "hai"}, {}])
    assert [r["key"] for r in rows] == ["X1", "c0001", "c0002"]
    with _db(owner_ctx).session() as s:
        css = {r.change_set_id for r in s.scalars(select(Revision).where(Revision.entity_type == "clip"))}
    assert len(css) == 1
    with pytest.raises(AppError) as e:
        clips.create(owner_ctx, seq, {"key": "X1"})
    assert e.value.code == "key_taken"


def test_shot_enums_validated(owner_ctx, seq):
    ok = clips.create(owner_ctx, seq, {"shot_size": "mcu", "angle": "ots", "camera_move": "push_in", "lens": "85mm",
                                       "composition": "thirds_l", "eyeline": "screen_right", "screen_dir": "L>R",
                                       "custom": {"axis_side": "A", "cut_in": "match_action"}})
    assert ok["screen_dir"] == "L→R"
    for bad in ({"shot_size": "huge"}, {"camera_move": "zoom"}, {"engine": "magic"}, {"link": "wipe"},
                {"custom": {"axis_side": "C"}}, {"custom": {"cut_in": "fade"}}, {"duration_ms": 0}):
        with pytest.raises(AppError) as e:
            clips.create(owner_ctx, seq, bad)
        assert e.value.code == "validation_failed", bad
    clips.create(owner_ctx, seq, {"shot_size": ""})  # rỗng = chưa đặt


def test_list_cursor_handles_1200_clips(owner_ctx, seq):
    ids = seed_clips(_db(owner_ctx), seq, 1200)
    seen, cursor, pages = [], None, 0
    while True:
        page = clips.list_clips(owner_ctx, seq, cursor, limit=500)
        seen += [c["id"] for c in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ids and pages == 3


def test_move_one_clip_among_1000_updates_exactly_one_row(owner_ctx, seq):
    ids = seed_clips(_db(owner_ctx), seq, 1000)
    with StatementCounter(_db(owner_ctx), "UPDATE clip") as counter:
        clips.move(owner_ctx, [ids[900]], after_clip_id=ids[10])
    assert counter.count == 1
    order = _order(owner_ctx, seq)
    assert order[11] == ids[900] and len(order) == 1000
    # đầu tập và cuối tập
    with StatementCounter(_db(owner_ctx), "UPDATE clip") as counter:
        clips.move(owner_ctx, [ids[500]], after_clip_id=0)
        clips.move(owner_ctx, [ids[0]], after_clip_id=None)
    assert counter.count == 2
    order = _order(owner_ctx, seq)
    assert order[0] == ids[500] and order[-1] == ids[0]


def test_move_several_keeps_given_order_and_insert_writes_k_rows(owner_ctx, seq):
    ids = seed_clips(_db(owner_ctx), seq, 50)
    with StatementCounter(_db(owner_ctx), "UPDATE clip") as counter:
        clips.move(owner_ctx, [ids[40], ids[30], ids[45]], after_clip_id=ids[2])
    assert counter.count == 3
    order = _order(owner_ctx, seq)
    assert order[3:6] == [ids[40], ids[30], ids[45]]
    with StatementCounter(_db(owner_ctx), "UPDATE clip") as counter:
        clips.create(owner_ctx, seq, {}, after_clip_id=ids[20])
    assert counter.count == 0


def test_move_rebalances_when_ranks_tie(owner_ctx, seq):
    a, b, c = seed_clips(_db(owner_ctx), seq, 3)
    with _db(owner_ctx).session() as s:  # dữ liệu nhập ngoài có thể trùng rank
        for i in (a, b, c):
            s.get(Clip, i).rank = "a5"
    clips.move(owner_ctx, [c], after_clip_id=a)
    assert _order(owner_ctx, seq) == [a, c, b]


def test_list_filters_flags_review_scene_asset(owner_ctx, seq):
    sc = sequences.scene_create(owner_ctx, seq, "Phòng khách")["id"]
    a = clips.create(owner_ctx, seq, {"assets": [{"asset_id": 7, "variant_id": 3}], "flags": {"stale_video": True}})
    b = clips.create(owner_ctx, seq, {"review": "approved", "scene_id": sc, "flags": {"drift": {"score": 40}}})
    c = clips.create(owner_ctx, seq, {"assets": [{"asset_id": 7}], "flags": {"stale_video": False}})

    def ids(flt):
        return [x["id"] for x in clips.list_clips(owner_ctx, seq, flt=flt)["items"]]

    assert ids({"flags": ["stale_video"]}) == [a["id"]]
    assert ids({"flags": ["stale_video", "drift"]}) == [a["id"], b["id"]]
    assert ids({"review": "approved"}) == [b["id"]]
    assert ids({"scene_id": sc}) == [b["id"]]
    assert ids({"scene_id": None}) == [a["id"], c["id"]]
    assert ids({"asset_id": 7}) == [a["id"], c["id"]]
    assert ids({"asset_id": 7, "variant_id": 3}) == [a["id"]]


def test_update_marks_stale_only_for_changed_fields_with_outputs(owner_ctx, seq):
    c = clips.create(owner_ctx, seq, {"image_prompt": "cũ"})
    # chưa có take nào → không có gì "cũ"
    out = clips.update(owner_ctx, c["id"], {"image_prompt": "mới"})
    assert out["flags"] == {} and out["stale_applied"] == []
    db = _db(owner_ctx)
    it = add_take(db, c["id"], "image", media_id=None)
    vt = add_take(db, c["id"], "video", media_id=None)
    with db.session() as s:
        row = s.get(Clip, c["id"])
        row.image_take_id, row.video_take_id = it, vt
    out = clips.update(owner_ctx, c["id"], {"image_prompt": "mới"})  # không đổi giá trị
    assert out["flags"] == {}
    out = clips.update(owner_ctx, c["id"], {"motion_prompt": "đi chậm"})
    assert out["flags"] == {"stale_video": True}
    out = clips.update(owner_ctx, c["id"], {"image_prompt": "khác", "shot_size": "cu"})
    assert out["flags"] == {"stale_video": True, "stale_image": True}
    out = clips.update(owner_ctx, c["id"], {"transition": {"kind": "fade", "duration_ms": 500}})
    assert out["flags"]["stale_chunk"] is True
    # sửa và gắn cờ cùng một change set
    with db.session() as s:
        last = s.scalars(select(Revision).where(Revision.entity_type == "clip").order_by(Revision.id.desc())).first()
        assert last.after_json["flags"]["stale_chunk"] is True and last.after_json["transition"]["kind"] == "fade"


def test_update_rejects_rank_and_take_fields_and_stale_write(owner_ctx, seq):
    c = clips.create(owner_ctx, seq, {})
    for field in ("rank", "image_take_id", "sequence_id"):
        with pytest.raises(AppError) as e:
            clips.update(owner_ctx, c["id"], {field: "a1" if field == "rank" else 1})
        assert e.value.code == "readonly_field"
    from datetime import datetime, timedelta

    with pytest.raises(AppError) as e:
        clips.update(owner_ctx, c["id"], {"title": "x"},
                     expected_updated_at=datetime.fromisoformat(str(c["updated_at"])) - timedelta(seconds=5))
    assert e.value.code == "stale_write"


def test_bulk_update(owner_ctx, seq):
    ids = [clips.create(owner_ctx, seq, {})["id"] for _ in range(3)]
    out = clips.bulk_update(owner_ctx, ids, {"engine": "still_motion", "motion_preset": "slow_zoom_in"})
    assert out["updated"] == ids
    assert {c["engine"] for c in clips.list_clips(owner_ctx, seq)["items"]} == {"still_motion"}


def test_split_shares_take_and_moves_dialogue(owner_ctx, seq):
    c = clips.create(owner_ctx, seq, {"duration_ms": 10000, "image_prompt": "p"})
    nxt = clips.create(owner_ctx, seq, {})
    db = _db(owner_ctx)
    vt = add_take(db, c["id"], "video")
    with db.session() as s:
        s.get(Clip, c["id"]).video_take_id = vt
        s.add_all([DialogueLine(clip_id=c["id"], rank="a0", text="đầu", offset_ms=500),
                   DialogueLine(clip_id=c["id"], rank="a1", text="sau", offset_ms=7000)])
    out = clips.split(owner_ctx, c["id"], 4000)
    first, second = out["first"], out["second"]
    assert first["duration_ms"] == 4000 and first["trim_out_ms"] == 6000
    assert second["duration_ms"] == 6000 and second["trim_in_ms"] == 4000 and second["video_take_id"] == vt
    assert _order(owner_ctx, seq) == [c["id"], second["id"], nxt["id"]]
    with db.session() as s:
        moved = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == second["id"])).all()
        assert [(d.text, d.offset_ms) for d in moved] == [("sau", 3000)]
    with pytest.raises(AppError):
        clips.split(owner_ctx, c["id"], 4000)


def test_duplicate_copies_without_takes(owner_ctx, seq):
    c = clips.create(owner_ctx, seq, {"image_prompt": "p", "flags": {"drift": True}})
    tail = clips.create(owner_ctx, seq, {})
    db = _db(owner_ctx)
    it = add_take(db, c["id"], "image")
    with db.session() as s:
        s.get(Clip, c["id"]).image_take_id = it
        s.add(DialogueLine(clip_id=c["id"], rank="a0", text="chào"))
    d = clips.duplicate(owner_ctx, c["id"])
    assert d["image_prompt"] == "p" and d["image_take_id"] is None and d["flags"] == {}
    assert _order(owner_ctx, seq) == [c["id"], d["id"], tail["id"]]
    assert [x["text"] for x in clips.get(owner_ctx, d["id"])["dialogue"]] == ["chào"]


def test_delete_two_steps_keeps_takes_and_reanchors_timeline(owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {})
    b = clips.create(owner_ctx, seq, {})
    c = clips.create(owner_ctx, seq, {})
    db = _db(owner_ctx)
    add_take(db, b["id"], "image")
    with db.session() as s:
        s.add(TimelineItem(sequence_id=seq, track="music", anchor_clip_id=b["id"], offset_ms=1200))
        s.add(DialogueLine(clip_id=b["id"], rank="a0", text="x"))
    prev = clips.delete_preview(owner_ctx, [b["id"]])
    assert prev["impact"]["takes_kept"] == 1 and prev["impact"]["timeline_items_reanchored"] == 1
    with pytest.raises(AppError) as e:
        clips.delete(owner_ctx, None)
    assert e.value.code == "confirm_required"
    clips.delete(owner_ctx, prev["confirm_token"])
    assert _order(owner_ctx, seq) == [a["id"], c["id"]]
    with db.session() as s:
        item = s.scalars(select(TimelineItem)).one()
        assert item.anchor_clip_id == c["id"]
        assert s.scalars(select(DialogueLine)).one().deleted_at is not None
        from sagareel.db.models import Take

        assert s.scalars(select(Take)).one().owner_id == b["id"]  # take còn nguyên


def test_clip_ops_registered_with_flags(client):
    cat = {o["name"]: o for o in client.get("/api/ops").json()}
    for name in ("clip.create", "clip.bulk_create", "clip.get", "clip.list", "clip.update", "clip.bulk_update",
                 "clip.move", "clip.split", "clip.duplicate", "clip.delete_preview", "clip.delete",
                 "clip.regenerate", "clip.rewrite_prompt", "review.queue"):
        assert name in cat, name
    assert cat["clip.list"]["read_only"] and cat["clip.list"]["viewer"]
    assert cat["clip.delete"]["destructive"] and not cat["clip.delete_preview"]["viewer"]
    assert cat["clip.regenerate"]["costly"] and cat["clip.regenerate"]["long_running"]
    assert cat["clip.regenerate"]["mcp"] == "tool"


def test_clip_list_over_rest(client, owner_ctx, project):
    seq_id = sequences.create(owner_ctx, project, "Tập REST")["id"]
    seed_clips(owner_ctx.services.db, seq_id, 5)
    r = client.post("/api/ops/clip.list", json={"sequence_id": seq_id, "limit": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 2 and body["next_cursor"]
    r = client.post("/api/ops/clip.list", json={"sequence_id": seq_id, "filter": {"bogus": 1}})
    assert r.status_code == 422
