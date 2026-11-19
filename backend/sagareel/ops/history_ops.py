"""Thao tác nhóm `history`: liệt kê, hoàn tác, làm lại, khôi phục phiên bản."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import history
from sagareel.services.history import ChangeSetOut, HistoryPage


class _Scoped(BaseModel):
    project_id: int | None = Field(None, description="Project áp dụng; bỏ trống = project của ngữ cảnh gọi")


def _scoped(ctx: OpContext, p: _Scoped) -> OpContext:
    return ctx if p.project_id is None else dataclasses.replace(ctx, project_id=p.project_id)


class HistoryListParams(_Scoped):
    entity_type: str | None = Field(None, description="Lọc theo loại thực thể (tên bảng), vd. 'template'")
    entity_id: int | None = Field(None, description="Lọc theo id thực thể")
    cursor: int | None = Field(None, description="`next_cursor` của trang trước")
    limit: int = Field(50, ge=1, le=200)


class HistoryUndoParams(_Scoped):
    change_set_id: int | None = Field(None, description="Bỏ trống = thao tác mới nhất của chính bạn chưa hoàn tác")


class HistoryRedoParams(_Scoped):
    change_set_id: int | None = Field(None, description="Bỏ trống = thao tác bạn vừa hoàn tác gần nhất")


class HistoryRestoreParams(_Scoped):
    entity_type: str = Field(description="Loại thực thể (tên bảng), vd. 'template'")
    entity_id: int
    revision_id: int = Field(description="Phiên bản cần khôi phục (trạng thái ngay sau revision này)")


@op(
    "history.list",
    summary="Liệt kê lịch sử thay đổi (change set) theo project hoặc theo thực thể, mới nhất trước",
    params=HistoryListParams,
    result=HistoryPage,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
def history_list(ctx: OpContext, p: HistoryListParams) -> HistoryPage:
    return history.list(_scoped(ctx, p), p.entity_type, p.entity_id, p.cursor, p.limit)


@op(
    "history.undo",
    summary="Hoàn tác một thao tác (mặc định: thao tác mới nhất của chính bạn); từ chối nếu đã bị sửa tiếp",
    params=HistoryUndoParams,
    result=ChangeSetOut,
    mcp="tool",
)
def history_undo(ctx: OpContext, p: HistoryUndoParams) -> ChangeSetOut:
    return history.undo(_scoped(ctx, p), p.change_set_id)


@op(
    "history.redo",
    summary="Làm lại một thao tác đã hoàn tác (mặc định: thao tác bạn vừa hoàn tác gần nhất)",
    params=HistoryRedoParams,
    result=ChangeSetOut,
    mcp="tool",
)
def history_redo(ctx: OpContext, p: HistoryRedoParams) -> ChangeSetOut:
    return history.redo(_scoped(ctx, p), p.change_set_id)


@op(
    "history.restore",
    summary="Khôi phục một thực thể về một phiên bản cụ thể (dùng khi hoàn tác bị xung đột)",
    params=HistoryRestoreParams,
    result=ChangeSetOut,
    mcp="tool",
)
def history_restore(ctx: OpContext, p: HistoryRestoreParams) -> ChangeSetOut:
    return history.restore(_scoped(ctx, p), p.entity_type, p.entity_id, p.revision_id)
