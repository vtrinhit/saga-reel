"""Thao tác nhóm `scene` (cảnh trong tập): tạo, sửa, đổi chỗ, xoá hai bước."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.ops.project_ops import DeletePreviewOut
from sagareel.services import sequences


class SceneCreateParams(BaseModel):
    sequence_id: int
    title: str = ""
    key: str | None = Field(default=None, description="Mã cảnh trong tập; bỏ trống = s<NN>")
    after_scene_id: int | None = Field(default=None, description="Đặt sau cảnh này; bỏ trống = cuối, 0 = đầu")
    location_asset_id: int | None = None
    summary: str = ""
    state: dict[str, Any] | None = Field(default=None, description="time_of_day, era, weather, lighting, char_states")


class SceneUpdateParams(BaseModel):
    id: int
    values: dict[str, Any]
    expected_updated_at: datetime | None = None


class SceneMoveParams(BaseModel):
    id: int
    after_scene_id: int | None = Field(default=None, description="Bỏ trống = cuối, 0 = đầu")


class SceneDeletePreviewParams(BaseModel):
    ids: list[int] = Field(min_length=1)


class SceneDeleteParams(BaseModel):
    confirm_token: str | None = None


@op("scene.create", summary="Tạo cảnh trong tập", params=SceneCreateParams, mcp="tool")
def scene_create(ctx: OpContext, p: SceneCreateParams) -> dict:
    return sequences.scene_create(ctx, p.sequence_id, p.title, key=p.key, after_scene_id=p.after_scene_id,
                                  location_asset_id=p.location_asset_id, summary=p.summary, state=p.state)


@op("scene.update", summary="Sửa cảnh (hoàn tác được); kèm expected_updated_at để chống ghi đè",
    params=SceneUpdateParams, idempotent=True)
def scene_update(ctx: OpContext, p: SceneUpdateParams) -> dict:
    return sequences.scene_update(ctx, p.id, p.values, p.expected_updated_at)


@op("scene.move", summary="Đổi chỗ cảnh (chỉ ghi rank của cảnh đó)", params=SceneMoveParams, idempotent=True)
def scene_move(ctx: OpContext, p: SceneMoveParams) -> dict:
    return sequences.scene_move(ctx, p.id, p.after_scene_id)


@op("scene.delete_preview", summary="Xem tác động của việc xoá cảnh và lấy confirm_token",
    params=SceneDeletePreviewParams, result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False)
def scene_delete_preview(ctx: OpContext, p: SceneDeletePreviewParams) -> dict:
    return sequences.scene_delete_preview(ctx, p.ids)


@op("scene.delete", summary="Xoá (mềm) cảnh; clip của cảnh được giữ, chỉ bỏ gắn cảnh", params=SceneDeleteParams,
    destructive=True)
def scene_delete(ctx: OpContext, p: SceneDeleteParams) -> dict:
    return sequences.scene_delete(ctx, p.confirm_token)
