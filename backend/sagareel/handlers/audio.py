"""Job `audio.tts`: đọc một câu thoại (hoặc câu nghe thử) qua adapter TTS, chuẩn hoá −16 LUFS, lưu media + take.

`params` (do `services.audio.dialogue.submit_tts` tạo, không chứa khoá API): `connection_id`, `text` (gửi nguyên văn),
`voice`, `rate`, `pitch`, `volume`, `lang`, `primary`, `target_lufs`, `cache_key`, `dialogue_id` (None = nghe thử).

Các bước: synthesize → mốc từ (word-boundary của provider, căn theo từ của câu; không có thì ước lượng theo âm tiết)
→ `mix.normalize_line` (loudnorm hai lượt linear, < 3 s dùng gain) → `_store` media (ngoài transaction) → một UoW ghi
take (`owner_type=dialogue, part=audio`) + `dialogue_line.audio_take_id` (có lịch sử).
"""

from __future__ import annotations

import time
from typing import Any

import anyio

from sagareel.core.context import OpContext
from sagareel.db.models import Media
from sagareel.providers.base import TTSRequest
from sagareel.services import entities
from sagareel.services.audio import dialogue, mix, timing
from sagareel.services.jobs import (
    json_safe,  # -inf LUFS (câu rất ngắn) → null: JSON chuẩn
)
from sagareel.services.provider_jobs import _adapter, _plain, _store, provider_job

TTS_JOB = dialogue.TTS_JOB
OUT_NAME = "line.m4a"


@provider_job(TTS_JOB)
async def audio_tts(run: Any) -> dict:
    p = run.params
    t0 = time.monotonic()
    adapter = await _adapter(run, "tts")
    raw = run.workdir / "raw.mp3"
    run.progress(0.0, stage="Đang đọc lời thoại")
    req = TTSRequest(text=p["text"], voice=p["voice"], rate=p.get("rate") or "+0%", pitch=p.get("pitch") or "+0Hz",
                     volume=p.get("volume") or "+0%", lang=p.get("lang") or "vi", out_path=raw)
    result = await adapter.synthesize(req, run)
    wt = timing.word_timings_or_estimate(p["text"], result.duration_ms, result.words)
    if wt["estimated"]:
        run.log("warning", "Provider không trả mốc từng từ: phụ đề dùng thời điểm ước lượng theo âm tiết.")
    run.progress(60.0, stage="Đang chuẩn hoá độ to (−16 LUFS)")
    out = run.workdir / OUT_NAME
    loud = await anyio.to_thread.run_sync(lambda: mix.normalize_line(raw, out, target_i=float(p["target_lufs"])))
    for w in loud["warnings"]:
        run.log("warning", w["message"])
    inputs = json_safe(_plain({
        "text": p["text"], "voice": p["voice"], "rate": req.rate, "pitch": req.pitch, "volume": req.volume,
        "connection": p.get("connection"), "connection_id": adapter.conn.id, "adapter": adapter.adapter_id,
        "lang": req.lang, "cache_key": p["cache_key"], "target_lufs": p["target_lufs"],
        "profile_id": p.get("profile_id"), "words": wt["words"], "timing_estimated": wt["estimated"],
        "words_interpolated": wt["interpolated"], "raw_duration_ms": result.duration_ms,
        "loudness": {k: loud.get(k) for k in ("method", "normalization_type", "gain_db", "input", "output")},
        "warnings": loud["warnings"],
    }))
    meta = {"dialogue_id": p.get("dialogue_id"), "job_id": run.job_id, **inputs}
    (media_id,) = await _store(run, adapter, [out], kind="audio", meta=meta, spend_kind=None)
    elapsed = round((time.monotonic() - t0) * 1000)

    def write() -> dict:
        with run.services.db.session() as s:
            m = s.get(Media, media_id)
            duration = m.duration_ms if m is not None else None
        take_id = None
        if p.get("dialogue_id") is not None:
            ctx = OpContext(services=run.services, actor="system:audio.tts", role="owner")
            u = ctx.uow("Lưu âm thanh lời thoại", op_name=TTS_JOB)
            with u as s:
                line = s.get(entities.ENTITIES["dialogue_line"], p["dialogue_id"])
                if line is not None:
                    u.project_id = entities.project_id_of(s, line)
                take = dialogue.record_take(s, dialogue_id=int(p["dialogue_id"]), lang=req.lang,
                                            primary=bool(p.get("primary")), media_id=media_id, job_id=run.job_id,
                                            inputs=inputs, elapsed_ms=elapsed)
                take_id = take.id
        return {"duration_ms": duration, "take_id": take_id}

    rec = await anyio.to_thread.run_sync(write)
    run.progress(100.0, stage="Xong")
    return {"media_ids": [media_id], "take_id": rec["take_id"], "dialogue_id": p.get("dialogue_id"),
            "duration_ms": rec["duration_ms"], "words": inputs["words"], "timing_estimated": wt["estimated"],
            "loudness": inputs["loudness"], "connection_id": adapter.conn.id, "adapter": adapter.adapter_id}
