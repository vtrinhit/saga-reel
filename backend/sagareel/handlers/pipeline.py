"""Job `pipeline.run` (lane `pipeline`): chạy quy trình của một tập; nối lại được (trạng thái ở `sequence.pipeline`)."""

from __future__ import annotations

from sagareel.services import jobs
from sagareel.services.pipeline import engine


@jobs.job_handler(engine.JOB_TYPE, resumable=True)
async def pipeline_run(run) -> dict:
    return await engine.advance(run.services, int(run.params["sequence_id"]), job_id=run.job_id, log=run.log,
                                progress=run.progress)
