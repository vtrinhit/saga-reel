"""Thao tác nhóm `take`: liệt kê, chọn, chấm điểm, phán quyết, ghi chú, so sánh, "giống take này nhưng…".

Take không bao giờ bị ghi đè/xoá (spec §10)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import last_change_set_id, op
from sagareel.services import takes


class TakeListParams(BaseModel):
    owner_type: Literal["clip", "variant", "dialogue"] = "clip"
    owner_id: int
    part: Literal["image", "video", "audio", "sheet"] | None = None


class TakeIdParams(BaseModel):
    take_id: int


class TakeRateParams(BaseModel):
    take_id: int
    rating: int | None = Field(default=None, ge=0, le=5, description="0–5; null = bỏ điểm")


class TakeVerdictParams(BaseModel):
    take_id: int
    verdict: Literal["keep", "trim", "fix_post", "regenerate", "reject"] | None = Field(
        description="Phán quyết; null = xoá phán quyết (và lý do)")
    reason: str | None = Field(default=None, description="Lý do (vd. 'tay lỗi'); 2 lần cùng lý do → gợi ý đổi cách")


class TakeNoteParams(BaseModel):
    take_id: int
    notes: str


class TakeRegenerateLikeParams(BaseModel):
    take_id: int
    changes: dict[str, Any] = Field(default_factory=dict, description="Ghi đè như clip.regenerate overrides "
                                    "(prompt, refs, model, duration, connection, takes…)")
    dry_run: bool = False


class TakeCompareParams(BaseModel):
    take_ids: list[int] = Field(min_length=2, max_length=12)


@op("take.list", summary="Liệt kê take của clip/look/lời thoại (mới nhất trước) + take đang chọn",
    params=TakeListParams, read_only=True, idempotent=True, mcp="tool")
def take_list(ctx: OpContext, p: TakeListParams) -> dict:
    return takes.list_takes(ctx, p.owner_type, p.owner_id, p.part)


@op("take.select", summary="Chọn take làm đầu ra hiện tại (áp bảng làm-cũ)", params=TakeIdParams, idempotent=True,
    mcp="tool")
def take_select(ctx: OpContext, p: TakeIdParams) -> dict:
    return {**takes.select_take(ctx, p.take_id), "change_set_id": last_change_set_id()}


@op("take.rate", summary="Chấm điểm take (0–5)", params=TakeRateParams, idempotent=True, mcp="tool")
def take_rate(ctx: OpContext, p: TakeRateParams) -> dict:
    return takes.rate(ctx, p.take_id, p.rating)


@op("take.verdict", summary="Phán quyết take (keep/trim/fix_post/regenerate/reject) kèm lý do; null = xoá "
    "(hoàn tác được)", params=TakeVerdictParams, idempotent=True, mcp="tool")
def take_verdict(ctx: OpContext, p: TakeVerdictParams) -> dict:
    return {**takes.verdict(ctx, p.take_id, p.verdict, p.reason), "change_set_id": last_change_set_id()}


@op("take.note", summary="Ghi chú cho take", params=TakeNoteParams, idempotent=True)
def take_note(ctx: OpContext, p: TakeNoteParams) -> dict:
    return takes.note(ctx, p.take_id, p.notes)


@op("take.regenerate_like", summary="Tạo take mới giống take này nhưng đổi vài thứ (không ghi đè)",
    params=TakeRegenerateLikeParams, costly=True, long_running=True, mcp="tool")
async def take_regenerate_like(ctx: OpContext, p: TakeRegenerateLikeParams) -> dict:
    return await takes.regenerate_like(ctx, p.take_id, p.changes, p.dry_run)


@op("take.compare", summary="So sánh các take cạnh nhau (media + đầu vào khác nhau)", params=TakeCompareParams,
    read_only=True, idempotent=True)
def take_compare(ctx: OpContext, p: TakeCompareParams) -> dict:
    return takes.compare(ctx, p.take_ids)
