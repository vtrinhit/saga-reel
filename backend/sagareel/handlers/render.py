"""Job `render.sequence` (lane `ffmpeg`): dựng một tập, tiếp tục theo khối sau khi app khởi động lại."""

from __future__ import annotations

from sagareel.core.errors import AppError
from sagareel.render import pipeline
from sagareel.services import jobs


@jobs.job_handler(pipeline.JOB_TYPE, resumable=True)
async def render_sequence(run) -> dict:
    p = run.params
    try:
        result = await pipeline.render(
            run.services, int(p["sequence_id"]), p.get("variant") or "draft", p.get("platform"), p.get("lang"),
            p.get("preview_seconds"),
            on_progress=lambda pct, stage: run.progress(pct, stage=stage),
            log=run.log, cancel=run.cancelled, job_id=run.job_id,
        )
    except AppError as e:
        if e.code == "ffmpeg_cancelled" and run.cancelled.is_set():
            raise
        raise jobs.JobFailed(e.code, e.detail, e.hint) from e
    return {"render_id": result["id"], "media_id": result["media_id"], "duration_ms": result["duration_ms"],
            "chunks": result["chunks"], "cached": result["cached"], "url": result["url"]}
