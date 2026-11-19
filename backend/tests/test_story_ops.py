"""Op `story.*` → job `story.run` qua `run_task` THẬT + adapter `fake_llm` (template story_* đã seed vào DB)."""

from __future__ import annotations

import asyncio
import json

import anyio
import pytest
from sagareel.core.errors import AppError
from sagareel.core.ops import invoke
from sagareel.db.models import Asset, Job, ProviderConnection
from sagareel.ops import load_all
from sagareel.services import projects
from sagareel.services.seed import seed_defaults
from sqlalchemy import select
from test_story_writer import BIBLE

load_all()


async def wait_job(services, job_id: int, timeout: float = 15.0) -> Job:
    for _ in range(int(timeout / 0.05)):
        def get():
            with services.db.session() as s:
                j = s.get(Job, job_id)
                s.expunge(j)
                return j

        j = await anyio.to_thread.run_sync(get)
        if j.status in ("succeeded", "failed", "cancelled"):
            return j
        await asyncio.sleep(0.05)
    raise AssertionError("job chưa xong")


async def test_story_write_bible_job_with_real_routing(services, owner_ctx):
    seed_defaults(services.db)
    pid = projects.create(owner_ctx, name="P", key="p", genre_key="serial")["id"]
    with pytest.raises(AppError) as e:
        await invoke("story.write_bible", {"project_id": pid, "idea": "x"}, owner_ctx)
    assert e.value.code == "llm_not_configured" and "agent" in e.value.extra["alternatives"]
    with services.db.session() as s:
        s.add(ProviderConnection(kind="llm", adapter="fake_llm", name="fake", concurrency=2, enabled=True,
                                 options={"replies": {"story_write_bible": json.dumps(BIBLE, ensure_ascii=False)}}))
    events = []
    orig = services.bus.publish

    def publish(t, d, project_id=None):
        events.append((t, d))
        orig(t, d, project_id=project_id)

    services.bus.publish = publish
    job = await invoke("story.write_bible", {"project_id": pid, "idea": "Cô gái tìm nhẫn"}, owner_ctx)
    assert job["type"] == "story.run" and job["lane"] == "story"
    await services.worker.start()
    try:
        j = await wait_job(services, job["id"])
    finally:
        await services.worker.stop()
    assert j.status == "succeeded", (j.error_code, j.error_message)
    assert j.result["counts"]["asset_created"] == 4 and j.result["model"] == "fake-llm"
    assert any(t == "llm.delta" and d["task"] == "story.write_bible" for t, d in events)
    with services.db.session() as s:
        assert {a.key for a in s.scalars(select(Asset).where(Asset.project_id == pid))} == \
            {"linh", "minh", "villa", "ring"}


async def test_story_op_param_validation(services, owner_ctx):
    with pytest.raises(AppError) as e:
        await invoke("story.outline", {"project_id": 1}, owner_ctx)
    assert e.value.code == "validation_failed"
    with pytest.raises(AppError):
        await invoke("story.image_prompts", {}, owner_ctx)
    with pytest.raises(AppError):
        await invoke("story.adapt_novel", {"project_id": 1}, owner_ctx)
