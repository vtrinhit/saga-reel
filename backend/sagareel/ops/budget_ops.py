"""Thao tác nhóm `budget`: xem ngân sách tạo ảnh/video và số đã dùng (spec §18, bản M0).

Ghi trần ngân sách đi qua `settings.set_global` (`budget.enabled`, `budget.images_total`, `budget.videos_total`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import budget


class BudgetStatusParams(BaseModel):
    pass


class BudgetKindStatus(BaseModel):
    enabled: bool = Field(description="Ngân sách đang bật")
    total: int | None = Field(description="Trần số đơn vị; null = không giới hạn (tắt hoặc 0)")
    spent: float = Field(description="Số đơn vị đã dùng (không tính kết nối giả fake_*)")
    remaining: float | None = Field(description="Còn lại; null = không giới hạn")


class BudgetStatus(BaseModel):
    image: BudgetKindStatus
    video: BudgetKindStatus


@op(
    "budget.status",
    summary="Ngân sách ảnh/video: trần, đã dùng, còn lại (kết nối giả không tính)",
    params=BudgetStatusParams,
    result=BudgetStatus,
    read_only=True,
    idempotent=True,
    viewer=True,
    mcp="tool",
)
def budget_status(ctx: OpContext, p: BudgetStatusParams) -> BudgetStatus:
    resolver = ctx.services.resolver
    with ctx.services.db.session() as s:
        return BudgetStatus(
            image=BudgetKindStatus(**budget.status(s, "image", resolver=resolver)),
            video=BudgetKindStatus(**budget.status(s, "video", resolver=resolver)),
        )
