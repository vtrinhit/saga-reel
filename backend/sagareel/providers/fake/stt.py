"""`fake_stt`: trả `options.transcript`; thời điểm từng từ chia đều theo thời lượng file âm thanh."""

from __future__ import annotations

from typing import ClassVar

import anyio

from sagareel.media.probe import probe
from sagareel.providers.base import ConfigField, Health, STTAdapter, STTRequest, Transcript
from sagareel.providers.fake import HEALTH_FIELD, fake_health
from sagareel.providers.fake.tts import even_words
from sagareel.providers.registry import register_adapter


@register_adapter
class FakeSTTAdapter(STTAdapter):
    adapter_id = "fake_stt"
    label = "Nhận dạng lời giả (trả văn bản cố định)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("transcript", "Văn bản trả về", "str", False, "", help="Lời mà adapter giả “nghe” được."),
        HEALTH_FIELD,
    ]
    default_capabilities: ClassVar[dict] = {"word_timestamps": True}

    async def check_health(self) -> Health:
        return fake_health(self)

    async def transcribe(self, req: STTRequest, run=None) -> Transcript:
        text = str(self.conn.options.get("transcript", ""))
        if not req.word_timestamps:
            return Transcript(text=text, words=[])
        info = await anyio.to_thread.run_sync(probe, req.audio)
        return Transcript(text=text, words=even_words(text, info.duration_ms or 1000))
