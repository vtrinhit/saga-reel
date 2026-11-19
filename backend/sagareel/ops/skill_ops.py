"""Thao tác nhóm `skill`: đọc/sửa skill vận hành `.claude/skills/sagareel-operator/**` (bảng `skill_file`, có lịch sử)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import skills


class SkillListParams(BaseModel):
    pass


class SkillPathParams(BaseModel):
    path: str = Field(description="Đường dẫn tương đối trong thư mục skill, vd. SKILL.md, playbooks/montage.md")


class SkillUpdateParams(SkillPathParams):
    body: str = Field(description="Nội dung Markdown mới (≤ 256 KB)")
    expected_updated_at: datetime | None = Field(default=None, description="`updated_at` lúc đọc; lệch → stale_write")


class SkillFileInfo(BaseModel):
    path: str
    size: int
    updated_at: datetime


class SkillListOut(BaseModel):
    items: list[SkillFileInfo]
    dir: str


class SkillFileOut(SkillFileInfo):
    body: str
    change_set_id: int | None = None


@op("skill.list", summary="Liệt kê file skill vận hành (SKILL.md + playbook)", params=SkillListParams,
    result=SkillListOut, read_only=True, idempotent=True, viewer=False, mcp="tool")
def skill_list(ctx: OpContext, p: SkillListParams) -> dict:
    return skills.list_files(ctx)


@op("skill.get", summary="Đọc một file skill vận hành", params=SkillPathParams, result=SkillFileOut,
    read_only=True, idempotent=True, viewer=False, mcp="tool")
def skill_get(ctx: OpContext, p: SkillPathParams) -> dict:
    return skills.get(ctx, p.path)


@op("skill.update", summary="Sửa (hoặc tạo) file skill vận hành — có lịch sử, hoàn tác được; ghi ra .claude/skills/",
    params=SkillUpdateParams, result=SkillFileOut, idempotent=True, mcp="tool")
def skill_update(ctx: OpContext, p: SkillUpdateParams) -> dict:
    return skills.update(ctx, p.path, p.body, p.expected_updated_at)
