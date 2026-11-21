"""Thao tác Library (Task A): `asset_kind.*`, `asset.*`, `look.*`, `sheet.*`, xem trước prompt, drift check, chấm take.

Nghiệp vụ ở `services/library.py`, prompt ở `compile/compiler.py`. Op tốn tiền (`sheet.generate`,
`look.generate_matrix`) có `dry_run` trả ước tính; xoá đi hai bước `*.delete_preview` → `*.delete(confirm_token)`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import anyio
from pydantic import BaseModel, Field

from sagareel.compile import compiler
from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import confirm, library

_EXPECTED = Field(default=None, description="`updated_at` lúc đọc; lệch (người khác đã sửa) → lỗi stale_write 409")
_CONNECTION = Field(default=None, description="Tên hoặc id kết nối ảnh; bỏ trống = cài đặt image.connection")


class DeletePreviewOut(BaseModel):
    impact: dict[str, Any]
    confirm_token: str
    expires_in_s: int = confirm.TTL_S


class ConfirmParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Từ <loại>.delete_preview (10 phút)")


class IdParams(BaseModel):
    id: int


class UpdateParams(BaseModel):
    id: int
    values: dict[str, Any] = Field(description="Cột → giá trị mới")
    expected_updated_at: datetime | None = _EXPECTED


# --- asset_kind ---------------------------------------------------------------------------------------------------


class AssetKindListParams(BaseModel):
    project_id: int | None = Field(default=None, description="Bỏ trống = chỉ loại toàn cục")


@op("asset_kind.list", summary="Danh sách loại tài sản (toàn cục + của dự án) kèm góc sheet mặc định",
    params=AssetKindListParams, read_only=True, idempotent=True, mcp="tool")
def asset_kind_list(ctx: OpContext, p: AssetKindListParams) -> dict:
    return library.asset_kind_list(ctx, p.project_id)


class AssetKindCreateParams(BaseModel):
    key: str = Field(description="Mã loại (chữ thường không dấu, vd. vehicle)")
    label: str = Field(min_length=1, max_length=100)
    project_id: int | None = Field(default=None, description="Bỏ trống = loại toàn cục")
    icon: str = ""
    ref_priority: int = 0


@op("asset_kind.create", summary="Thêm loại tài sản", params=AssetKindCreateParams)
def asset_kind_create(ctx: OpContext, p: AssetKindCreateParams) -> dict:
    return library.asset_kind_create(ctx, p.key, p.label, p.project_id, p.icon, p.ref_priority)


@op("asset_kind.update", summary="Sửa loại tài sản (nhãn, icon, ưu tiên ref)", params=UpdateParams,
    idempotent=True)
def asset_kind_update(ctx: OpContext, p: UpdateParams) -> dict:
    return library.asset_kind_update(ctx, p.id, p.values, p.expected_updated_at)


# --- asset --------------------------------------------------------------------------------------------------------


class AssetCreateParams(BaseModel):
    project_id: int
    kind: str = Field(description="Mã loại: character/location/prop/…")
    name: str = Field(min_length=1, max_length=200)
    key: str | None = Field(default=None, description="Mã ngắn (vd. AN); bỏ trống = tự sinh từ tên")
    description: str = Field(default="", description="Mô tả khoá — dán nguyên văn vào mọi prompt synced")
    tags: list[str] | None = None


@op("asset.create", summary="Thêm tài sản (nhân vật/bối cảnh/đạo cụ) + look mặc định", params=AssetCreateParams,
    mcp="tool")
def asset_create(ctx: OpContext, p: AssetCreateParams) -> dict:
    return library.asset_create(ctx, p.project_id, p.kind, p.name, p.key, p.description, p.tags)


@op("asset.get", summary="Một tài sản kèm các look", params=IdParams, read_only=True, idempotent=True, mcp="tool")
def asset_get(ctx: OpContext, p: IdParams) -> dict:
    return library.asset_get(ctx, p.id)


class AssetListParams(BaseModel):
    project_id: int
    kind: str | None = None
    q: str | None = Field(default=None, description="Tìm theo tên/mã/mô tả")
    cursor: int | None = None
    limit: int = Field(default=100, ge=1, le=500)


@op("asset.list", summary="Danh sách tài sản của dự án (lọc theo loại, tìm chữ)", params=AssetListParams,
    read_only=True, idempotent=True, mcp="tool")
def asset_list(ctx: OpContext, p: AssetListParams) -> dict:
    return library.asset_list(ctx, p.project_id, p.kind, p.q, p.cursor, p.limit)


@op("asset.update", summary="Sửa tài sản (mô tả khoá, tên, mã, tag…)", params=UpdateParams, idempotent=True,
    mcp="tool")
def asset_update(ctx: OpContext, p: UpdateParams) -> dict:
    return library.asset_update(ctx, p.id, p.values, p.expected_updated_at)


@op("asset.delete_preview", summary="Tác động của việc xoá tài sản + confirm_token", params=IdParams,
    result=DeletePreviewOut, read_only=True, mcp="tool")
def asset_delete_preview(ctx: OpContext, p: IdParams) -> dict:
    return library.asset_delete_preview(ctx, p.id)


@op("asset.delete", summary="Xoá (mềm) tài sản — cần confirm_token", params=ConfirmParams, destructive=True,
    mcp="tool")
def asset_delete(ctx: OpContext, p: ConfirmParams) -> dict:
    return library.asset_delete(ctx, p.confirm_token)


class AssetDuplicateParams(BaseModel):
    id: int
    name: str | None = None
    key: str | None = None


@op("asset.duplicate", summary="Nhân bản tài sản cùng mọi look (dùng chung ảnh, không chép take)",
    params=AssetDuplicateParams, mcp="tool")
def asset_duplicate(ctx: OpContext, p: AssetDuplicateParams) -> dict:
    return library.asset_duplicate(ctx, p.id, p.name, p.key)


class DescribeParams(BaseModel):
    media_id: int
    kind: str = Field(default="character", description="character/location/prop")
    hint: str = Field(default="", description="Tên/ghi chú gợi ý cho LLM")
    project_id: int | None = None


@op("asset.describe_from_image", summary="LLM vision gợi ý mô tả/lock word từ một ảnh (job llm.run; không tự lưu)",
    params=DescribeParams, long_running=True, mcp="tool")
async def asset_describe_from_image(ctx: OpContext, p: DescribeParams) -> dict:
    return await anyio.to_thread.run_sync(lambda: library.describe_from_image(
        ctx, p.media_id, p.kind, p.hint, p.project_id))


# --- look ---------------------------------------------------------------------------------------------------------


class LookCreateParams(BaseModel):
    asset_id: int
    name: str = Field(min_length=1, max_length=200, description="Vd. \"Áo dài đỏ\", \"15 tuổi\"")
    description_delta: str = Field(default="", description="Khác biệt so với mô tả gốc — chèn nguyên văn")
    keep_features: list[str] | None = Field(default=None, description="Nét phải giữ")
    lock_word: str = Field(default="", description="Cụm \"màu + chất liệu + vật\" chèn nguyên văn mọi prompt")
    valid_from: int | None = Field(default=None, description="Từ tập số")
    valid_to: int | None = Field(default=None, description="Tới tập số")
    is_default: bool = False
    key: str | None = None


@op("look.create", summary="Thêm look cho tài sản", params=LookCreateParams, mcp="tool")
def look_create(ctx: OpContext, p: LookCreateParams) -> dict:
    return library.look_create(ctx, p.asset_id, p.name, p.description_delta, p.keep_features, p.lock_word,
                               p.valid_from, p.valid_to, p.is_default, p.key)


@op("look.update", summary="Sửa look (lock_word, delta, khoảng tập, mặc định…)", params=UpdateParams,
    idempotent=True, mcp="tool")
def look_update(ctx: OpContext, p: UpdateParams) -> dict:
    return library.look_update(ctx, p.id, p.values, p.expected_updated_at)


@op("look.delete_preview", summary="Tác động của việc xoá look + confirm_token", params=IdParams,
    result=DeletePreviewOut, read_only=True, mcp="tool")
def look_delete_preview(ctx: OpContext, p: IdParams) -> dict:
    return library.look_delete_preview(ctx, p.id)


@op("look.delete", summary="Xoá (mềm) look — cần confirm_token", params=ConfirmParams, destructive=True, mcp="tool")
def look_delete(ctx: OpContext, p: ConfirmParams) -> dict:
    return library.look_delete(ctx, p.confirm_token)


class SetAnchorParams(BaseModel):
    look_id: int
    media_id: int | None = Field(description="Ảnh mỏ neo (chính diện toàn thân); null = gỡ")


@op("look.set_anchor", summary="Đặt ảnh mỏ neo cho look", params=SetAnchorParams, idempotent=True, mcp="tool")
def look_set_anchor(ctx: OpContext, p: SetAnchorParams) -> dict:
    return library.look_set_anchor(ctx, p.look_id, p.media_id)


class AddRefsParams(BaseModel):
    look_id: int
    media_ids: list[int] = Field(min_length=1, max_length=20)


@op("look.add_refs", summary="Thêm ảnh tham chiếu cho look (cảnh báo khi > 6 ảnh)", params=AddRefsParams,
    idempotent=True, mcp="tool")
def look_add_refs(ctx: OpContext, p: AddRefsParams) -> dict:
    return library.look_add_refs(ctx, p.look_id, p.media_ids)


class MatrixParams(BaseModel):
    asset_id: int
    outfits: list[str] = Field(default_factory=list, description="Trang phục (chèn nguyên văn vào description_delta)")
    expressions: list[str] = Field(default_factory=list, description="Biểu cảm")
    generate_sheets: bool = Field(default=False, description="Gửi luôn sheet (anchor trước) cho mỗi look mới")
    views: list[str] | None = None
    takes: int = Field(default=1, ge=1, le=library.MAX_TAKES)
    connection: int | str | None = _CONNECTION
    dry_run: bool = False


@op("look.generate_matrix", summary="Tạo look con theo ma trận trang phục × biểu cảm (dry_run = ước tính)",
    params=MatrixParams, costly=True, long_running=True, mcp="tool")
async def look_generate_matrix(ctx: OpContext, p: MatrixParams) -> dict:
    return await library.look_generate_matrix(ctx, p.asset_id, p.outfits, p.expressions, p.generate_sheets,
                                              p.views, p.takes, p.connection, p.dry_run)


class LookPreviewParams(BaseModel):
    look_id: int
    view: str = "anchor"
    connection: int | str | None = _CONNECTION


@op("look.preview_prompt", summary="Xem trước prompt một góc sheet của look ({text, refs, warnings, provider_caps})",
    params=LookPreviewParams, read_only=True, idempotent=True, mcp="tool")
def look_preview_prompt(ctx: OpContext, p: LookPreviewParams) -> dict:
    with ctx.services.db.session() as s:
        return compiler.to_dict(compiler.compile_sheet(s, p.look_id, p.view, connection=p.connection))


# --- sheet --------------------------------------------------------------------------------------------------------


class SheetGenerateParams(BaseModel):
    look_id: int
    views: list[str] | None = Field(default=None, description="Mặc định theo loại: anchor, closeup, profile, back, "
                                                              "three_quarter (nhân vật)")
    takes: int = Field(default=1, ge=1, le=library.MAX_TAKES)
    connection: int | str | None = _CONNECTION
    dry_run: bool = False


@op("sheet.generate", summary="Tạo sheet cho look: anchor chỉ từ chữ, mỗi góc khác chỉ ref mỏ neo (job; dry_run)",
    params=SheetGenerateParams, costly=True, long_running=True, mcp="tool")
async def sheet_generate(ctx: OpContext, p: SheetGenerateParams) -> dict:
    return await library.sheet_generate(ctx, p.look_id, p.views, p.takes, p.connection, p.dry_run)


class SheetSelectParams(BaseModel):
    look_id: int
    view: str
    take_id: int


@op("sheet.select", summary="Chọn take cho một góc sheet (anchor → ảnh mỏ neo)", params=SheetSelectParams,
    idempotent=True, mcp="tool")
def sheet_select(ctx: OpContext, p: SheetSelectParams) -> dict:
    return library.sheet_select(ctx, p.look_id, p.view, p.take_id)


class LookIdParams(BaseModel):
    look_id: int


@op("sheet.list", summary="Các take sheet của look theo góc (đánh dấu take đang chọn)", params=LookIdParams,
    read_only=True, idempotent=True, mcp="tool")
def sheet_list(ctx: OpContext, p: LookIdParams) -> dict:
    return library.sheet_list(ctx, p.look_id)


# --- Prompt clip --------------------------------------------------------------------------------------------------


class ClipPreviewParams(BaseModel):
    clip_id: int
    part: Literal["image", "video"] = "image"
    connection: int | str | None = Field(default=None, description="Kết nối ảnh để lấy max_refs (chỉ part=image)")


@op("clip.preview_prompt", summary="Xem trước prompt cuối cùng của clip ({text, refs, warnings, provider_caps})",
    params=ClipPreviewParams, read_only=True, idempotent=True, mcp="tool")
async def clip_preview_prompt(ctx: OpContext, p: ClipPreviewParams) -> dict:
    if p.part == "image":
        await compiler.prepare_clip(ctx.services, p.clip_id)

    def run() -> dict:
        with ctx.services.db.session() as s:
            if p.part == "image":
                return compiler.to_dict(compiler.compile_clip_image(s, p.clip_id, connection=p.connection))
            return compiler.to_dict(compiler.compile_clip_motion(s, p.clip_id))

    return await anyio.to_thread.run_sync(run)


# --- Drift + chấm take --------------------------------------------------------------------------------------------


class DriftParams(BaseModel):
    clip_ids: list[int] = Field(default_factory=list, description="Kiểm take video (hoặc ảnh) đang chọn của clip")
    take_ids: list[int] = Field(default_factory=list)


@op("drift.check", summary="Drift check bằng LLM vision so với ảnh mỏ neo (job mỗi take; < 70 → flags.drift)",
    params=DriftParams, long_running=True, mcp="tool")
async def drift_check(ctx: OpContext, p: DriftParams) -> dict:
    return await anyio.to_thread.run_sync(lambda: library.submit_llm_checks(
        ctx, library.DRIFT_JOB, "drift_check", p.clip_ids, p.take_ids))


class ScoreParams(BaseModel):
    take_ids: list[int] = Field(min_length=1)


@op("take.score", summary="Chấm sơ bộ take bằng LLM vision vào auto_score (job mỗi take)", params=ScoreParams,
    long_running=True, mcp="tool")
async def take_score(ctx: OpContext, p: ScoreParams) -> dict:
    return await anyio.to_thread.run_sync(lambda: library.submit_llm_checks(
        ctx, library.SCORE_JOB, "score_take", [], p.take_ids))
