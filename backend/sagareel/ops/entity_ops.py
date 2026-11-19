"""Thao tác nhóm `entity`: đọc/liệt kê/sửa chung cho mọi thực thể ★ M1–M7 (`services/entities.py`).

Op chuyên biệt của từng khu (asset.*, clip.*…) vẫn là đường chính; `entity.*` cho agent/MCP sửa nhanh một trường.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import entities

EntityType = Literal[tuple(entities.ENTITIES)]  # type: ignore[valid-type]


class EntityGetParams(BaseModel):
    type: EntityType = Field(description="Loại thực thể (= tên bảng), vd. project, clip, asset")
    id: int


class EntityListParams(BaseModel):
    type: EntityType = Field(description="Loại thực thể (= tên bảng)")
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Lọc bằng nhau theo cột, vd. {\"sequence_id\": 3}; giá trị mảng = một trong; null = trống",
    )
    cursor: str | None = Field(default=None, description="`next_cursor` của trang trước")
    limit: int = Field(default=100, ge=1, le=entities.MAX_LIMIT)


class EntityPage(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class EntityPatchParams(BaseModel):
    type: EntityType = Field(description="Loại thực thể (= tên bảng)")
    id: int
    values: dict[str, Any] = Field(description="Cột → giá trị mới (không sửa được id, project_id, *_at)")
    expected_updated_at: datetime | None = Field(
        default=None, description="`updated_at` lúc đọc; lệch (người khác đã sửa) → lỗi stale_write 409"
    )


@op("entity.get", summary="Đọc một thực thể theo loại + id (mọi cột)", params=EntityGetParams,
    read_only=True, idempotent=True, mcp="tool")
def entity_get(ctx: OpContext, p: EntityGetParams) -> dict:
    with ctx.services.db.session() as s:
        return entities.to_dict(entities.get(s, p.type, p.id))


@op("entity.list", summary="Liệt kê thực thể theo loại, lọc theo cột, phân trang cursor (theo rank hoặc id)",
    params=EntityListParams, result=EntityPage, read_only=True, idempotent=True, mcp="tool")
def entity_list(ctx: OpContext, p: EntityListParams) -> dict:
    with ctx.services.db.session() as s:
        return entities.list_page(s, p.type, p.filters, p.cursor, p.limit)


@op("entity.patch", summary="Sửa các trường của một thực thể (hoàn tác được); kèm expected_updated_at để chống ghi đè",
    params=EntityPatchParams, idempotent=True, mcp="tool")
def entity_patch(ctx: OpContext, p: EntityPatchParams) -> dict:
    return entities.patch(ctx, p.type, p.id, p.values, p.expected_updated_at)
