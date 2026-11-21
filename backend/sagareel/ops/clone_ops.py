"""Clone từ video (Task K, M6): `clone.from_video` — job tách cảnh + LLM vision tạo clip. Chỉ video đã upload."""

from __future__ import annotations

from typing import Literal

import anyio
from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.errors import ValidationFailed
from sagareel.core.ops import op
from sagareel.services import jobs, llm
from sagareel.services.channel import clone


class CloneParams(BaseModel):
    media_id: int = Field(description="Video mẫu đã upload (chỉ phân tích, không chép khung hình)")
    sequence_id: int = Field(description="Tập nhận các clip mới (thêm vào cuối)")
    replace_map: dict[str, str] = Field(
        default_factory=dict,
        description="Mô tả nhân vật gốc -> `key` tài sản của dự án, vd. {\"cô gái áo đỏ\": \"AN\"}")
    scene_threshold: float = Field(default=0.3, gt=0, lt=1)
    min_scene_s: float = Field(default=1.0, gt=0, description="Cảnh ngắn hơn bị gộp vào cảnh trước")
    transcript: str | None = Field(default=None, description="Lời dán tay (chưa có STT); chia theo thời lượng cảnh")
    engine: Literal["auto", "ai_video", "still_motion"] = Field(
        default="auto",
        description="auto: cảnh 4.5-17 s -> ai_video, còn lại still_motion; clip giữ đúng độ dài cảnh")
    dry_run: bool = Field(default=False, description="Chỉ tách cảnh và trả kế hoạch (không gọi LLM, không tạo job)")


@op("clone.from_video", summary="Clone cấu trúc video mẫu: tách cảnh, LLM vision viết lại prompt thay nhân vật, "
    "tạo clip (job clone.from_video; dry_run = chỉ kế hoạch)", params=CloneParams, long_running=True, costly=True,
    mcp="tool")
async def from_video(ctx: OpContext, p: CloneParams) -> dict:
    services = ctx.services
    project_id, rmap = await anyio.to_thread.run_sync(
        clone.resolve_replace_map, services, p.sequence_id, p.replace_map)
    planned = await clone.plan(services, p.media_id, p.scene_threshold, p.min_scene_s, p.engine, p.transcript)
    if p.dry_run:
        return {"dry_run": True, "replace": [r["original"] for r in rmap], **planned}
    if planned["scene_count"] > clone.MAX_SCENES:
        raise ValidationFailed(f"Video có {planned['scene_count']} cảnh (tối đa {clone.MAX_SCENES}).",
                               code="too_many_scenes", hint="Tăng scene_threshold hoặc min_scene_s.")

    def prepare() -> int:  # chưa cấu hình LLM vision -> llm_not_configured trước khi tạo job
        with services.db.session() as s:
            return llm.first_connection(s, clone.TASK, images=True)[1].id

    conn_id = await anyio.to_thread.run_sync(prepare)
    params = p.model_dump(exclude={"dry_run"})
    return await anyio.to_thread.run_sync(lambda: jobs.submit(
        services, clone.JOB_TYPE, params, lane=f"conn:{conn_id}", project_id=project_id,
        sequence_id=p.sequence_id, max_attempts=2,
        cost_estimate={"kind": "llm", "units": planned["scene_count"], "task": clone.TASK}))
