"""Job `audio.translate_dub` (lane `story`): dịch lời thoại của tập sang ngôn ngữ khác rồi xếp job TTS."""

from __future__ import annotations

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.services import jobs
from sagareel.services.audio import dub


@jobs.job_handler(dub.JOB_TYPE)
async def audio_translate_dub(run) -> dict:
    p = run.params
    ctx = OpContext(services=run.services, actor=p.get("actor") or "user:owner", role="owner",
                    project_id=p.get("project_id"))
    try:
        return await dub.run_dub(ctx, int(p["sequence_id"]), p["lang"], force=bool(p.get("force")),
                                 tts=bool(p.get("tts", True)), progress=run.progress)
    except AppError as e:
        raise jobs.JobFailed(e.code, e.detail, e.hint) from e
