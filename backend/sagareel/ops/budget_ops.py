"""Thao tác nhóm `budget`: xem ngân sách tạo ảnh/video và số đã dùng (spec §18).

Ghi trần ngân sách toàn cục đi qua `settings.set_global` (`budget.enabled`, `budget.images_total`,
`budget.videos_total`); trần riêng dự án ở `project.budget` (`images_total`, `videos_total`, `enabled`) qua
`project.update`. `budget.confirm_below_units`: dưới số lượt này UI/agent không cần hỏi lại.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import budget
from sagareel.services.settings import global_layer


class BudgetStatusParams(BaseModel):
    project_id: int | None = Field(None, description="Kèm trần riêng của dự án (project.budget)")


class BudgetProjectStatus(BaseModel):
    project_id: int
    total: int | None = Field(description="Trần của dự án; null = không giới hạn")
    spent: float
    remaining: float | None


class BudgetKindStatus(BaseModel):
    enabled: bool = Field(description="Ngân sách đang bật")
    total: int | None = Field(description="Trần số đơn vị; null = không giới hạn (tắt hoặc 0)")
    spent: float = Field(description="Số đơn vị đã dùng (không tính kết nối giả fake_*)")
    remaining: float | None = Field(description="Còn lại; null = không giới hạn")
    project: BudgetProjectStatus | None = None


class BudgetStatus(BaseModel):
    image: BudgetKindStatus
    video: BudgetKindStatus
    confirm_below_units: int = Field(5, description="Không hỏi xác nhận lại với thao tác tốn tiền dưới số lượt này")


@op(
    "budget.status",
    summary="Ngân sách ảnh/video: trần, đã dùng, còn lại (toàn cục + dự án nếu có project_id; kết nối giả không tính)",
    params=BudgetStatusParams,
    result=BudgetStatus,
    read_only=True,
    idempotent=True,
    viewer=True,
    mcp="tool",
)
def budget_status(ctx: OpContext, p: BudgetStatusParams) -> BudgetStatus:
    resolver = ctx.services.resolver
    project_id = p.project_id if p.project_id is not None else ctx.project_id
    with ctx.services.db.session() as s:
        if project_id is not None:
            from sagareel.services import entities

            entities.get(s, "project", project_id)
        confirm = resolver.resolve("budget.confirm_below_units", [global_layer(s)]).value
        return BudgetStatus(
            image=BudgetKindStatus(**budget.status(s, "image", resolver=resolver, project_id=project_id)),
            video=BudgetKindStatus(**budget.status(s, "video", resolver=resolver, project_id=project_id)),
            confirm_below_units=int(confirm or 0),
        )
