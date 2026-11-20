"""Thao tác nhóm `queue`: luồng — trần số việc chạy cùng lúc theo loại provider trên toàn app (`services/concurrency`).

Đổi trần là áp dụng ngay cho việc mới (worker được đánh thức, phát `queue.updated`); việc đang chạy không bị dừng.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import concurrency

Kind = Literal["image", "video", "llm", "tts", "stt", "render"]
Cap = Annotated[int, Field(ge=concurrency.MIN_CAP, le=concurrency.MAX_CAP)]


class EmptyParams(BaseModel):
    pass


class QueueSetConcurrencyParams(BaseModel):
    values: dict[Kind, Cap] = Field(
        min_length=1,
        description="Loại → trần số việc chạy cùng lúc (1–20), vd. {\"video\": 3, \"image\": 2}. Loại vắng giữ nguyên.",
    )


@op(
    "queue.concurrency",
    summary="Luồng: trần số việc chạy cùng lúc theo loại provider (ảnh/video/LLM/TTS/STT/dựng) kèm số đang chạy/chờ",
    params=EmptyParams,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
def queue_concurrency(ctx: OpContext, p: EmptyParams) -> dict:
    return concurrency.overview(ctx.services)


@op(
    "queue.set_concurrency",
    summary="Đổi trần luồng theo loại provider (1–20; áp dụng ngay cho việc mới, không dừng việc đang chạy; hoàn tác được)",
    params=QueueSetConcurrencyParams,
    idempotent=True,
    mcp="tool",
)
def queue_set_concurrency(ctx: OpContext, p: QueueSetConcurrencyParams) -> dict:
    return concurrency.set_caps(ctx, dict(p.values))
