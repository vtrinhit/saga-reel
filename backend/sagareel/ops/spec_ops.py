"""File khai báo project (spec §17): `project.export_spec` → YAML, `project.plan` → khác biệt + ước tính + fingerprint,
`project.apply(spec, plan_fingerprint)` → áp trong một change set (khoá ổn định, áp lại = không đổi gì)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.errors import ValidationFailed
from sagareel.core.ops import op
from sagareel.services import entities
from sagareel.services.projects import KEY_RE
from sagareel.services.spec import export
from sagareel.services.spec import models as m
from sagareel.services.spec import plan as spec_plan


class ExportSpecParams(BaseModel):
    project_id: int
    include: list[m.Section] | None = Field(
        default=None, description="Phần đưa vào: presets, library, groups, sequences (bỏ trống = tất cả)")
    save: bool = Field(default=True, description="Ghi thêm file vào outputs/specs/")


class ExportSpecOut(BaseModel):
    yaml: str
    path: str | None = Field(description="File đã ghi (tương đối so với thư mục outputs), null nếu save=false")
    sha256: str
    counts: dict[str, int]


class SpecInputParams(BaseModel):
    spec_text: str | None = Field(default=None, description="Nội dung file khai báo (YAML hoặc JSON)")
    upload_id: str | None = Field(default=None, description="Hoặc id upload (kind=other) chứa file khai báo")


class ApplyParams(SpecInputParams):
    plan_fingerprint: str = Field(min_length=64, max_length=64, description="Lấy từ project.plan của đúng file này")


class PlanEntry(BaseModel):
    type: str
    key: str
    changes: dict[str, Any] | None = None


class PlanOut(BaseModel):
    project_key: str
    project_id: int | None
    sections: list[str]
    creates: list[PlanEntry]
    updates: list[PlanEntry]
    delete_candidates: list[PlanEntry] = Field(
        description="Có trong DB nhưng không có trong file — CHỈ liệt kê, không bao giờ tự xoá")
    estimate: dict[str, Any]
    warnings: list[dict[str, Any]]
    plan_fingerprint: str
    summary: str


class ApplyOut(BaseModel):
    project_id: int | None
    project_key: str
    applied: bool = Field(description="false = không có gì thay đổi (áp lại cùng file)")
    change_set_id: int | None
    created: list[PlanEntry]
    updated: list[PlanEntry]
    delete_candidates: list[PlanEntry]


def _counts(spec: m.ProjectSpec) -> dict[str, int]:
    seqs = spec.sequences or []
    lib = spec.library
    return {
        "assets": len(lib.assets) if lib else 0,
        "sequences": len(seqs),
        "clips": sum(len(q.clips) for q in seqs),
        "dialogue": sum(len(c.dialogue) for q in seqs for c in q.clips),
        "media": len(spec.media),
    }


def export_spec(ctx: OpContext, project_id: int, include: list[str] | None, save: bool) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        project = entities.get(s, "project", project_id)
        if not KEY_RE.fullmatch(project.key or ""):
            raise ValidationFailed(f"Mã dự án '{project.key}' không hợp lệ để xuất file khai báo.",
                                   code="invalid_path_part", hint="Đổi mã dự án (chữ không dấu, số, '-', '_').")
        spec = export.build(s, project, include)
    text = export.to_yaml(spec)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rel = None
    if save:
        d = spec_plan.save_dir(ctx.services.settings).resolve()
        name = f"{spec.project.key}-{datetime.now():%Y%m%d-%H%M%S}.yaml"
        target = (d / name).resolve()
        if target.parent != d:  # tên file không bao giờ được thoát khỏi outputs/specs
            raise ValidationFailed("Tên file khai báo nằm ngoài thư mục outputs/specs.", code="invalid_path_part")
        d.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        rel = f"specs/{name}"
    return {"yaml": text, "path": rel, "sha256": digest, "counts": _counts(spec)}


@op("project.export_spec", summary="Xuất file khai báo project (YAML: style, preset, Library, tập, cảnh, clip, thoại, "
    "timeline, provider theo tên kết nối) + lưu vào outputs/specs/", params=ExportSpecParams, result=ExportSpecOut,
    read_only=True, idempotent=True, viewer=False, mcp="tool")
def project_export_spec(ctx: OpContext, p: ExportSpecParams) -> dict:
    return export_spec(ctx, p.project_id, p.include, p.save)


@op("project.plan", summary="Lập kế hoạch áp file khai báo: tạo mới / sửa (diff từng trường) / ứng viên xoá (chỉ liệt "
    "kê) + ước tính ảnh/video/giọng/render + plan_fingerprint", params=SpecInputParams, result=PlanOut,
    read_only=True, idempotent=True, viewer=False, mcp="tool")
def project_plan(ctx: OpContext, p: SpecInputParams) -> dict:
    return spec_plan.plan(ctx, spec_plan.load_text(ctx, p.spec_text, p.upload_id))


@op("project.apply", summary="Áp file khai báo đã plan (một change set, hoàn tác được; khoá ổn định nên áp lại không "
    "tạo trùng; không bao giờ xoá). Dữ liệu đổi sau plan → plan_stale", params=ApplyParams, result=ApplyOut,
    idempotent=True, mcp="tool")
def project_apply(ctx: OpContext, p: ApplyParams) -> dict:
    return spec_plan.apply(ctx, spec_plan.load_text(ctx, p.spec_text, p.upload_id), p.plan_fingerprint)
