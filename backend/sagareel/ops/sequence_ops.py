"""Thao tác nhóm `sequence` (tập): CRUD, xoá hai bước, nhân bản, đổi thứ tự, lint ngữ pháp shot, tổng chi phí,
xuất shot list CSV/XLSX."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.ops.project_ops import DeletePreviewOut
from sagareel.services import sequences


class SequenceCreateParams(BaseModel):
    project_id: int
    title: str = Field(min_length=1, max_length=300, description="Tên tập")
    key: str | None = Field(default=None, description="Mã tập duy nhất trong dự án; bỏ trống = ep<number>")
    number: int | None = Field(default=None, ge=0, description="Số tập; bỏ trống = số lớn nhất + 1")
    group_id: int | None = Field(default=None, description="Nhóm (series/season/arc)")
    synopsis: str = ""
    script: str = ""
    target_platforms: list[str] | None = None


class SequenceIdParams(BaseModel):
    id: int


class SequenceListParams(BaseModel):
    project_id: int
    group_id: int | None = None
    cursor: str | None = Field(default=None, description="`next_cursor` của trang trước")
    limit: int = Field(default=100, ge=1, le=500)


class SequenceUpdateParams(BaseModel):
    id: int
    values: dict[str, Any] = Field(description="Cột → giá trị mới, vd. {\"title\": \"…\", \"script\": \"…\"}")
    expected_updated_at: datetime | None = Field(
        default=None, description="`updated_at` lúc đọc; lệch (người khác đã sửa) → lỗi stale_write 409")


class SequenceDeleteParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ sequence.delete_preview")


class SequenceDuplicateParams(BaseModel):
    id: int
    title: str | None = Field(default=None, description="Tên tập mới; bỏ trống = '<tên cũ> (bản sao)'")
    key: str | None = None


class SequenceReorderParams(BaseModel):
    project_id: int
    ids: list[int] = Field(min_length=1, description="Id tập theo thứ tự mới (số tập đánh lại 1..n)")


class SequenceCostParams(BaseModel):
    id: int
    per_clip: bool = Field(default=False, description="Kèm chi phí từng clip (sắp giảm dần)")


class SequenceShotlistParams(BaseModel):
    id: int
    format: Literal["csv", "xlsx"] = "csv"


@op("sequence.create", summary="Tạo tập mới trong dự án", params=SequenceCreateParams, mcp="tool")
def sequence_create(ctx: OpContext, p: SequenceCreateParams) -> dict:
    return sequences.create(ctx, p.project_id, p.title, key=p.key, number=p.number, group_id=p.group_id,
                            synopsis=p.synopsis, script=p.script, target_platforms=p.target_platforms)


@op("sequence.get", summary="Đọc một tập kèm số clip/cảnh/tổng thời lượng", params=SequenceIdParams,
    read_only=True, idempotent=True, mcp="tool")
def sequence_get(ctx: OpContext, p: SequenceIdParams) -> dict:
    return sequences.get(ctx, p.id)


@op("sequence.list", summary="Liệt kê tập của dự án theo số tập, phân trang cursor", params=SequenceListParams,
    read_only=True, idempotent=True, mcp="tool")
def sequence_list(ctx: OpContext, p: SequenceListParams) -> dict:
    return sequences.list_sequences(ctx, p.project_id, p.group_id, p.cursor, p.limit)


@op("sequence.update", summary="Sửa tập (hoàn tác được); kèm expected_updated_at để chống ghi đè",
    params=SequenceUpdateParams, idempotent=True, mcp="tool")
def sequence_update(ctx: OpContext, p: SequenceUpdateParams) -> dict:
    return sequences.update(ctx, p.id, p.values, p.expected_updated_at)


@op("sequence.delete_preview", summary="Xem tác động của việc xoá tập và lấy confirm_token (10 phút)",
    params=SequenceIdParams, result=DeletePreviewOut, read_only=True, idempotent=True, viewer=False)
def sequence_delete_preview(ctx: OpContext, p: SequenceIdParams) -> dict:
    return sequences.delete_preview(ctx, p.id)


@op("sequence.delete", summary="Xoá (mềm, hoàn tác được) tập — cần confirm_token từ sequence.delete_preview",
    params=SequenceDeleteParams, destructive=True)
def sequence_delete(ctx: OpContext, p: SequenceDeleteParams) -> dict:
    return sequences.delete(ctx, p.confirm_token)


@op("sequence.duplicate", summary="Nhân bản tập: cảnh, clip, lời thoại, timeline (không chép take)",
    params=SequenceDuplicateParams, mcp="tool")
def sequence_duplicate(ctx: OpContext, p: SequenceDuplicateParams) -> dict:
    return sequences.duplicate(ctx, p.id, p.title, p.key)


@op("sequence.reorder", summary="Đổi thứ tự tập trong dự án (đánh lại số tập)", params=SequenceReorderParams,
    idempotent=True)
def sequence_reorder(ctx: OpContext, p: SequenceReorderParams) -> dict:
    return sequences.reorder(ctx, p.project_id, p.ids)


@op("sequence.lint", summary="Kiểm tra ngữ pháp shot của tập (trục 180°, hướng màn hình, cỡ cảnh lặp, chuỗi continue, "
    "thoại quá dài) — chỉ cảnh báo", params=SequenceIdParams, read_only=True, idempotent=True, mcp="tool")
def sequence_lint(ctx: OpContext, p: SequenceIdParams) -> dict:
    return sequences.lint(ctx, p.id)


@op("sequence.cost_summary", summary="Tổng chi phí thật của tập (mọi take kể cả lần thử lại), theo phần",
    params=SequenceCostParams, read_only=True, idempotent=True, mcp="tool")
def sequence_cost_summary(ctx: OpContext, p: SequenceCostParams) -> dict:
    return sequences.cost_summary(ctx, p.id, p.per_clip)


@op("sequence.export_shotlist", summary="Xuất shot list của tập ra CSV/XLSX (outputs/shotlists) + link tải",
    params=SequenceShotlistParams, viewer=False)
def sequence_export_shotlist(ctx: OpContext, p: SequenceShotlistParams) -> dict:
    return sequences.export_shotlist(ctx, p.id, p.format)


class SequenceEstimateParams(BaseModel):
    id: int
    variant: Literal["draft", "animatic", "final"] = Field(default="final", description="Biến thể dựng để ước tính")
    platform: str | None = Field(default=None, description="Preset nền tảng (ảnh hưởng mã hoá bản cuối)")
    lang: str | None = Field(default=None, description="Ngôn ngữ thoại/phụ đề; trống = ngôn ngữ chính")
    generation: bool = Field(default=True, description="Kèm ước tính tạo ảnh/video/âm thanh cho clip còn thiếu")
    overrides: dict[str, Any] | None = Field(
        default=None, description="Như clip.regenerate.overrides (connection, video_connection…) cho phần tạo")


@op("sequence.estimate", summary="Ước tính cả tập trước khi chạy: tạo ảnh/video/TTS còn thiếu (dry_run) + dựng "
    "(khối, thời gian, dung lượng) — không tốn tiền, không ghi gì", params=SequenceEstimateParams, read_only=True,
    idempotent=True, mcp="tool")
async def sequence_estimate(ctx: OpContext, p: SequenceEstimateParams) -> dict:
    import anyio
    from sqlalchemy import select

    from sagareel.core.errors import AppError, payload
    from sagareel.db.models import Clip
    from sagareel.render import pipeline
    from sagareel.services import takes

    await anyio.to_thread.run_sync(sequences.get, ctx, p.id)  # 404 nếu không có tập
    render = await anyio.to_thread.run_sync(
        lambda: pipeline.estimate(ctx.services, p.id, p.variant, p.platform, p.lang))
    out: dict[str, Any] = {"sequence_id": p.id, "render": render, "generation": None}
    if not p.generation:
        return out

    def missing() -> list[int]:
        with ctx.services.db.session() as s:
            return list(s.scalars(select(Clip.id).where(
                Clip.sequence_id == p.id, Clip.deleted_at.is_(None),
                (Clip.video_take_id.is_(None)) | (Clip.image_take_id.is_(None) & (Clip.link != "continue")))))

    ids = await anyio.to_thread.run_sync(missing)
    if ids:
        try:
            gen = await takes.regenerate(ctx, ids=ids, part="all", overrides=p.overrides, dry_run=True)
            out["generation"] = {"clip_ids": ids, "estimate": gen["estimate"], "skipped": gen["skipped"],
                                 "warnings": gen["warnings"]}
        except AppError as e:  # vd. chưa cấu hình kết nối ảnh/video: ước tính dựng vẫn trả về
            out["generation"] = {"clip_ids": ids, "error": payload(e)}
    else:
        out["generation"] = {"clip_ids": [], "estimate": {"jobs": 0, "takes": 0, "parts": {}}}
    return out
