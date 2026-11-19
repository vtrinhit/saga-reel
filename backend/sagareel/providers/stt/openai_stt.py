"""`openai_stt`: nhận dạng lời qua API tương thích OpenAI — `POST {base}/audio/transcriptions` (multipart).

Trường: `file`, `model`, `response_format=verbose_json`, `timestamp_granularities[]=word`, `language` (nếu có). Từ
trả về tính bằng giây → ms; chữ đầu ra chuẩn hoá NFC. Chưa có kết nối STT nào thì `provider.try_stt` trả
`stt_not_configured` (op). Sức khoẻ, dấu "submitting" và cách ánh xạ lỗi: xem `providers/openai_audio.py`.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import anyio
import httpx

from sagareel.providers import audio_util
from sagareel.providers.base import ConfigField, ProviderJobError, STTAdapter, STTRequest, Transcript
from sagareel.providers.openai_audio import HEALTH_SPEECH_FIELD, OpenAIAudioMixin
from sagareel.providers.registry import register_adapter
from sagareel.services.media import guess_mime

if TYPE_CHECKING:
    from sagareel.services.worker import JobRun

DEFAULT_MODEL = "whisper-1"


def silent_wav(ms: int = 300, rate: int = 16_000) -> bytes:
    """WAV im lặng (mono 16 bit) cho yêu cầu thử sức khoẻ — tạo trong bộ nhớ, không cần ffmpeg."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * (rate * ms // 1000))
    return buf.getvalue()


def _ms(value) -> int:
    return round(float(value) * 1000)


@register_adapter
class OpenAISTTAdapter(OpenAIAudioMixin, STTAdapter):
    adapter_id = "openai_stt"
    label = "Nhận dạng lời qua API tương thích OpenAI"
    service_label = "nhận dạng lời (STT)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("base_url", "Địa chỉ API", "url", True, help="Gồm cả /v1, vd. https://api.openai.com/v1"),
        ConfigField("api_key", "Khoá API", "secret", False),
        ConfigField("default_model", "Model", "str", False, DEFAULT_MODEL),
        HEALTH_SPEECH_FIELD,
    ]
    default_capabilities: ClassVar[dict] = {"word_timestamps": True}
    http_timeout: ClassVar[float] = 300.0

    def _model(self) -> str:
        return self.conn.default_model or DEFAULT_MODEL

    def _form(self, lang: str | None, words: bool) -> dict[str, str]:
        data = {"model": self._model(), "response_format": "verbose_json"}
        if words:
            data["timestamp_granularities[]"] = "word"
        if lang:
            data["language"] = lang
        return data

    async def _probe_request(self, c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(self._url("audio/transcriptions"), headers=self._headers(), data=self._form(None, False),
                            files={"file": ("probe.wav", silent_wav(), "audio/wav")})

    async def transcribe(self, req: STTRequest, run: JobRun | None = None) -> Transcript:
        audio = Path(req.audio)
        content = await anyio.to_thread.run_sync(audio.read_bytes)
        form = self._form(req.lang, req.word_timestamps)
        r = await self._paid_post(run, lambda c: c.post(
            self._url("audio/transcriptions"), headers=self._headers(), data=form,
            files={"file": (audio.name, content, guess_mime(audio))}, timeout=self.http_timeout,
        ))
        try:
            body = r.json()
            text = audio_util.nfc(str(body.get("text", "")))
            words = [(audio_util.nfc(str(w["word"])), _ms(w["start"]), _ms(w["end"]))
                     for w in (body.get("words") or [])] if req.word_timestamps else []
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise ProviderJobError("bad_response", "Máy chủ STT trả kết quả không đúng định dạng verbose_json.",
                                   hint="Kiểm tra model có hỗ trợ verbose_json và timestamp theo từ.") from e
        return Transcript(text=text, words=words)
