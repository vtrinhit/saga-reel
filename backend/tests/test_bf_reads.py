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
