"""`context.pack` + `prompts.submit` (spec §7.4 — chế độ viết `agent`/`manual`: app không gọi LLM)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services.story import author

Task = Literal["write_bible", "outline", "write_script", "split_clips", "image_prompts", "motion_prompts", "dialogue"]


class ContextPackParams(BaseModel):
    sequence_id: int | None = None
    clip_ids: list[int] | None = Field(default=None, max_length=1000,
                                       description="Chỉ các clip này (+ một clip trước/sau làm ngữ cảnh)")
    task: Task | None = Field(default=None, description="Bước đang viết: chỉ kèm template của bước này")

    @model_validator(mode="after")
    def _one(self) -> ContextPackParams:
        if self.sequence_id is None and not self.clip_ids:
            raise ValueError("cần sequence_id hoặc clip_ids")
        return self


class SubmitItem(BaseModel):
    clip_id: int | None = None
    sequence_id: int | None = None
    field: Literal["image_prompt", "motion_prompt", "dialogue", "script", "synopsis", "title", "clips"]
    value: Any = Field(description="Chuỗi; dialogue = [{speaker, text, emotion}]; clips = JSON như story_split_clips")

    @model_validator(mode="after")
    def _target(self) -> SubmitItem:
        if (self.clip_id is None) == (self.sequence_id is None):
            raise ValueError("cần đúng một trong clip_id / sequence_id")
        return self


class PromptsSubmitParams(BaseModel):
    items: list[SubmitItem] = Field(min_length=1, max_length=author.MAX_ITEMS)
    source: Literal["agent", "user", "import"] = Field(default="agent", description="Ghi vào *_src.by")
    name: str | None = Field(default=None, description="Tên agent/người viết (mặc định = actor)")
    model: str | None = Field(default=None, description="Model agent đã dùng (ghi vào *_src)")


@op("context.pack", summary="Gói ngữ cảnh để tự viết (style, thể loại, bible liên quan, tài sản + look, clip trước/sau, "
    "thoại, giới hạn provider, template)", params=ContextPackParams, read_only=True, idempotent=True, mcp="tool")
def context_pack(ctx: OpContext, p: ContextPackParams) -> dict:
    return author.pack(ctx, sequence_id=p.sequence_id, clip_ids=p.clip_ids, task=p.task)


@op("prompts.submit", summary="Gửi hàng loạt prompt ảnh/motion, thoại, kịch bản, dàn ý hoặc danh sách clip (nguồn agent) "
    "→ quy trình đang chờ nhập tự chạy tiếp", params=PromptsSubmitParams, mcp="tool")
def prompts_submit(ctx: OpContext, p: PromptsSubmitParams) -> dict:
    return author.submit(ctx, [i.model_dump() for i in p.items], source=p.source, name=p.name, model=p.model)
