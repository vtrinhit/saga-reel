"""Thao tác lưu trữ: dọn file take không được chọn sang thùng rác (hai bước), thùng rác (liệt kê/khôi phục/xoá hẳn)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import storage


class CleanupFilters(BaseModel):
    unselected_takes_older_than_days: int = Field(default=30, ge=0,
                                                  description="Chỉ take (không được chọn) tạo cách đây ≥ số ngày này")
    kinds: list[Literal["image", "video", "audio"]] | None = Field(default=None, description="Mặc định cả ba")
    project_id: int | None = None


class CleanupPreviewParams(BaseModel):
    filters: CleanupFilters = Field(default_factory=CleanupFilters)


class ConfirmParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ bước *_preview (10 phút)")


class TrashListParams(BaseModel):
    project_id: int | None = None
    cursor: int | None = None
    limit: int = Field(default=100, ge=1, le=500)


class TrashRestoreParams(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=5000)


class PurgePreviewParams(BaseModel):
    older_than_days: int = Field(default=0, ge=0, description="Chỉ file đã vào thùng rác ≥ số ngày này")
    project_id: int | None = None


@op("media.cleanup_preview", summary="Xem trước dọn file gốc của take không được chọn (đủ cũ) sang thùng rác + "
    "confirm_token", params=CleanupPreviewParams, read_only=True, idempotent=True, viewer=False, mcp="tool")
def media_cleanup_preview(ctx: OpContext, p: CleanupPreviewParams) -> dict:
    return storage.cleanup_preview(ctx, p.filters.model_dump(exclude_none=True))


@op("media.cleanup", summary="Chuyển file gốc của take không được chọn sang data/trash/ (cần confirm_token từ "
    "media.cleanup_preview; khôi phục bằng trash.restore)", params=ConfirmParams, destructive=True, mcp="tool")
def media_cleanup(ctx: OpContext, p: ConfirmParams) -> dict:
    return storage.cleanup(ctx, p.confirm_token)


@op("trash.list", summary="Liệt kê file trong thùng rác (data/trash) + tổng dung lượng", params=TrashListParams,
    read_only=True, idempotent=True, viewer=False)
def trash_list(ctx: OpContext, p: TrashListParams) -> dict:
    return storage.trash_list(ctx, p.project_id, p.cursor, p.limit)


@op("trash.restore", summary="Đưa file từ thùng rác về chỗ cũ", params=TrashRestoreParams, idempotent=True)
def trash_restore(ctx: OpContext, p: TrashRestoreParams) -> dict:
    return storage.restore(ctx, p.media_ids)


@op("trash.purge_preview", summary="Xem trước xoá hẳn file trong thùng rác + confirm_token", params=PurgePreviewParams,
    read_only=True, idempotent=True, viewer=False)
def trash_purge_preview(ctx: OpContext, p: PurgePreviewParams) -> dict:
    return storage.purge_preview(ctx, p.older_than_days, p.project_id)


@op("trash.purge", summary="Xoá hẳn file trong thùng rác (không hoàn tác được; cần confirm_token)", params=ConfirmParams,
    destructive=True)
def trash_purge(ctx: OpContext, p: ConfirmParams) -> dict:
    return storage.purge(ctx, p.confirm_token)
