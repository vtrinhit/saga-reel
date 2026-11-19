"""`fake_tts`: tiếng sine dài 70 ms/ký tự, thời điểm từng từ chia đều."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from sagareel.media import ffmpeg
from sagareel.providers.base import ConfigField, Health, TTSAdapter, TTSRequest, TTSResult
from sagareel.providers.fake import HEALTH_FIELD, fake_health
from sagareel.providers.registry import register_adapter

MS_PER_CHAR = 70
VOICES = [
    {"id": "fake-vi-female", "label": "Giọng nữ giả", "lang": "vi", "gender": "female"},
    {"id": "fake-vi-male", "label": "Giọng nam giả", "lang": "vi", "gender": "male"},
    {"id": "fake-en-female", "label": "Giọng nữ giả (tiếng Anh)", "lang": "en", "gender": "female"},
]


def even_words(text: str, duration_ms: int) -> list[tuple[str, int, int]]:
    words = text.split()
    if not words:
        return []
    n = len(words)
    return [(w, round(duration_ms * i / n), round(duration_ms * (i + 1) / n)) for i, w in enumerate(words)]


@register_adapter
class FakeTTSAdapter(TTSAdapter):
    adapter_id = "fake_tts"
    label = "Đọc giả (tiếng sine)"
    config_fields: ClassVar[list[ConfigField]] = [HEALTH_FIELD]
    default_capabilities: ClassVar[dict] = {"langs": ["vi", "en"], "word_timings": True}

    async def check_health(self) -> Health:
        return fake_health(self)

    async def estimate(self, req: TTSRequest) -> dict:
        return {"units": 1, "chars": len(req.text), "seconds": MS_PER_CHAR * len(req.text) / 1000, "cost": 0}

    async def voices(self, lang: str | None = None) -> list[dict]:
        return [dict(v) for v in VOICES if lang is None or v["lang"] == lang]

    async def synthesize(self, req: TTSRequest, run=None) -> TTSResult:
        duration_ms = max(MS_PER_CHAR, MS_PER_CHAR * len(req.text))
        out = Path(req.out_path)
        with ffmpeg.atomic_output(out) as part:
            await ffmpeg.run(
                ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=24000:duration={duration_ms / 1000:.3f}",
                 "-ac", "1", str(part)],
                timeout=120,
                cancel=run.cancelled if run is not None else None,
            )
        words = even_words(req.text, duration_ms) if req.word_timings else []
        return TTSResult(path=out, duration_ms=duration_ms, words=words)
