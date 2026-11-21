"""Kênh mẫu (Task K, M6): `channel.study_*`, `channel.analyze` (job), `channel.apply`. Nghiệp vụ ở `services/channel/`."""

from __future__ import annotations

from typing import Any

import anyio
from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import confirm, jobs, llm
from sagareel.services.channel import analysis, study
from sagareel.services.channel import apply as apply_svc


class DeletePreviewOut(BaseModel):
    impact: dict[str, Any]
    confirm_token: str
    expires_in_s: int = confirm.TTL_S


class StudyCreateParams(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="Tên kênh/bộ video mẫu")
    media_ids: list[int] = Field(description="Video mẫu đã upload (media.kind=video), tối đa 20")
    captions: str = Field(default="", description="Tiêu đề/ghi chú của các video (dán tay) cho LLM")
    project_id: int | None = None


@op("channel.study_create", summary="Tạo phân tích kênh mẫu từ các video đã upload (chưa phân tích)",
    params=StudyCreateParams, mcp="tool")
def study_create(ctx: OpContext, p: StudyCreateParams) -> dict:
    return study.create(ctx, p.title, p.media_ids, p.captions, p.project_id)


class IdParams(BaseModel):
    id: int


@op("channel.study_get", summary="Một phân tích kênh kèm kết quả (số đo, phong cách, hook, cấu trúc)",
    params=IdParams, read_only=True, idempotent=True, mcp="tool")
def study_get(ctx: OpContext, p: IdParams) -> dict:
    return study.get(ctx, p.id)


class StudyListParams(BaseModel):
    project_id: int | None = None
    cursor: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


@op("channel.study_list", summary="Danh sách phân tích kênh mẫu", params=StudyListParams, read_only=True,
    idempotent=True, mcp="tool")
def study_list(ctx: OpContext, p: StudyListParams) -> dict:
    return study.list_page(ctx, p.project_id, p.cursor, p.limit)


@op("channel.study_delete_preview", summary="Tác động của việc xoá phân tích kênh + confirm_token", params=IdParams,
    result=DeletePreviewOut, read_only=True, mcp="tool")
def study_delete_preview(ctx: OpContext, p: IdParams) -> dict:
    return study.delete_preview(ctx, p.id)


class ConfirmParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Từ channel.study_delete_preview (10 phút)")


@op("channel.study_delete", summary="Xoá (mềm) phân tích kênh — cần confirm_token", params=ConfirmParams,
    destructive=True, mcp="tool")
def study_delete(ctx: OpContext, p: ConfirmParams) -> dict:
    return study.delete(ctx, p.confirm_token)


class AnalyzeParams(BaseModel):
    study_id: int
    scene_threshold: float = Field(default=0.3, gt=0, lt=1, description="Ngưỡng đổi cảnh ffmpeg (0-1)")
    min_scene_s: float = Field(default=0.5, gt=0, description="Cảnh ngắn hơn bị gộp vào cảnh trước")
    max_frames: int = Field(default=16, ge=3, le=16, description="Số keyframe tối đa mỗi video trên lưới")


@op("channel.analyze", summary="Phân tích kênh mẫu (job channel.analyze): nhịp cắt, hook, lưới keyframe + LLM vision",
    params=AnalyzeParams, long_running=True, mcp="tool")
def analyze(ctx: OpContext, p: AnalyzeParams) -> dict:
    st = study.get(ctx, p.study_id)
    with ctx.services.db.session() as s:  # kiểm cấu hình LLM vision trước khi tạo job
        conn = llm.first_connection(s, analysis.TASK, images=True)[1]
    params = {"study_id": p.study_id, "scene_threshold": p.scene_threshold, "min_scene_s": p.min_scene_s,
              "max_frames": p.max_frames}
    return jobs.submit(ctx.services, analysis.JOB_TYPE, params, lane=f"conn:{conn.id}", project_id=st["project_id"],
                       client_ref=f"channel.analyze:{p.study_id}", max_attempts=2,
                       cost_estimate={"kind": "llm", "units": len(st["media_ids"]), "task": analysis.TASK})


class MakeParams(BaseModel):
    genre: bool = Field(default=True, description="Ghi số liệu thể loại (nhịp, độ dài clip/tập, hook) vào dự án")
    style: bool = Field(default=True, description="Ghi khối phong cách + khối tránh vào dự án")
    library_suggestions: bool = True


class ApplyParams(BaseModel):
    study_id: int
    project_id: int | None = Field(default=None, description="Dự án nhận kết quả; bỏ trống = dự án của phân tích, "
                                   "không có thì tạo dự án mẫu mới")
    make: MakeParams = Field(default_factory=MakeParams)


@op("channel.apply", summary="Ghi kết quả phân tích kênh vào dự án (số liệu thể loại, phong cách) + gợi ý Library; "
    "không có dự án → tạo dự án mẫu mới", params=ApplyParams, mcp="tool")
async def apply(ctx: OpContext, p: ApplyParams) -> dict:
    return await anyio.to_thread.run_sync(lambda: apply_svc.apply(
        ctx, p.study_id, p.project_id, p.make.genre, p.make.style, p.make.library_suggestions))
