"""Job `clip.analyze_trim` (lane `ffmpeg`): phân tích take video đang chọn để tự cắt theo nội dung (không tốn tiền)."""

from __future__ import annotations

import anyio

from sagareel.services import jobs, trimming


@jobs.job_handler(trimming.JOB_TYPE)
async def analyze_trim(run) -> dict:
    ids = [int(i) for i in run.params.get("clip_ids") or []]
    run.progress(0.0, stage="Đang phân tích video để tự cắt")
    return await anyio.to_thread.run_sync(trimming.analyze_clips, run.services, ids)
