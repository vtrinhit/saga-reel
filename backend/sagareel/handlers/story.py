"""Job `story.run` (lane `story`): chạy một bước AI viết (`services.story.writer.TASKS`), chữ chạy dần qua `llm.delta`."""

from __future__ import annotations

from typing import Any

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.services import jobs
from sagareel.services.llm import DELTA_EVENT, DeltaThrottle
from sagareel.services.story import writer

JOB_TYPE = "story.run"
LANE = "story"


@jobs.job_handler(JOB_TYPE)
async def story_run(run) -> dict:
    p = run.params
    task = p["task"]
    fn = writer.TASKS.get(task)
    if fn is None:
        raise jobs.JobFailed("story_task_unknown", f"Không có bước viết '{task}'.")
    services = run.services
    ctx = OpContext(services=services, actor=p.get("actor") or "user:owner", role="owner",
                    project_id=p.get("project_id"))

    def emit(text: str, **extra: Any) -> None:
        services.bus.publish(DELTA_EVENT, {"job_id": run.job_id, "task": f"story.{task}", "text": text, **extra},
                             project_id=p.get("project_id"))

    throttle = DeltaThrottle(emit)
    kwargs = dict(p.get("args") or {})
    if task == "adapt_novel":
        kwargs["progress"] = lambda pct, stage: run.progress(pct, stage=stage)
    run.progress(None, stage=f"Đang viết: {task}")
    try:
        return await fn(ctx, **kwargs, on_delta=throttle.push, log=run.log)
    except AppError as e:
        raise jobs.JobFailed(e.code, e.detail, e.hint) from e
    finally:
        throttle.flush()
