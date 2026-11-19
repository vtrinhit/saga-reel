"""Thao tác nhóm `bible` (Story Bible có bằng chứng, spec §23.9): tìm FTS không dấu, CRUD, xác nhận/từ chối."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.ops.project_ops import DeletePreviewOut
from sagareel.services.story import bible

ItemType = Literal["character", "location", "relationship", "event", "storyline", "fact", "open_question"]
Status = Literal["pending", "confirmed", "rejected"]
Source = Literal["script", "novel", "manual", "llm"]


class EvidenceIn(BaseModel):
    quote: str = Field(min_length=1, description="Câu trích nguyên văn làm bằng chứng")
    source: Source = "manual"
    sequence_id: int | None = None
    start: int | None = Field(default=None, description="Vị trí bắt đầu (ký tự) trong kịch bản/truyện")
    end: int | None = None


class BibleSearchParams(BaseModel):
    project_id: int
    q: str = Field(min_length=1, description="Từ khoá; có dấu hay không dấu đều được (đ = d)")
    types: list[ItemType] | None = None
    statuses: list[Status] | None = Field(default=None, description="Mặc định pending + confirmed")
    limit: int = Field(default=20, ge=1, le=200)


class BibleListParams(BaseModel):
    project_id: int
    type: ItemType | None = None
    status: Status | None = None
    locked: bool | None = None
    cursor: int | None = None
    limit: int = Field(default=200, ge=1, le=500)


class BibleIdParams(BaseModel):
    id: int


class BibleCreateParams(BaseModel):
    project_id: int
    type: ItemType
    summary: str = Field(min_length=1)
    key: str | None = Field(default=None, description="Bỏ trống = tự sinh từ summary")
    attributes: dict[str, Any] = Field(default_factory=dict, description="vd. {traits: [...], age: 25}")
    status: Status = "confirmed"
    locked: bool = Field(default=False, description="Khoá: LLM không được sửa (nhãn nhân thiết)")
    story_order: int | None = Field(default=None, description="Số tập/cảnh nơi sự việc xảy ra")
    asset_id: int | None = None
    evidence: list[EvidenceIn] = Field(default_factory=list)


class BibleUpdateParams(BaseModel):
    id: int
    values: dict[str, Any] = Field(description="type/key/summary/attributes/status/locked/story_order/asset_id")
    expected_updated_at: datetime | None = None


class BibleIdsParams(BaseModel):
    ids: list[int] = Field(min_length=1)
    lock: bool | None = Field(default=None, description="Đồng thời khoá/mở khoá các mục")


class BibleDeletePreviewParams(BaseModel):
    ids: list[int] = Field(min_length=1)


class BibleDeleteParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ bible.delete_preview")


class EvidenceAddParams(EvidenceIn):
    item_id: int


@op("bible.search", summary="Tìm trong Story Bible (FTS5, bỏ dấu + đ→d) theo tóm tắt và bằng chứng",
    params=BibleSearchParams, read_only=True, idempotent=True, mcp="tool")
def bible_search(ctx: OpContext, p: BibleSearchParams) -> dict:
    return bible.search(ctx, p.project_id, p.q, types=p.types, statuses=p.statuses, limit=p.limit)


@op("bible.list", summary="Liệt kê mục Story Bible của dự án (lọc loại/trạng thái/khoá)", params=BibleListParams,
    read_only=True, idempotent=True)
def bible_list(ctx: OpContext, p: BibleListParams) -> dict:
    return bible.list_items(ctx, p.project_id, type_=p.type, status=p.status, locked=p.locked, cursor=p.cursor,
                            limit=p.limit)


@op("bible.get", summary="Đọc một mục Story Bible kèm bằng chứng", params=BibleIdParams, read_only=True,
    idempotent=True)
def bible_get(ctx: OpContext, p: BibleIdParams) -> dict:
    return bible.get(ctx, p.id)


@op("bible.create", summary="Thêm mục Story Bible (nhân vật/bối cảnh/quan hệ/sự kiện/tuyến/sự thật/câu hỏi mở)",
    params=BibleCreateParams, mcp="tool")
def bible_create(ctx: OpContext, p: BibleCreateParams) -> dict:
    return bible.create(ctx, p.project_id, p.type, p.summary, key=p.key, attributes=p.attributes, status=p.status,
                        locked=p.locked, story_order=p.story_order, asset_id=p.asset_id,
                        evidence=[e.model_dump() for e in p.evidence])


@op("bible.update", summary="Sửa mục Story Bible (hoàn tác được; expected_updated_at chống ghi đè)",
    params=BibleUpdateParams, idempotent=True)
def bible_update(ctx: OpContext, p: BibleUpdateParams) -> dict:
    return bible.update(ctx, p.id, p.values, p.expected_updated_at)


@op("bible.confirm", summary="Xác nhận các mục Story Bible đang chờ (tuỳ chọn khoá)", params=BibleIdsParams,
    idempotent=True, mcp="tool")
def bible_confirm(ctx: OpContext, p: BibleIdsParams) -> dict:
    return bible.set_status(ctx, p.ids, "confirmed", lock=p.lock)


@op("bible.reject", summary="Từ chối các mục Story Bible LLM đề xuất (không dùng khi viết)", params=BibleIdsParams,
    idempotent=True, mcp="tool")
def bible_reject(ctx: OpContext, p: BibleIdsParams) -> dict:
    return bible.set_status(ctx, p.ids, "rejected", lock=p.lock)


@op("bible.delete_preview", summary="Xem tác động của việc xoá mục Story Bible và lấy confirm_token",
    params=BibleDeletePreviewParams, result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False)
def bible_delete_preview(ctx: OpContext, p: BibleDeletePreviewParams) -> dict:
    return bible.delete_preview(ctx, p.ids)


@op("bible.delete", summary="Xoá (mềm, hoàn tác được) mục Story Bible — cần confirm_token", params=BibleDeleteParams,
    destructive=True)
def bible_delete(ctx: OpContext, p: BibleDeleteParams) -> dict:
    return bible.delete(ctx, p.confirm_token)


@op("bible.evidence_add", summary="Thêm bằng chứng (trích dẫn) cho một mục Story Bible", params=EvidenceAddParams)
def bible_evidence_add(ctx: OpContext, p: EvidenceAddParams) -> dict:
    return bible.evidence_add(ctx, p.item_id, p.quote, source=p.source, sequence_id=p.sequence_id, start=p.start,
                              end=p.end)
