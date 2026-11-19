"""`openai_tts`: đọc lời qua API tương thích OpenAI — `POST {base}/audio/speech`.

Body: `{"model", "input", "voice", "response_format": "mp3", "speed"}`; `input` gửi **nguyên văn**. API không trả thời
điểm từng từ (`words=[]`). Sức khoẻ, dấu "submitting" và cách ánh xạ lỗi: xem `providers/openai_audio.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import anyio
import httpx

from sagareel.providers import audio_util
from sagareel.providers.base import ConfigField, TTSAdapter, TTSRequest, TTSResult
from sagareel.providers.openai_audio import HEALTH_SPEECH_FIELD, OpenAIAudioMixin
from sagareel.providers.registry import register_adapter

if TYPE_CHECKING:
    from sagareel.services.worker import JobRun

DEFAULT_MODEL = "tts-1"
DEFAULT_VOICES = ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"]


def _finish(out: Path, audio: bytes) -> int:
    audio_util.write_atomic(out, audio)
    return audio_util.duration_ms(out)


@register_adapter
class OpenAITTSAdapter(OpenAIAudioMixin, TTSAdapter):
    adapter_id = "openai_tts"
    label = "Đọc lời qua API tương thích OpenAI"
    service_label = "đọc lời (TTS)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("base_url", "Địa chỉ API", "url", True, help="Gồm cả /v1, vd. http://localhost:20128/v1"),
        ConfigField("api_key", "Khoá API", "secret", False),
        ConfigField("default_model", "Model", "str", False, DEFAULT_MODEL),
        ConfigField("speed", "Tốc độ đọc", "str", False, 1.0, help="Số từ 0.25 đến 4.0 (mặc định 1.0)."),
        ConfigField("voices", "Danh sách giọng", "str", False, None,
                    help=f"Danh sách tên giọng máy chủ hỗ trợ; mặc định {DEFAULT_VOICES}."),
        HEALTH_SPEECH_FIELD,
    ]
    default_capabilities: ClassVar[dict] = {"word_timings": False}

    def _model(self) -> str:
        return self.conn.default_model or DEFAULT_MODEL

    def _speed(self) -> float:
        try:
            return float(self.conn.options.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            return 1.0

    async def voices(self, lang: str | None = None) -> list[dict]:
        names = self.conn.options.get("voices")
        names = list(names) if isinstance(names, list) and names else DEFAULT_VOICES
        # Giọng OpenAI đọc được nhiều ngôn ngữ: không lọc theo lang.
        return [{"id": str(n), "label": str(n), "lang": "multi"} for n in names]

    async def estimate(self, req: TTSRequest) -> dict:
        return {"units": 1, "chars": len(req.text)}

    async def _probe_request(self, c: httpx.AsyncClient) -> httpx.Response:
        voices = await self.voices()
        return await c.post(self._url("audio/speech"), headers=self._headers(), json={
            "model": self._model(), "input": "a", "voice": voices[0]["id"], "response_format": "mp3",
        })

    async def synthesize(self, req: TTSRequest, run: JobRun | None = None) -> TTSResult:
        body = {"model": self._model(), "input": req.text, "voice": req.voice, "response_format": "mp3",
                "speed": self._speed()}
        r = await self._paid_post(run, lambda c: c.post(self._url("audio/speech"), headers=self._headers(),
                                                          json=body, timeout=self.http_timeout))
        out = Path(req.out_path)
        duration = await anyio.to_thread.run_sync(_finish, out, r.content)
        return TTSResult(path=out, duration_ms=duration, words=[])
