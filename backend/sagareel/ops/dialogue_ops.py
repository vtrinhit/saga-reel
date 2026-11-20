"""Thao tác nhóm `dialogue`: lời thoại theo clip (thứ tự rank), đặt trọn danh sách, chuyển, xoá hai bước."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import last_change_set_id, op
from sagareel.ops.voice_ops import DeleteParams, DeletePreviewOut, IdsParams
from sagareel.services.audio import dialogue

Source = Literal["tts", "upload", "grok", "none"]


def _cs(out: dict) -> dict:
    """Kèm `change_set_id` của lần ghi (None = không có gì đổi) để UI hoàn tác đúng thao tác."""
    return {**out, "change_set_id": last_change_set_id()}


class DialogueCreateParams(BaseModel):
    clip_id: int
    text: str = Field(description="Lời thoại (gửi TTS nguyên văn)")
    speaker_asset_id: int | None = Field(default=None, description="Nhân vật nói; trống = người kể")
    after_id: int | None = Field(default=None, description="Chèn sau câu này; trống = cuối clip")
    emotion: str | None = None
    source: Source | None = Field(default=None, description="tts (mặc định) / upload / grok / none")
    voice_override: dict[str, Any] | None = None
    offset_ms: int | None = Field(default=None, ge=0, description="Bắt đầu sau đầu clip (ms)")
    subtitle: bool | None = None
    translations: dict[str, str] | None = None


class DialogueUpdateParams(BaseModel):
    id: int
    values: dict[str, Any]
    expected_updated_at: datetime | None = None


class DialogueMoveParams(BaseModel):
    ids: list[int] = Field(min_length=1)
    after_id: int | None = Field(default=None, description="Đặt sau câu này; trống = đầu clip")
    clip_id: int | None = Field(default=None, description="Chuyển sang clip khác")


class DialogueLineIn(BaseModel):
    id: int | None = Field(default=None, description="Câu có sẵn (sửa); trống = câu mới")
    text: str | None = None
    speaker_asset_id: int | None = None
    emotion: str | None = None
    source: Source | None = None
    voice_override: dict[str, Any] | None = None
    offset_ms: int | None = Field(default=None, ge=0)
    subtitle: bool | None = None
    translations: dict[str, str] | None = None


class DialogueBulkSetParams(BaseModel):
    clip_id: int
    lines: list[DialogueLineIn] = Field(description="Toàn bộ lời thoại của clip theo thứ tự; câu cũ vắng mặt bị xoá")


class DialogueListParams(BaseModel):
    clip_id: int | None = None
    sequence_id: int | None = None


@op("dialogue.create", summary="Thêm một câu thoại vào clip", params=DialogueCreateParams, mcp="tool")
def dialogue_create(ctx: OpContext, p: DialogueCreateParams) -> dict:
    vals = p.model_dump(exclude={"clip_id", "text", "after_id"})
    return _cs(dialogue.create(ctx, p.clip_id, p.text, after_id=p.after_id, **vals))


@op("dialogue.update", summary="Sửa câu thoại (hoàn tác được)", params=DialogueUpdateParams, idempotent=True,
    mcp="tool")
def dialogue_update(ctx: OpContext, p: DialogueUpdateParams) -> dict:
    return _cs(dialogue.update(ctx, p.id, p.values, p.expected_updated_at))


@op("dialogue.move", summary="Đổi thứ tự / chuyển câu thoại sang clip khác", params=DialogueMoveParams, mcp="tool")
def dialogue_move(ctx: OpContext, p: DialogueMoveParams) -> dict:
    return _cs({"items": dialogue.move(ctx, p.ids, p.after_id, p.clip_id)})


@op("dialogue.list", summary="Lời thoại của một clip hoặc cả tập (theo thứ tự)", params=DialogueListParams,
    read_only=True, idempotent=True, mcp="tool")
def dialogue_list(ctx: OpContext, p: DialogueListParams) -> dict:
    return {"items": dialogue.list_lines(ctx, p.clip_id, p.sequence_id)}


@op("dialogue.bulk_set", summary="Đặt trọn danh sách lời thoại của clip (sửa/tạo/xoá trong một change set)",
    params=DialogueBulkSetParams, mcp="tool")
def dialogue_bulk_set(ctx: OpContext, p: DialogueBulkSetParams) -> dict:
    lines = [x.model_dump(exclude_none=True) for x in p.lines]
    return _cs({"items": dialogue.bulk_set(ctx, p.clip_id, lines)})


@op("dialogue.delete_preview", summary="Xem tác động khi xoá câu thoại + confirm_token", params=IdsParams,
    result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False)
def dialogue_delete_preview(ctx: OpContext, p: IdsParams) -> dict:
    return dialogue.delete_preview(ctx, p.ids)


@op("dialogue.delete", summary="Xoá (mềm) câu thoại — cần confirm_token", params=DeleteParams, destructive=True)
def dialogue_delete(ctx: OpContext, p: DeleteParams) -> dict:
    return _cs(dialogue.delete(ctx, p.confirm_token))
