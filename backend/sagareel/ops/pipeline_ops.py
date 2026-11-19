"""Thao tác nhóm `pipeline` (quy trình theo preset, spec §13) + `sequence.status` (cho MCP `sr_sequence_status`)."""

from __future__ import annotations

from typing import Any

import anyio
from pydantic import BaseModel, Field, model_validator

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services.pipeline import engine, idea
from sagareel.services.pipeline import workflow as W


class PipelineOverrides(BaseModel):
    steps: dict[str, bool] = Field(default_factory=dict, description='Bật/tắt bước, vd. {"gate:pilot": true, '
                                   '"export": false}')
    settings: dict[str, Any] = Field(default_factory=dict, description="Cài đặt tầng run, vd. "
                                     '{"author.image_prompts": "agent"}')
    params: dict[str, Any] = Field(default_factory=dict, description='Tham số bước, vd. {"idea": "…", '
                                   '"outline": {"arc": "…"}}')
    genre_overrides: dict[str, Any] = Field(default_factory=dict, description='Ghi đè số liệu thể loại, vd. '
                                            '{"clips": [12, 12]}')
    platforms: list[str] = Field(default_factory=list, description="Nền tảng cho bước export")


class PipelineRunParams(BaseModel):
    sequence_ids: list[int] = Field(min_length=1, max_length=500, description="Tập đầu là tập dẫn của lô")
    workflow_key: str = Field(default=W.DEFAULT_WORKFLOW, description="full_auto / storyboard_first / serial_batch …")
    overrides: PipelineOverrides = Field(default_factory=PipelineOverrides)
    restart: bool = Field(default=False, description="Chạy lại từ đầu (bỏ trạng thái + quyết định duyệt cũ)")
    dry_run: bool = Field(default=False, description="Chỉ trả các bước + ước tính, không chạy")


class PipelineStatusParams(BaseModel):
    sequence_ids: list[int] | None = Field(default=None, max_length=500)
    project_id: int | None = None

    @model_validator(mode="after")
    def _one(self) -> PipelineStatusParams:
        if not self.sequence_ids and self.project_id is None:
            raise ValueError("cần sequence_ids hoặc project_id")
        return self


class SequenceIdParams(BaseModel):
    sequence_id: int


class IdeaParams(BaseModel):
    idea: str = Field(min_length=1, description="Ý tưởng câu chuyện")
    genre: str = Field(default="serial", description="Key preset thể loại: storyboard, montage, series, serial, narration")
    episodes: int = Field(default=1, ge=1, le=200)
    clips_per_episode: int = Field(default=14, ge=1, le=60)
    project_id: int | None = Field(default=None, description="Bỏ trống = tạo dự án mới")
    name: str | None = Field(default=None, description="Tên dự án mới (mặc định lấy từ ý tưởng)")
    workflow_key: str = W.DEFAULT_WORKFLOW
    steps: dict[str, bool] = Field(default_factory=dict, description="Bật/tắt bước của quy trình")
    platforms: list[str] = Field(default_factory=list)
    dry_run: bool = Field(default=False, description="Chỉ ước tính (số ảnh/video/LLM, giây phim, ngân sách)")


@op("pipeline.run", summary="Chạy quy trình theo preset cho các tập (job dài pipeline.run, dừng ở gate/bước chờ nhập, "
    "nối lại được); dry_run chỉ ước tính", params=PipelineRunParams, costly=True, long_running=True, mcp="tool")
def pipeline_run(ctx: OpContext, p: PipelineRunParams) -> dict:
    if p.dry_run or ctx.dry_run:
        return engine.plan(ctx, p.sequence_ids, p.workflow_key, p.overrides.model_dump())
    return engine.start(ctx, p.sequence_ids, p.workflow_key, p.overrides.model_dump(), restart=p.restart)


@op("pipeline.status", summary="Trạng thái quy trình từng tập: bước, gate, đang chờ gì, job con",
    params=PipelineStatusParams, read_only=True, idempotent=True, mcp="tool")
def pipeline_status(ctx: OpContext, p: PipelineStatusParams) -> dict:
    return engine.status(ctx, sequence_ids=p.sequence_ids, project_id=p.project_id)


@op("pipeline.cancel", summary="Huỷ quy trình của tập (job quy trình + job con đang chờ/chạy)", params=SequenceIdParams,
    mcp="tool")
async def pipeline_cancel(ctx: OpContext, p: SequenceIdParams) -> dict:
    return await engine.cancel(ctx, p.sequence_id)


@op("pipeline.idea_to_video", summary="Ý tưởng → dự án + N tập → quy trình tự động (bible → dàn ý → kịch bản → clip → "
    "ảnh/video); dry_run ước tính trước", params=IdeaParams, costly=True, long_running=True, mcp="tool")
def pipeline_idea_to_video(ctx: OpContext, p: IdeaParams) -> dict:
    kw: dict[str, Any] = {"idea": p.idea, "genre": p.genre, "episodes": p.episodes,
                          "clips_per_episode": p.clips_per_episode, "workflow_key": p.workflow_key,
                          "project_id": p.project_id, "steps": p.steps}
    if p.dry_run or ctx.dry_run:
        return idea.estimate(ctx, **kw)
    return idea.create(ctx, **kw, name=p.name, platforms=p.platforms)


@op("sequence.status", summary="Tóm tắt một tập cho agent: quy trình + gate + clip/take + chi phí thật",
    params=SequenceIdParams, read_only=True, idempotent=True, mcp="tool")
async def sequence_status(ctx: OpContext, p: SequenceIdParams) -> dict:
    return await anyio.to_thread.run_sync(engine.sequence_status, ctx, p.sequence_id)
