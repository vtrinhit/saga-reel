"""Job `channel.analyze` và `clone.from_video` (LLM vision qua `services.llm.run_task`)."""

from __future__ import annotations

from sagareel.core.errors import AppError
from sagareel.services import jobs, llm
from sagareel.services import provider_jobs as pj
from sagareel.services.channel import analysis, clone


@pj.provider_job(analysis.JOB_TYPE, secrets=llm._all_api_keys)
async def channel_analyze(run) -> dict:
    p = run.params
    try:
        return await analysis.analyze_study(
            run.services, int(p["study_id"]), threshold=float(p.get("scene_threshold", 0.3)),
            min_scene_s=float(p.get("min_scene_s", 0.5)), max_frames=int(p.get("max_frames", 16)),
            progress=lambda pct, stage: run.progress(pct, stage=stage), log=run.log, cancel=run.cancelled)
    except AppError as e:
        if e.code == "ffmpeg_cancelled" and run.cancelled.is_set():
            raise
        raise jobs.JobFailed(e.code, e.detail, e.hint) from e


@pj.provider_job(clone.JOB_TYPE, secrets=llm._all_api_keys)
async def clone_from_video(run) -> dict:
    try:
        return await clone.run(run.services, run.params, progress=lambda pct, stage: run.progress(pct, stage=stage),
                               log=run.log, cancel=run.cancelled, job_id=run.job_id)
    except AppError as e:
        if e.code == "ffmpeg_cancelled" and run.cancelled.is_set():
            raise
        raise jobs.JobFailed(e.code, e.detail, e.hint) from e
