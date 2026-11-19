"""Thao tác nhóm `system`."""

from __future__ import annotations

from pydantic import BaseModel

from sagareel import APP_NAME, __version__
from sagareel.core.context import OpContext
from sagareel.core.ops import REGISTRY, op


class SystemInfoParams(BaseModel):
    pass


class SystemInfo(BaseModel):
    app: str
    version: str
    boot_id: str
    ops_count: int


@op(
    "system.info",
    summary="Thông tin ứng dụng: tên, phiên bản, boot_id, số thao tác đã đăng ký",
    params=SystemInfoParams,
    result=SystemInfo,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
async def system_info(ctx: OpContext, p: SystemInfoParams) -> SystemInfo:
    return SystemInfo(app=APP_NAME, version=__version__, boot_id=ctx.services.boot_id, ops_count=len(REGISTRY))
