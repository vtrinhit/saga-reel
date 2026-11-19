"""Task BF — op đọc cho UI: history.list lọc actor, pipeline.status toàn cục + media xem trước gate, đếm, enum…"""

from __future__ import annotations

import dataclasses

import anyio
import pytest
from sagareel import ops
from sagareel.core.ops import invoke
from sagareel.db.models import Render
from sagareel.services import clips, history, projects, sequences
from sagareel.services.story import state

ops.load_all()


@pytest.fixture
def seq(owner_ctx, project) -> int:
    return sequences.create(owner_ctx, project, "Tập 1")["id"]


def _inv(name, payload, ctx):
    return anyio.run(invoke, name, payload, ctx)


def test_history_list_actor_exact_and_prefix(owner_ctx, project, seq):
    bot = dataclasses.replace(owner_ctx, actor="agent:my_bot")
    other = dataclasses.replace(owner_ctx, actor="agent:myxbot")
    clips.create(owner_ctx, seq, {"title": "u"})
    clips.create(bot, seq, {"title": "b"})
    clips.create(other, seq, {"title": "o"})
    ctx = dataclasses.replace(owner_ctx, project_id=project)
    assert {c.actor for c in history.list(ctx, actor="agent:").items} == {"agent:my_bot", "agent:myxbot"}
    assert {c.actor for c in history.list(ctx, actor="agent:my_bot").items} == {"agent:my_bot"}
    assert {c.actor for c in history.list(ctx, actor="user:").items} == {"user:owner"}
    out = _inv("history.list", {"project_id": project, "actor": "agent:my_"}, owner_ctx)  # không ':' cuối = đúng tên
    assert out["items"] == []


def test_pipeline_status_global_paginated_with_gate_preview(owner_ctx, project, seq, make_clip_tiny):
    p2 = projects.create(owner_ctx, name="Khác", key="khac")["id"]
    s2 = sequences.create(owner_ctx, p2, "Tập A")["id"]
    db = owner_ctx.services.db
    state.replace(db, seq, "run", {"status": "waiting_review", "steps": [], "waiting": {"gate": "animatic"}})
    mid = make_clip_tiny(db, project)
    with db.session() as s:
        s.add(Render(sequence_id=seq, variant="draft", status="succeeded", media_id=mid))
    out = _inv("pipeline.status", {}, owner_ctx)
    ids = [x["sequence_id"] for x in out["items"]]
    assert seq in ids and s2 in ids and out["next_cursor"] is None
    item = next(x for x in out["items"] if x["sequence_id"] == seq)
    assert item["project_id"] == project and item["preview"]["media_id"] == mid
    page = _inv("pipeline.status", {"limit": 1}, owner_ctx)
    assert len(page["items"]) == 1 and page["next_cursor"] == page["items"][0]["sequence_id"]
    active = _inv("pipeline.status", {"active_only": True}, owner_ctx)
    assert [x["sequence_id"] for x in active["items"]] == [seq]


def test_counts_pending_flags_and_review_queue_match(owner_ctx, project, seq):
    from _fixtures_b import add_take
    from sagareel.db.models import Clip

    db = owner_ctx.services.db
    s2 = sequences.create(owner_ctx, project, "Tập 2")["id"]
    a = clips.create(owner_ctx, seq, {})
    b = clips.create(owner_ctx, seq, {})
    c = clips.create(owner_ctx, s2, {})
    clips.create(owner_ctx, s2, {})  # chưa có take → không tính chờ duyệt
    for cl in (a, b, c):
        t = add_take(db, cl["id"], "image", media_id=1)
        with db.session() as s:
            s.get(Clip, cl["id"]).image_take_id = t
    with db.session() as s:
        s.get(Clip, b["id"]).flags = {"drift": {"score": 10}, "stale_video": True}
        s.get(Clip, a["id"]).flags = {"failed": {"part": "video"}}
    lst = _inv("sequence.list", {"project_id": project}, owner_ctx)["items"]
    by = {x["id"]: x["counts"] for x in lst}
    assert by[seq]["takes_pending"] == 2 and by[s2]["takes_pending"] == 1
    assert by[seq]["flags"] == {"stale": 1, "drift": 1, "failed": 1}
    pc = _inv("project.get", {"id": project}, owner_ctx)["counts"]
    assert pc["takes_pending"] == 3 and pc["drift"] == 1
    q = _inv("review.queue", {"sequence_ids": [s2, seq], "filter": {"has_take": True}, "limit": 2}, owner_ctx)
    assert [x["id"] for x in q["items"]] == [a["id"], b["id"]] and q["next_cursor"]
    q2 = _inv("review.queue", {"sequence_ids": [s2, seq], "filter": {"has_take": True}, "limit": 2,
                               "cursor": q["next_cursor"]}, owner_ctx)
    assert [x["id"] for x in q2["items"]] == [c["id"]] and q2["items"][0]["sequence_id"] == s2
    cost = _inv("sequence.cost_summary", {"sequence_ids": [seq, s2]}, owner_ctx)
    assert len(cost["items"]) == 2 and cost["takes"] == 3


def test_scene_list_meta_enums_asset_counts(owner_ctx, project, seq):
    from sagareel.db.models import Clip
    from sagareel.services import library

    sc = _inv("scene.create", {"sequence_id": seq, "title": "Mở"}, owner_ctx)
    clips.create(owner_ctx, seq, {"duration_ms": 3000}, scene_id=sc["id"])
    clips.create(owner_ctx, seq, {"duration_ms": 2000})
    out = _inv("scene.list", {"sequence_id": seq}, owner_ctx)
    assert out["items"][0]["counts"] == {"clips": 1, "duration_ms": 3000}
    assert out["unassigned"] == {"clips": 1, "duration_ms": 2000}
    en = _inv("meta.enums", {}, owner_ctx)
    assert en["shot"]["shot_size"][0] == "ews" and "keep" in en["verdicts"] and "still_motion" in en["engines"]
    asset = library.asset_create(owner_ctx, project, "character", "An")
    look = asset["looks"][0]["id"]
    cl = clips.create(owner_ctx, seq, {"assets": [{"asset_id": asset["id"], "variant_id": look}]})
    with owner_ctx.services.db.session() as s:
        s.get(Clip, cl["id"]).flags = {"drift": {"score": 5}}
    items = _inv("asset.list", {"project_id": project}, owner_ctx)["items"]
    a = next(x for x in items if x["id"] == asset["id"])
    assert a["counts"] == {"looks": 1, "drift": 1} and a["looks"][0]["counts"] == {"drift": 1}


def test_job_queue_position(owner_ctx):
    from sagareel.services import jobs

    with owner_ctx.services.db.session() as s:
        a = jobs.enqueue(s, "test.x", {}, lane="L1")
        b = jobs.enqueue(s, "test.x", {}, lane="L1")
        c = jobs.enqueue(s, "test.x", {}, lane="L1", priority=5)
        d = jobs.enqueue(s, "test.x", {}, lane="L2")
        ids = (a.id, b.id, c.id, d.id)
    out = {j["id"]: j["queue_position"] for j in _inv("job.list", {}, owner_ctx)["items"]}
    assert (out[ids[2]], out[ids[0]], out[ids[1]], out[ids[3]]) == (1, 2, 3, 1)
    assert _inv("job.get", {"id": ids[1]}, owner_ctx)["queue_position"] == 3
