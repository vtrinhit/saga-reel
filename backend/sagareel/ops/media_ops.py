"""Thao tác nhóm `media`: xem và liệt kê file trong kho media."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import media
from sagareel.services.media import MediaKind, MediaOut, MediaPage


class MediaGetParams(BaseModel):
    id: int = Field(description="Id media")


class MediaListParams(BaseModel):
    project_id: int | None = Field(None, description="Lọc theo project; bỏ trống = mọi media")
    kind: MediaKind | None = Field(None, description="Lọc theo loại: image/video/audio/subtitle/other")
    cursor: int | None = Field(None, description="`next_cursor` của trang trước")
    limit: int = Field(50, ge=1, le=200)


@op(
    "media.get",
    summary="Xem thông tin một media (kích thước, thời lượng, mime, URL tải và URL ảnh thu nhỏ)",
    params=MediaGetParams,
    result=MediaOut,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
def media_get(ctx: OpContext, p: MediaGetParams) -> MediaOut:
    with ctx.services.db.session() as s:
        return media.to_out(media.get(s, p.id))


@op(
    "media.list",
    summary="Liệt kê media mới nhất trước, lọc theo project/loại, phân trang bằng cursor",
    params=MediaListParams,
    result=MediaPage,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
def media_list(ctx: OpContext, p: MediaListParams) -> MediaPage:
    with ctx.services.db.session() as s:
        return media.list(s, project_id=p.project_id, kind=p.kind, cursor=p.cursor, limit=p.limit)
