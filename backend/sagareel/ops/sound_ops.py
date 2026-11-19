"""Thao tác nhóm `sound` (kho âm thanh có nguồn + giấy phép) và `timeline` (nhạc/SFX/title neo vào clip, §23.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services.audio import timeline


class SoundAddParams(BaseModel):
    media_id: int
    kind: Literal["music", "sfx", "ambience"]
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, description="Nguồn (URL/tác giả/kho)")
    license: str = Field(min_length=1, description="Giấy phép; không rõ thì ghi unknown (sẽ cảnh báo khi xuất)")
    attribution: str = ""
    project_id: int | None = Field(default=None, description="Trống = kho toàn cục")
    tags: list[str] = Field(default_factory=list)


class SoundListParams(BaseModel):
    project_id: int | None = None
    kind: Literal["music", "sfx", "ambience"] | None = None
    q: str | None = Field(default=None, description="Lọc theo tiêu đề")
    include_global: bool = True


Track = Literal["music", "sfx", "title"]


class TimelineAddParams(BaseModel):
    sequence_id: int
    track: Track
    anchor_clip_id: int | None = Field(default=None, description="Clip neo; trống = đầu tập")
    offset_ms: int = Field(default=0, ge=0, description="Bắt đầu sau đầu clip neo (ms)")
    end_anchor: dict[str, Any] | None = Field(default=None, description="{clip_id} | {sequence_end: true} | trống")
    duration_ms: int | None = Field(default=None, gt=0)
    sound_id: int | None = None
    media_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict, description="volume_db, fade_in_s, fade_out_s, duck, loop, "
                                   "text (title)…")


class TimelineUpdateParams(BaseModel):
    id: int
    values: dict[str, Any]
    expected_updated_at: datetime | None = None


class TimelineRemovePreviewParams(BaseModel):
    ids: list[int] = Field(min_length=1)


class TimelineRemoveParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ timeline.remove_preview")


class TimelineListParams(BaseModel):
    sequence_id: int
    track: Track | None = None


@op("sound.add", summary="Thêm âm thanh vào kho (bắt buộc nguồn + giấy phép)", params=SoundAddParams, mcp="tool")
def sound_add(ctx: OpContext, p: SoundAddParams) -> dict:
    return timeline.sound_add(ctx, p.media_id, p.kind, p.title, p.source, p.license, p.attribution,
                              project_id=p.project_id, tags=p.tags)


@op("sound.list", summary="Liệt kê kho âm thanh (dự án + toàn cục)", params=SoundListParams, read_only=True,
    idempotent=True, mcp="tool")
def sound_list(ctx: OpContext, p: SoundListParams) -> dict:
    return {"items": timeline.sound_list(ctx, p.project_id, p.kind, p.q, p.include_global)}


@op("timeline.add", summary="Thêm nhạc/SFX/title vào tập, neo vào clip", params=TimelineAddParams, mcp="tool")
def timeline_add(ctx: OpContext, p: TimelineAddParams) -> dict:
    vals = p.model_dump(exclude={"sequence_id", "track"})
    return timeline.timeline_add(ctx, p.sequence_id, p.track, **vals)


@op("timeline.update", summary="Sửa mục timeline (neo, offset, thời lượng, tham số)", params=TimelineUpdateParams,
    idempotent=True, mcp="tool")
def timeline_update(ctx: OpContext, p: TimelineUpdateParams) -> dict:
    return timeline.timeline_update(ctx, p.id, p.values, p.expected_updated_at)


@op("timeline.remove_preview", summary="Xem các mục timeline sẽ gỡ + confirm_token", params=TimelineRemovePreviewParams,
    read_only=True, idempotent=True, viewer=False, mcp="tool")
def timeline_remove_preview(ctx: OpContext, p: TimelineRemovePreviewParams) -> dict:
    return timeline.timeline_remove_preview(ctx, p.ids)


@op("timeline.remove", summary="Gỡ mục timeline (xoá mềm, hoàn tác được) — cần confirm_token",
    params=TimelineRemoveParams, destructive=True, mcp="tool")
def timeline_remove(ctx: OpContext, p: TimelineRemoveParams) -> dict:
    return timeline.timeline_remove(ctx, p.confirm_token)


@op("timeline.list", summary="Các mục nhạc/SFX/title của tập", params=TimelineListParams, read_only=True,
    idempotent=True, mcp="tool")
def timeline_list(ctx: OpContext, p: TimelineListParams) -> dict:
    return {"items": timeline.timeline_list(ctx, p.sequence_id, p.track)}
