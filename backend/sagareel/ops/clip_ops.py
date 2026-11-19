"""Thao tác nhóm `clip` (+ `review.queue`): tạo/sửa/đổi thứ tự/tách/nhân bản/xoá hai bước, tạo lại, nhờ AI sửa prompt.

`after_clip_id`: bỏ trống = cuối tập, 0 = đầu tập. Danh sách phân trang cursor theo rank (chịu được hàng nghìn clip).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ValidationFailed
from sagareel.core.ops import last_change_set_id, op
from sagareel.ops.project_ops import DeletePreviewOut
from sagareel.services import clips, entities, llm, takes


class ClipFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flags: list[str] | None = Field(default=None,
                                    description="Clip có ít nhất một cờ, vd. [\"stale_video\", \"drift\"]")
    review: list[str] | str | None = Field(default=None, description="pending/approved/rejected/skipped")
    scene_id: int | None = Field(default=None, description="Lọc theo cảnh (dùng kèm `no_scene` để lấy clip ngoài cảnh)")
    no_scene: bool = False
    asset_id: int | None = Field(default=None, description="Clip dùng tài sản này")
    variant_id: int | None = Field(default=None, description="… với look này (kèm asset_id)")
    has_take: bool = Field(default=False, description="Chỉ clip đã có take ảnh/video đang chọn (review=pending + "
                           "has_take = đúng số `takes_pending`)")

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.has_take:
            out["has_take"] = True
        if self.flags:
            out["flags"] = self.flags
        if self.review:
            out["review"] = self.review
        if self.no_scene:
            out["scene_id"] = None
        elif self.scene_id is not None:
            out["scene_id"] = self.scene_id
        if self.asset_id is not None:
            out["asset_id"] = self.asset_id
            if self.variant_id is not None:
                out["variant_id"] = self.variant_id
        return out


class ClipCreateParams(BaseModel):
    sequence_id: int
    after_clip_id: int | None = Field(default=None, description="Chèn sau clip này; bỏ trống = cuối, 0 = đầu")
    scene_id: int | None = None
    fields: dict[str, Any] = Field(default_factory=dict, description="Cột clip, vd. image_prompt, duration_ms, "
                                   "shot_size, assets, axis_side, cut_in, cut_out")


class ClipBulkCreateParams(BaseModel):
    sequence_id: int
    clips: list[dict[str, Any]] = Field(min_length=1, max_length=clips.MAX_BULK)
    after_clip_id: int | None = None
    scene_id: int | None = None


class ClipIdParams(BaseModel):
    id: int


class ClipListParams(BaseModel):
    sequence_id: int
    cursor: str | None = Field(default=None, description="`next_cursor` của trang trước")
    limit: int = Field(default=100, ge=1, le=clips.LIST_MAX)
    filter: ClipFilter | None = None


class ClipUpdateParams(BaseModel):
    id: int
    values: dict[str, Any]
    expected_updated_at: datetime | None = Field(
        default=None, description="`updated_at` lúc đọc; lệch (người khác đã sửa) → lỗi stale_write 409")


class ClipBulkUpdateParams(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=clips.MAX_BULK)
    values: dict[str, Any]


class ClipMoveParams(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=clips.MAX_BULK)
    after_clip_id: int | None = Field(default=None, description="Bỏ trống = cuối, 0 = đầu")
    scene_id: int | None = Field(default=None, description="Bỏ trống = giữ cảnh, 0 = bỏ khỏi cảnh")


class ClipSplitParams(BaseModel):
    id: int
    at_ms: int = Field(gt=0)


class ClipDuplicateParams(BaseModel):
    id: int
    after_clip_id: int | None = Field(default=None, description="Bỏ trống = ngay sau clip gốc")


class ClipDeletePreviewParams(BaseModel):
    ids: list[int] = Field(min_length=1)


class ClipDeleteParams(BaseModel):
    confirm_token: str | None = None


class RegenerateOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, description="Gửi nguyên văn thay cho prompt ghép")
    feedback: str | None = Field(default=None, description="Góp ý (lưu vào take.inputs)")
    assets: list[dict[str, Any]] | None = Field(default=None, description="Tài sản dùng cho lần này (không lưu)")
    refs: list[int] | None = Field(default=None, description="Ảnh tham chiếu (media id) thay cho ref ghép")
    duration: int | None = Field(default=None, ge=1, description="Giây (video)")
    connection: int | str | None = Field(default=None, description="Kết nối cho phần đang tạo (part=all: ảnh)")
    video_connection: int | str | None = Field(default=None, description="Kết nối video khi part=all")
    model: str | None = None
    takes: int = Field(default=1, ge=1, le=takes.MAX_TAKES)
    select: bool = Field(default=True, description="Tự chọn take mới khi xong (áp bảng làm-cũ)")


class ClipSelector(BaseModel):
    sequence_id: int
    filter: ClipFilter | None = None


class ClipRegenerateParams(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=takes.MAX_CLIPS)
    selector: ClipSelector | None = Field(default=None, description="Chọn theo tập + bộ lọc thay cho ids")
    part: Literal["image", "video", "audio", "subtitle", "all"] = "image"
    overrides: RegenerateOverrides = Field(default_factory=RegenerateOverrides)
    dry_run: bool = False


class ClipRewriteParams(BaseModel):
    id: int
    part: Literal["image", "video"] = "image"
    feedback: str = Field(min_length=1)
    expected_updated_at: datetime | None = None


class ReviewQueueParams(BaseModel):
    sequence_id: int | None = Field(default=None, description="Một tập (hoặc sequence_ids)")
    sequence_ids: list[int] | None = Field(default=None, max_length=500,
                                           description="Nhiều tập: theo thứ tự tập; mỗi item kèm sequence_id")
    filter: ClipFilter | None = Field(default=None, description="Mặc định review=pending; thêm has_take=true để "
                                      "khớp số takes_pending")
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=clips.LIST_MAX)


def _with_cs(out: dict) -> dict:
    """Kèm `change_set_id` của lần ghi (None = không có gì đổi) để UI hoàn tác đúng thao tác."""
    return {**out, "change_set_id": last_change_set_id()}


@op("clip.create", summary="Tạo một clip trong tập (chèn theo rank)", params=ClipCreateParams, mcp="tool")
def clip_create(ctx: OpContext, p: ClipCreateParams) -> dict:
    return {**clips.create(ctx, p.sequence_id, p.fields, p.after_clip_id, p.scene_id),
            "change_set_id": last_change_set_id()}


@op("clip.bulk_create", summary="Tạo nhiều clip liền nhau trong tập (một change set)", params=ClipBulkCreateParams,
    mcp="tool")
def clip_bulk_create(ctx: OpContext, p: ClipBulkCreateParams) -> dict:
    return {"items": clips.bulk_create(ctx, p.sequence_id, p.clips, p.after_clip_id, p.scene_id),
            "change_set_id": last_change_set_id()}


@op("clip.get", summary="Đọc một clip kèm lời thoại", params=ClipIdParams, read_only=True, idempotent=True,
    mcp="tool")
def clip_get(ctx: OpContext, p: ClipIdParams) -> dict:
    return clips.get(ctx, p.id)


@op("clip.list", summary="Liệt kê clip của tập theo thứ tự, lọc cờ/duyệt/cảnh/tài sản, phân trang cursor",
    params=ClipListParams, read_only=True, idempotent=True, mcp="tool")
def clip_list(ctx: OpContext, p: ClipListParams) -> dict:
    return clips.list_clips(ctx, p.sequence_id, p.cursor, p.limit, p.filter.as_dict() if p.filter else None)


@op("clip.update", summary="Sửa clip (hoàn tác được) + gắn cờ làm-cũ theo trường đổi", params=ClipUpdateParams,
    idempotent=True, mcp="tool")
def clip_update(ctx: OpContext, p: ClipUpdateParams) -> dict:
    return _with_cs(clips.update(ctx, p.id, p.values, p.expected_updated_at))


@op("clip.bulk_update", summary="Sửa cùng giá trị cho nhiều clip (một change set)", params=ClipBulkUpdateParams,
    idempotent=True, mcp="tool")
def clip_bulk_update(ctx: OpContext, p: ClipBulkUpdateParams) -> dict:
    return _with_cs(clips.bulk_update(ctx, p.ids, p.values))


@op("clip.move", summary="Đổi thứ tự clip (chỉ ghi rank của clip được di chuyển), tuỳ chọn đổi cảnh",
    params=ClipMoveParams, mcp="tool")
def clip_move(ctx: OpContext, p: ClipMoveParams) -> dict:
    return _with_cs(clips.move(ctx, p.ids, p.after_clip_id, p.scene_id))


@op("clip.split", summary="Tách clip tại một mốc (ms): hai clip cùng take, cắt bằng trim", params=ClipSplitParams)
def clip_split(ctx: OpContext, p: ClipSplitParams) -> dict:
    return _with_cs(clips.split(ctx, p.id, p.at_ms))


@op("clip.duplicate", summary="Nhân bản clip (prompt, tài sản, shot, lời thoại; không take)",
    params=ClipDuplicateParams)
def clip_duplicate(ctx: OpContext, p: ClipDuplicateParams) -> dict:
    return _with_cs(clips.duplicate(ctx, p.id, p.after_clip_id))


@op("clip.delete_preview", summary="Xem tác động của việc xoá clip và lấy confirm_token",
    params=ClipDeletePreviewParams, result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False,
    mcp="tool")
def clip_delete_preview(ctx: OpContext, p: ClipDeletePreviewParams) -> dict:
    return clips.delete_preview(ctx, p.ids)


@op("clip.delete", summary="Xoá (mềm) clip; take giữ nguyên, timeline neo sang clip kế", params=ClipDeleteParams,
    destructive=True, mcp="tool")
def clip_delete(ctx: OpContext, p: ClipDeleteParams) -> dict:
    return _with_cs(clips.delete(ctx, p.confirm_token))


@op("clip.regenerate", summary="Tạo lại ảnh/video của clip thành take mới (không ghi đè); dry_run chỉ ước tính",
    params=ClipRegenerateParams, costly=True, long_running=True, mcp="tool")
async def clip_regenerate(ctx: OpContext, p: ClipRegenerateParams) -> dict:
    selector = None
    if p.selector is not None:
        selector = {"sequence_id": p.selector.sequence_id,
                    **(p.selector.filter.as_dict() if p.selector.filter else {})}
    ov = p.overrides.model_dump(exclude_none=True)
    return await takes.regenerate(ctx, ids=p.ids, selector=selector, part=p.part, overrides=ov, dry_run=p.dry_run)


@op("clip.rewrite_prompt", summary="Nhờ AI viết lại prompt ảnh/video của clip theo góp ý (lưu phiên bản nguồn llm)",
    params=ClipRewriteParams, costly=True, long_running=True, mcp="tool")
async def clip_rewrite_prompt(ctx: OpContext, p: ClipRewriteParams) -> dict:
    field = "image_prompt" if p.part == "image" else "motion_prompt"

    def current() -> str:
        with ctx.services.db.session() as s:
            c = clips.get_clip(s, p.id)
            entities.check_fresh("clip", c, p.expected_updated_at)
            return getattr(c, field)

    old = await anyio.to_thread.run_sync(current)
    label = "image" if p.part == "image" else "video (motion)"
    variables = {"input": f"Current {label} prompt:\n{old}\n\nFeedback:\n{p.feedback}"}
    outcome = await llm.run_task(ctx.services, "rewrite_prompt", variables)
    new = outcome.text.strip()
    if not new:
        raise AppError("llm_empty", "LLM trả prompt rỗng.", hint="Thử lại hoặc đổi model cho công việc rewrite_prompt.",
                       status=502)

    def save() -> dict:
        src = {"source": "llm", "model": outcome.model, "feedback": p.feedback, "previous": old,
               "at": datetime.now().isoformat(timespec="seconds")}
        llm_ctx = dataclasses.replace(ctx, actor=f"llm:{outcome.model}")
        return clips.update(llm_ctx, p.id, {field: new, f"{field}_src": src}, p.expected_updated_at)

    if old == new:
        raise ValidationFailed("LLM trả lại đúng prompt cũ; không có gì để lưu.")
    out = await anyio.to_thread.run_sync(save)
    return {"clip": out, "part": p.part, "prompt": new, "previous": old, "model": outcome.model}


@op("review.queue", summary="Hàng duyệt: clip cần duyệt theo thứ tự (mặc định pending) kèm take đang chọn",
    params=ReviewQueueParams, read_only=True, idempotent=True, mcp="tool")
def review_queue(ctx: OpContext, p: ReviewQueueParams) -> dict:
    flt = p.filter.as_dict() if p.filter else None
    if p.sequence_ids:
        return takes.review_queue_many(ctx, p.sequence_ids, flt, p.cursor, p.limit)
    if p.sequence_id is None:
        raise ValidationFailed("Cần sequence_id hoặc sequence_ids.")
    return takes.review_queue(ctx, p.sequence_id, flt, p.cursor, p.limit)
