"""Task BF — ghi thân thiện hoàn tác: op clip/take trả `change_set_id`; `take.verdict` null xoá được và hoàn tác được;
`sequence.pipeline/gates` là cột sổ sách (hoàn tác không ghi lùi)."""

from __future__ import annotations

import pytest
from _fixtures_b import add_take
from sagareel.db.models import Sequence, Take
from sagareel.services import clips, history, sequences


@pytest.fixture
def seq(owner_ctx, project) -> int:
    return sequences.create(owner_ctx, project, "Tập 1")["id"]


def _call(client, name, payload):
    r = client.post(f"/api/ops/{name}", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _undo(client, cs_id, project_id):
    return _call(client, "history.undo", {"change_set_id": cs_id, "project_id": project_id})


def test_clip_ops_return_change_set_id(client, owner_ctx, project, seq):
    a = _call(client, "clip.create", {"sequence_id": seq, "fields": {"title": "A"}})
    assert isinstance(a["change_set_id"], int) and a["id"]
    bulk = _call(client, "clip.bulk_create", {"sequence_id": seq, "clips": [{"title": "B"}, {"title": "C"}]})
    assert isinstance(bulk["change_set_id"], int) and len(bulk["items"]) == 2
    b, c = (x["id"] for x in bulk["items"])
    mv = _call(client, "clip.move", {"ids": [c], "after_clip_id": 0})
    assert isinstance(mv["change_set_id"], int)
    _undo(client, mv["change_set_id"], project)
    assert [x["id"] for x in clips.list_clips(owner_ctx, seq)["items"]] == [a["id"], b, c]
    up = _call(client, "clip.bulk_update", {"ids": [a["id"], b], "values": {"notes": "x"}})
    assert isinstance(up["change_set_id"], int)
    same = _call(client, "clip.bulk_update", {"ids": [a["id"], b], "values": {"notes": "x"}})
    assert same["change_set_id"] is None  # không đổi gì → không có change set
    prev = _call(client, "clip.delete_preview", {"ids": [c]})
    gone = _call(client, "clip.delete", {"confirm_token": prev["confirm_token"]})
    assert isinstance(gone["change_set_id"], int)
    _undo(client, gone["change_set_id"], project)
    assert len(clips.list_clips(owner_ctx, seq)["items"]) == 3
    u1 = _call(client, "clip.update", {"id": a["id"], "values": {"title": "A2"}})
    assert isinstance(u1["change_set_id"], int)


def test_take_select_and_verdict_change_set_and_clear(client, owner_ctx, project, seq):
    db = owner_ctx.services.db
    a = clips.create(owner_ctx, seq, {})
    t = add_take(db, a["id"], "image", media_id=5)
    sel = _call(client, "take.select", {"take_id": t})
    assert isinstance(sel["change_set_id"], int)
    v = _call(client, "take.verdict", {"take_id": t, "verdict": "regenerate", "reason": "tay lỗi"})
    assert v["verdict"] == "regenerate" and isinstance(v["change_set_id"], int)
    assert v["previous"] == {"verdict": None, "reason": ""}
    cleared = _call(client, "take.verdict", {"take_id": t, "verdict": None})
    assert cleared["verdict"] == "" and cleared["verdict_reason"] == ""
    assert cleared["previous"] == {"verdict": "regenerate", "reason": "tay lỗi"}
    _undo(client, cleared["change_set_id"], project)  # hoàn tác lần xoá → phán quyết cũ trở lại
    with db.session() as s:
        row = s.get(Take, t)
        assert (row.verdict, row.verdict_reason) == ("regenerate", "tay lỗi")
    page = history.list(owner_ctx.__class__(services=owner_ctx.services, actor="user:owner", role="owner",
                                             project_id=project), "take", t)
    assert page.items and page.items[0].op_name.startswith("history.")
    r = client.post("/api/ops/take.verdict", json={"take_id": t})
    assert r.status_code == 422  # verdict bắt buộc (null để xoá)


def test_pipeline_and_gates_are_bookkeeping(owner_ctx, project, seq):
    from sagareel.services.pipeline import engine
    from sagareel.services.story import state

    sequences.update(owner_ctx, seq, {"title": "Tập 1b"})
    state.replace(owner_ctx.services.db, seq, "run", {"status": "running", "steps": []})
    engine.write_gate(owner_ctx.services, seq, "pilot", {"status": "approved"})
    ctx = owner_ctx.__class__(services=owner_ctx.services, actor="user:owner", role="owner", project_id=project)
    history.undo(ctx)  # hoàn tác đổi tên → không được xoá trạng thái quy trình/gate
    with owner_ctx.services.db.session() as s:
        q = s.get(Sequence, seq)
        assert q.title == "Tập 1"
        assert q.pipeline["run"]["status"] == "running"
        assert q.gates["pilot"]["status"] == "approved"
