"""Thao tác nhóm `settings`: schema cài đặt, giá trị hiệu lực kèm tầng nguồn, ghi tầng global."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, RootModel

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.core.resolver import Resolved, SettingDef
from sagareel.services import settings


class SettingDefList(RootModel[list[SettingDef]]):
    pass


class ResolvedList(RootModel[list[Resolved]]):
    pass


class SettingsSchemaParams(BaseModel):
    pass


class SettingsExplainParams(BaseModel):
    run_overrides: dict[str, Any] | None = Field(
        None, description="Xem trước ghi đè cho riêng một lần chạy (tầng 'run'), vd. {\"video.duration\": 10}"
    )


class SettingsSetGlobalParams(BaseModel):
    values: dict[str, Any] = Field(
        description="Khoá → giá trị ghi đè ở tầng global; giá trị null = bỏ ghi đè, trở về mặc định"
    )


@op(
    "settings.schema",
    summary="Danh sách khoá cài đặt: kiểu, mặc định, lựa chọn, mô tả",
    params=SettingsSchemaParams,
    result=SettingDefList,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
def settings_schema(ctx: OpContext, p: SettingsSchemaParams) -> SettingDefList:
    return SettingDefList(settings.schema(ctx))


@op(
    "settings.explain",
    summary="Giá trị hiệu lực của mọi khoá cài đặt kèm tầng nguồn (default/global/run)",
    params=SettingsExplainParams,
    result=ResolvedList,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
def settings_explain(ctx: OpContext, p: SettingsExplainParams) -> ResolvedList:
    return ResolvedList(settings.explain(ctx, p.run_overrides))


@op(
    "settings.set_global",
    summary="Ghi đè cài đặt ở tầng toàn cục (hoàn tác được); trả về giá trị hiệu lực mới",
    params=SettingsSetGlobalParams,
    result=ResolvedList,
    idempotent=True,
    mcp="tool",
)
def settings_set_global(ctx: OpContext, p: SettingsSetGlobalParams) -> ResolvedList:
    settings.set_global(ctx, p.values)
    return ResolvedList(settings.explain(ctx))
