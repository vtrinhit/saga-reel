"""Thao tác nhóm `story` (AI viết, spec §14.3–14.4, §15): mỗi op tạo job `story.run` (chữ chạy dần qua `llm.delta`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from sagareel.core.context import OpContext
from sagareel.core.errors import ValidationFailed
from sagareel.core.ops import op
from sagareel.handlers.story import JOB_TYPE, LANE
from sagareel.services import entities, jobs, llm

TASK_KEYS = {
    "write_bible": "story_write_bible", "outline": "story_outline", "write_script": "story_write_script",
    "split_clips": "story_split_clips", "image_prompts": "story_image_prompts",
    "motion_prompts": "story_motion_prompts", "dialogue": "story_dialogue", "adapt_novel": "story_adapt_novel",
    "memory_summarize": "story_memory_summarize",
}


def submit(ctx: OpContext, task: str, args: dict[str, Any], *, project_id: int | None = None,
           sequence_id: int | None = None) -> dict:
    """Kiểm LLM đã cấu hình (lỗi `llm_not_configured` 409 trả ngay, kèm gợi ý manual/agent) rồi tạo job."""
    with ctx.services.db.session() as s:
        if sequence_id is not None:
            seq = entities.get(s, "sequence", sequence_id)
            project_id = seq.project_id
        elif project_id is not None:
            entities.get(s, "project", project_id)
        llm.routes(s, TASK_KEYS[task])
    ref = f"story:{task}:{sequence_id if sequence_id is not None else f'p{project_id}'}"
    return jobs.submit(ctx.services, JOB_TYPE, {"task": task, "args": args, "actor": ctx.actor,
                                                "project_id": project_id},
                       lane=LANE, project_id=project_id, sequence_id=sequence_id, client_ref=ref, max_attempts=2)


class WriteBibleParams(BaseModel):
    project_id: int
    idea: str = Field(min_length=1, description="Ý tưởng / logline / tóm tắt câu chuyện")
    genre: str | None = Field(default=None, description="Key preset thể loại (serial, series, narration…); bỏ trống = "
                              "thể loại của dự án")
    feedback: str = ""


class OutlineParams(BaseModel):
    project_id: int
    episodes: int | None = Field(default=None, ge=1, le=50, description="Số tập MỚI cần lập dàn ý (nối tiếp)")
    sequence_ids: list[int] | None = Field(default=None, max_length=50, description="Hoặc: điền dàn ý cho các tập có sẵn")
    arc: str = Field(default="", description="Định hướng arc (tuỳ chọn)")
    group_id: int | None = Field(default=None, description="Nhóm arc: gán tập + ghi tóm tắt arc")
    feedback: str = ""

    @model_validator(mode="after")
    def _one(self) -> OutlineParams:
        if not self.episodes and not self.sequence_ids:
            raise ValueError("cần episodes hoặc sequence_ids")
        return self


class SequenceTaskParams(BaseModel):
    sequence_id: int
    feedback: str = Field(default="", description="Góp ý để viết lại")


class SplitParams(SequenceTaskParams):
    replace: bool = Field(default=True, description="Tập đã có clip → xoá mềm (hoàn tác được) rồi chia lại")


class ClipsTaskParams(BaseModel):
    sequence_id: int | None = None
    clip_ids: list[int] | None = Field(default=None, max_length=1000)
    overwrite: bool = Field(default=False, description="Viết lại cả clip đã có (trừ prompt người dùng tự viết)")
    feedback: str = ""

    @model_validator(mode="after")
    def _one(self) -> ClipsTaskParams:
        if self.sequence_id is None and not self.clip_ids:
            raise ValueError("cần sequence_id hoặc clip_ids")
        return self


class AdaptParams(BaseModel):
    project_id: int
    text: str | None = Field(default=None, description="Văn bản truyện (dán thẳng)")
    upload_id: str | None = Field(default=None, description="Hoặc: file .txt đã upload (upload.start/finish)")
    target_episode_s: int = Field(default=75, ge=15, le=1800, description="Độ dài mục tiêu mỗi tập (giây)")
    max_episodes: int = Field(default=200, ge=1, le=500)
    feedback: str = ""

    @model_validator(mode="after")
    def _one(self) -> AdaptParams:
        if not (self.text or "").strip() and not self.upload_id:
            raise ValueError("cần text hoặc upload_id")
        return self


def _clip_scope(ctx: OpContext, p: ClipsTaskParams) -> int:
    if p.sequence_id is not None:
        return p.sequence_id
    with ctx.services.db.session() as s:
        rows = [entities.get(s, "clip", i) for i in dict.fromkeys(p.clip_ids or [])]
        ids = {c.sequence_id for c in rows}
    if len(ids) != 1:
        raise ValidationFailed("Các clip phải thuộc cùng một tập.")
    return ids.pop()


@op("story.write_bible", summary="AI viết Story Bible từ ý tưởng (nhân vật có nhãn nhân thiết, bối cảnh, quan hệ, câu "
    "hỏi mở) + tạo tài sản Library → job story.run", params=WriteBibleParams, long_running=True, mcp="tool")
def story_write_bible(ctx: OpContext, p: WriteBibleParams) -> dict:
    return submit(ctx, "write_bible", {"project_id": p.project_id, "idea": p.idea, "genre": p.genre,
                                       "feedback": p.feedback}, project_id=p.project_id)


@op("story.outline", summary="AI lập dàn ý một lô tập (hook đầu, điểm treo cuối) từ bible + bộ nhớ truyện → job",
    params=OutlineParams, long_running=True, mcp="tool")
def story_outline(ctx: OpContext, p: OutlineParams) -> dict:
    return submit(ctx, "outline", {"project_id": p.project_id, "episodes": p.episodes, "sequence_ids": p.sequence_ids,
                                   "arc": p.arc, "group_id": p.group_id, "feedback": p.feedback},
                  project_id=p.project_id)


@op("story.write_script", summary="AI viết kịch bản tập từ dàn ý + số liệu thể loại + bộ nhớ truyện (chữ chạy dần) → job",
    params=SequenceTaskParams, long_running=True, mcp="tool")
def story_write_script(ctx: OpContext, p: SequenceTaskParams) -> dict:
    return submit(ctx, "write_script", {"sequence_id": p.sequence_id, "feedback": p.feedback},
                  sequence_id=p.sequence_id)


@op("story.split_clips", summary="AI chia kịch bản thành cảnh + clip theo mẫu phủ cảnh (trường shot, thoại, tài sản "
    "theo mã) → job", params=SplitParams, long_running=True, mcp="tool")
def story_split_clips(ctx: OpContext, p: SplitParams) -> dict:
    return submit(ctx, "split_clips", {"sequence_id": p.sequence_id, "feedback": p.feedback, "replace": p.replace},
                  sequence_id=p.sequence_id)


def _clips_task(ctx: OpContext, task: str, p: ClipsTaskParams) -> dict:
    sid = _clip_scope(ctx, p)
    args = {"sequence_id": p.sequence_id, "clip_ids": p.clip_ids, "feedback": p.feedback, "overwrite": p.overwrite}
    return submit(ctx, task, args, sequence_id=sid)


@op("story.image_prompts", summary="AI viết prompt ảnh hàng loạt cho clip (mặc định chỉ clip còn trống) → job",
    params=ClipsTaskParams, long_running=True, mcp="tool")
def story_image_prompts(ctx: OpContext, p: ClipsTaskParams) -> dict:
    return _clips_task(ctx, "image_prompts", p)


@op("story.motion_prompts", summary="AI viết prompt chuyển động hàng loạt (mặc định chỉ clip còn trống) → job",
    params=ClipsTaskParams, long_running=True, mcp="tool")
def story_motion_prompts(ctx: OpContext, p: ClipsTaskParams) -> dict:
    return _clips_task(ctx, "motion_prompts", p)


@op("story.dialogue", summary="AI viết/rút gọn lời thoại cho clip (mặc định chỉ clip thoại quá dài) → job",
    params=ClipsTaskParams, long_running=True, mcp="tool")
def story_dialogue(ctx: OpContext, p: ClipsTaskParams) -> dict:
    return _clips_task(ctx, "dialogue", p)


@op("story.adapt_novel", summary="Chuyển thể truyện dài: tách tập theo độ dài mục tiêu, mỗi tập kết ở điểm treo, rút "
    "nhân vật + sự thật vào bible → job", params=AdaptParams, long_running=True, mcp="tool")
def story_adapt_novel(ctx: OpContext, p: AdaptParams) -> dict:
    return submit(ctx, "adapt_novel", {"project_id": p.project_id, "text": p.text, "upload_id": p.upload_id,
                                       "target_episode_s": p.target_episode_s, "max_episodes": p.max_episodes,
                                       "feedback": p.feedback}, project_id=p.project_id)


@op("story.memory_summarize", summary="Ghi bộ nhớ truyện sau tập: tóm tắt + đề xuất mục Story Bible pending kèm bằng "
    "chứng → job", params=SequenceTaskParams, long_running=True, mcp="tool")
def story_memory_summarize(ctx: OpContext, p: SequenceTaskParams) -> dict:
    return submit(ctx, "memory_summarize", {"sequence_id": p.sequence_id, "feedback": p.feedback},
                  sequence_id=p.sequence_id)
