"""Task B — take: liệt kê, chọn (áp làm-cũ), chấm, ghi chú, phán quyết (gợi ý đổi cách), so sánh, hàng duyệt."""

from __future__ import annotations

import pytest
from _fixtures_b import add_take
from sagareel.core.errors import AppError
from sagareel.db.models import Clip
from sagareel.services import clips, sequences, takes


@pytest.fixture
def seq(owner_ctx, project) -> int:
    return sequences.create(owner_ctx, project, "Tập 1")["id"]


def _flags(ctx, clip_id):
    with ctx.services.db.session() as s:
        return dict(s.get(Clip, clip_id).flags or {})


def test_list_and_select_image_marks_stale_video_and_next_continue(owner_ctx, seq):
    db = owner_ctx.services.db
    a = clips.create(owner_ctx, seq, {})
    b = clips.create(owner_ctx, seq, {"link": "continue"})
    i1 = add_take(db, a["id"], "image", media_id=101)
    i2 = add_take(db, a["id"], "image", media_id=102)
    v1 = add_take(db, a["id"], "video", media_id=201)
    bv = add_take(db, b["id"], "video", media_id=301)
    takes.select_take(owner_ctx, i1)
    takes.select_take(owner_ctx, v1)
    takes.select_take(owner_ctx, bv)
    assert _flags(owner_ctx, a["id"]) == {} and _flags(owner_ctx, b["id"]) == {}
    out = takes.select_take(owner_ctx, i2)  # ảnh mới → video cũ, clip continue kế tiếp cũ khung đầu
    assert out["owner"]["image_take_id"] == i2 and out["owner"]["video_take_id"] == v1
    assert _flags(owner_ctx, a["id"]) == {"stale_video": True}
    assert _flags(owner_ctx, b["id"]) == {"stale_image": True}
    assert out["stale"]["next"] == {"clip_id": b["id"], "flags": ["stale_image"]}
    v2 = add_take(db, a["id"], "video", media_id=202)
    takes.select_take(owner_ctx, v2)
    assert _flags(owner_ctx, a["id"]) == {}
    bv2 = add_take(db, b["id"], "video", media_id=302)
    takes.select_take(owner_ctx, bv2)  # clip continue tạo video mới → hết stale_image
    assert _flags(owner_ctx, b["id"]) == {}
    listed = takes.list_takes(owner_ctx, "clip", a["id"], "image")
    assert [t["id"] for t in listed["items"]] == [i2, i1]
    assert listed["selected"] == {"image": i2, "video": v2}


def test_select_requires_finished_take(owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {})
    t = add_take(owner_ctx.services.db, a["id"], "image", status="running")
    with pytest.raises(AppError) as e:
        takes.select_take(owner_ctx, t)
    assert e.value.code == "take_not_ready"


def test_rate_note_verdict_suggest_change(owner_ctx, seq):
    db = owner_ctx.services.db
    a = clips.create(owner_ctx, seq, {})
    t1 = add_take(db, a["id"], "video", media_id=1)
    t2 = add_take(db, a["id"], "video", media_id=2)
    t3 = add_take(db, a["id"], "image", media_id=3)
    assert takes.rate(owner_ctx, t1, 4)["rating"] == 4
    with pytest.raises(AppError):
        takes.rate(owner_ctx, t1, 9)
    assert takes.note(owner_ctx, t1, "mặt hơi lệch")["notes"] == "mặt hơi lệch"
    out = takes.verdict(owner_ctx, t1, "regenerate", "Tay lỗi")
    assert out["verdict"] == "regenerate" and out["verdict_reason"] == "Tay lỗi"
    assert out["suggest_change"] is None
    takes.verdict(owner_ctx, t3, "regenerate", "tay lỗi")  # khác phần → không tính
    out = takes.verdict(owner_ctx, t2, "regenerate", "  tay   LỖI ")
    sc = out["suggest_change"]
    assert sc is not None and sc["count"] == 2 and sc["take_ids"] == [t1, t2] and sc["suggestions"]
    out = takes.verdict(owner_ctx, t2, "keep")
    assert out["verdict"] == "keep" and out["verdict_reason"] == ""
    with pytest.raises(AppError):
        takes.verdict(owner_ctx, t2, "maybe")


def test_compare_reports_differing_inputs(owner_ctx, seq):
    db = owner_ctx.services.db
    a = clips.create(owner_ctx, seq, {})
    t1 = add_take(db, a["id"], "image", media_id=1, inputs={"prompt": "x", "model": "m"}, seed=1)
    t2 = add_take(db, a["id"], "image", media_id=2, inputs={"prompt": "y", "model": "m"}, seed=1)
    out = takes.compare(owner_ctx, [t1, t2])
    assert [t["id"] for t in out["items"]] == [t1, t2] and out["diff_keys"] == ["prompt"]


def test_review_queue_defaults_to_pending_with_take_counts(owner_ctx, seq):
    db = owner_ctx.services.db
    a = clips.create(owner_ctx, seq, {})
    b = clips.create(owner_ctx, seq, {"review": "approved"})
    c = clips.create(owner_ctx, seq, {})
    t = add_take(db, a["id"], "image", media_id=None)
    add_take(db, a["id"], "image", status="failed")
    with db.session() as s:
        s.get(Clip, a["id"]).image_take_id = t
    page = takes.review_queue(owner_ctx, seq, None, None, 50)
    assert [x["id"] for x in page["items"]] == [a["id"], c["id"]]
    first = page["items"][0]
    assert first["take_counts"] == {"image": 2} and first["selected_takes"]["image"]["id"] == t
    page = takes.review_queue(owner_ctx, seq, {"review": ["approved"]}, None, 50)
    assert [x["id"] for x in page["items"]] == [b["id"]]


def test_take_ops_over_rest(client, owner_ctx, project):
    seq_id = sequences.create(owner_ctx, project, "REST")["id"]
    a = clips.create(owner_ctx, seq_id, {})
    t = add_take(owner_ctx.services.db, a["id"], "image", media_id=5)
    r = client.post("/api/ops/take.select", json={"take_id": t})
    assert r.status_code == 200, r.text
    r = client.post("/api/ops/take.list", json={"owner_id": a["id"]})
    assert r.status_code == 200 and r.json()["selected"]["image"] == t
    r = client.post("/api/ops/take.verdict", json={"take_id": t, "verdict": "trim"})
    assert r.status_code == 200 and r.json()["verdict"] == "trim"
    r = client.post("/api/ops/review.queue", json={"sequence_id": seq_id})
    assert r.status_code == 200 and r.json()["items"][0]["id"] == a["id"]
