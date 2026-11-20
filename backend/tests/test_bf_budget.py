"""Task BF — ngân sách theo dự án (project.budget) trong budget.status/check/reserve, khoá confirm_below_units, ước
tính dry run theo bước + tập chưa chia clip."""

from __future__ import annotations

import anyio
import pytest
from sagareel import ops
from sagareel.core.errors import AppError
from sagareel.core.ops import invoke
from sagareel.db.models import Job, ProviderConnection, Spend
from sagareel.services import budget, entities, sequences

ops.load_all()


def _inv(name, payload, ctx):
    return anyio.run(invoke, name, payload, ctx)


@pytest.fixture
def real_conn(owner_ctx) -> int:
    with owner_ctx.services.db.session() as s:
        c = ProviderConnection(kind="image", adapter="maivideo_image", name="real-img")
        s.add(c)
        s.flush()
        return c.id


def test_project_cap_enforced_by_check_and_reserve(owner_ctx, project, real_conn):
    entities.patch(owner_ctx, "project", project, {"budget": {"images_total": 2}})
    db = owner_ctx.services.db
    with db.session() as s:
        jobs = [Job(type="generate.image", lane="x", project_id=project) for _ in range(3)]
        s.add_all(jobs)
        s.flush()
        ids = [j.id for j in jobs]
    with db.session() as s:
        budget.reserve(s, "image", 1, ids[0], real_conn)
        budget.reserve(s, "image", 1, ids[1], real_conn)
    with db.session() as s:
        assert [r.project_id for r in s.query(Spend).all()] == [project, project]
        st = budget.status(s, "image", project_id=project)
        assert st["project"] == {"project_id": project, "total": 2, "spent": 2.0, "remaining": 0.0}
        with pytest.raises(AppError) as e:
            budget.reserve(s, "image", 1, ids[2], real_conn)
        assert e.value.code == "budget_exceeded" and e.value.extra["scope"] == "project"
        budget.check(s, "image", 5, real_conn)  # không truyền dự án → chỉ trần toàn cục (60)
    out = _inv("budget.status", {"project_id": project}, owner_ctx)
    assert out["image"]["project"]["remaining"] == 0 and out["video"]["project"]["total"] is None
    assert out["confirm_below_units"] == 5
    entities.patch(owner_ctx, "project", project, {"budget": {"images_total": 2, "enabled": False}})
    with db.session() as s:
        budget.check(s, "image", 5, real_conn, project_id=project)


def test_dry_run_steps_and_unsplit_estimate(owner_ctx, project):
    sid = sequences.create(owner_ctx, project, "Tập 1", script="")["id"]
    plan = _inv("pipeline.run", {"sequence_ids": [sid], "dry_run": True}, owner_ctx)
    keys = [x["key"] for x in plan["steps"]]
    assert keys == plan["step_keys"] and "generate_images" in keys
    img = next(x for x in plan["steps"] if x["key"] == "generate_images")
    assert img["units"]["images"] > 0  # tập chưa chia clip → ước tính theo thể loại
    assert plan["sequences"][0]["clips_estimated"] is True and plan["estimate"]["clips_estimated"] is True
    assert plan["estimate"]["images"] == img["units"]["images"] and "note" in plan
    render = [x for x in plan["steps"] if x["key"].startswith("render")]
    assert all(x["units"]["renders"] == 1 for x in render)
    assert "budget" in plan["estimate"] and "project" in plan["estimate"]["budget"]["image"]
