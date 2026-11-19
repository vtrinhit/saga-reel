"""Thao tác nhóm `gate`: duyệt / từ chối điểm duyệt của quy trình (spec §13). Agent không được bỏ qua gate."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services.pipeline import engine


class GateApproveParams(BaseModel):
    sequence_id: int
    gate: str = Field(min_length=1, description="Tên gate: outline, storyboard, animatic, pilot, final… (có/không "
                      "tiền tố gate:)")
    note: str = ""


class GateRejectParams(BaseModel):
    sequence_id: int
    gate: str = Field(min_length=1)
    feedback: str = Field(min_length=1, description="Góp ý — được đưa vào bước viết/tạo lại")


@op("gate.approve", summary="Duyệt điểm duyệt của tập → quy trình chạy tiếp (duyệt trước được khi chưa tới gate)",
    params=GateApproveParams, idempotent=True, mcp="tool")
def gate_approve(ctx: OpContext, p: GateApproveParams) -> dict:
    return engine.approve(ctx, p.sequence_id, p.gate, p.note)


@op("gate.reject", summary="Từ chối gate đang chờ kèm góp ý → chạy lại các bước liên quan với góp ý đó",
    params=GateRejectParams, mcp="tool")
def gate_reject(ctx: OpContext, p: GateRejectParams) -> dict:
    return engine.reject(ctx, p.sequence_id, p.gate, p.feedback)
