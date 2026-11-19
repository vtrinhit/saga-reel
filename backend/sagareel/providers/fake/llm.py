"""`fake_llm`: trả lời theo `options.replies` (khoá = `params["template_key"]` hoặc `"default"`), stream theo từ.

`options.fail` giả lập lỗi provider (thử chuỗi dự phòng): `true`/`"unavailable"` → `ProviderUnavailable`,
`"transient"` → `TransientError`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import ClassVar

from sagareel.providers.base import (
    ConfigField,
    Health,
    LLMAdapter,
    LLMRequest,
    LLMResult,
    ProviderUnavailable,
    TransientError,
)
from sagareel.providers.fake import HEALTH_FIELD, fake_health
from sagareel.providers.registry import register_adapter

DEFAULT_REPLY = "Đây là câu trả lời giả của fake_llm."
_CHUNK_RE = re.compile(r"\s*\S+\s*|\s+")  # mỗi đoạn = khoảng trắng đầu + một từ + khoảng trắng sau


def chunks(text: str) -> list[str]:
    """Tách theo từ, giữ nguyên mọi khoảng trắng: `"".join(chunks(t)) == t`."""
    return _CHUNK_RE.findall(text)


@register_adapter
class FakeLLMAdapter(LLMAdapter):
    adapter_id = "fake_llm"
    label = "LLM giả (trả lời cố định)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("replies", "Câu trả lời theo template", "str", False, None,
                    help='JSON {"<template_key>|default": "câu trả lời"}; khoá lấy từ params.template_key.'),
        ConfigField("models", "Danh sách model", "str", False, None, help="Mặc định [\"fake-llm\"]."),
        ConfigField("delay_ms", "Trễ mỗi từ (ms)", "int", False, 0),
        ConfigField("fail", "Giả lập lỗi", "str", False, None,
                    help='true/"unavailable" → provider không dùng được; "transient" → lỗi tạm thời.'),
        HEALTH_FIELD,
    ]
    default_capabilities: ClassVar[dict] = {"vision": True, "json_mode": True, "stream": True}

    async def check_health(self) -> Health:
        return fake_health(self)

    async def estimate(self, req: LLMRequest) -> dict:
        return {"units": 1, "cost": 0}

    async def list_models(self) -> list[str]:
        models = self.conn.options.get("models")
        return list(models) if isinstance(models, list) and models else ["fake-llm"]

    async def chat(self, req: LLMRequest, on_delta: Callable[[str], None] | None = None) -> LLMResult:
        fail = self.conn.options.get("fail")
        if fail == "transient":
            raise TransientError(f"LLM giả '{self.conn.name}' đang giả lập lỗi tạm thời.", retry_after=0)
        if fail is True or fail == "unavailable":
            raise ProviderUnavailable(f"LLM giả '{self.conn.name}' đang giả lập lỗi không dùng được.")
        replies = self.conn.options.get("replies") or {}
        key = req.params.get("template_key")
        text = replies.get(key) if key is not None and key in replies else replies.get("default", DEFAULT_REPLY)
        text = str(text)
        delay = float(self.conn.options.get("delay_ms", 0)) / 1000
        parts = chunks(text)
        for part in parts:
            if on_delta is not None:
                on_delta(part)
            if delay:
                await asyncio.sleep(delay)
        prompt_chars = sum(len(str(m.get("content", ""))) for m in req.messages)
        return LLMResult(
            text=text,
            model=req.model or self.conn.default_model or "fake-llm",
            usage={"prompt_tokens": prompt_chars // 4, "completion_tokens": len(parts), "images": len(req.images)},
        )
