"""Thao tác nhóm `preset` (mẫu: nền tảng, thể loại, phụ đề…) và `export` (kiểm tra + gói xuất cho nền tảng).

Preset cài sẵn nạp lười từ `defaults/presets/<loại>/*.yaml` ở lần gọi `preset.*`/`export.*` đầu tiên (xem
`services/presets.py`). `export.create` tạo một job `export.package` (lane `ffmpeg`) cho cả lô tập x nền tảng x ngôn ngữ.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio
from pydantic import BaseModel, Field
from sqlalchemy import select

from sagareel.core.context import OpContext
from sagareel.core.errors import NotFound, ValidationFailed
from sagareel.core.ops import op
from sagareel.db.models import ExportPackage
from sagareel.services import jobs, presets
from sagareel.services.export import package

PresetKind = str


# --- preset.* -------------------------------------------------------------------------------------------------------


class PresetRef(BaseModel):
    id: int | None = Field(default=None, description="Id preset; hoặc dùng `kind` + `key`")
    kind: PresetKind | None = Field(default=None, description=f"Loại: {', '.join(presets.KINDS)}")
    key: str | None = None
    project_id: int | None = Field(default=None, description="Ưu tiên preset riêng của project (nếu có)")


class PresetListParams(BaseModel):
    kind: PresetKind | None = Field(default=None, description=f"Lọc theo loại: {', '.join(presets.KINDS)}")
    project_id: int | None = Field(default=None, description="Gồm cả preset riêng của project (che bản toàn cục cùng key)")


class PresetCreateParams(BaseModel):
    kind: PresetKind
    key: str = Field(description="Chữ thường, số, gạch ngang/dưới; vd. `tiktok-cau-chuyen`")
    name: str
    data: dict[str, Any] = Field(description="Nội dung; kind platform/genre được kiểm tra chặt theo schema")
    project_id: int | None = Field(default=None, description="Trống = toàn cục")


class PresetUpdateParams(PresetRef):
    name: str | None = None
    data: dict[str, Any] | None = Field(default=None, description="Thay toàn bộ `data` (đã kiểm tra theo loại)")
    expected_updated_at: datetime | None = Field(default=None, description="Lệch → lỗi stale_write 409")


class PresetDuplicateParams(BaseModel):
    id: int
    key: str | None = Field(default=None, description="Trống = `<key>-copy`")
    name: str | None = None
    to_project: bool = Field(default=False, description="Chép sang `project_id` thay vì cùng phạm vi bản gốc")
    project_id: int | None = None


class PresetIdParams(BaseModel):
    id: int


class PresetDeleteParams(BaseModel):
    confirm_token: str | None = Field(default=None, description="Lấy từ preset.delete_preview")


class PresetResetParams(BaseModel):
    key: str
    kind: PresetKind | None = Field(default=None, description="Bắt buộc khi key có ở nhiều loại")


@op("preset.list", summary="Liệt kê preset (nền tảng, thể loại, phụ đề…), lọc theo loại; có `modified` cho preset cài sẵn đã sửa",
    params=PresetListParams, read_only=True, idempotent=True, mcp="tool")
def preset_list(ctx: OpContext, p: PresetListParams) -> dict:
    return {"items": presets.list_presets(ctx, p.kind, p.project_id)}


@op("preset.get", summary="Đọc một preset theo id hoặc (kind, key)", params=PresetRef, read_only=True,
    idempotent=True, mcp="tool")
def preset_get(ctx: OpContext, p: PresetRef) -> dict:
    return presets.get_preset(ctx, p.id, p.kind, p.key, p.project_id)


@op("preset.create", summary="Tạo preset mới (nền tảng/thể loại được kiểm tra chặt)", params=PresetCreateParams,
    mcp="tool")
def preset_create(ctx: OpContext, p: PresetCreateParams) -> dict:
    return presets.create(ctx, p.kind, p.key, p.name, p.data, p.project_id)


@op("preset.update", summary="Sửa tên/dữ liệu preset (cả preset cài sẵn; hoàn tác được)", params=PresetUpdateParams,
    idempotent=True, mcp="tool")
def preset_update(ctx: OpContext, p: PresetUpdateParams) -> dict:
    return presets.update(ctx, p.id, p.kind, p.key, p.project_id, p.name, p.data, p.expected_updated_at)


@op("preset.duplicate", summary="Nhân bản preset (bản sao không phải cài sẵn) làm khuôn", params=PresetDuplicateParams)
def preset_duplicate(ctx: OpContext, p: PresetDuplicateParams) -> dict:
    return presets.duplicate(ctx, p.id, p.key, p.name, p.project_id, p.to_project)


@op("preset.delete_preview", summary="Xem tác động của việc xoá preset và lấy confirm_token (10 phút)",
    params=PresetIdParams, read_only=True, viewer=False)
def preset_delete_preview(ctx: OpContext, p: PresetIdParams) -> dict:
    return presets.delete_preview(ctx, p.id)


@op("preset.delete", summary="Xoá (mềm) preset — cần confirm_token từ preset.delete_preview; không xoá preset cài sẵn",
    params=PresetDeleteParams, destructive=True)
def preset_delete(ctx: OpContext, p: PresetDeleteParams) -> dict:
    return presets.delete(ctx, p.confirm_token)


@op("preset.reset_builtin", summary="Khôi phục preset cài sẵn về nội dung gốc (hồi sinh nếu đã xoá)",
    params=PresetResetParams, idempotent=True)
def preset_reset_builtin(ctx: OpContext, p: PresetResetParams) -> dict:
    return presets.reset_builtin(ctx, p.key, p.kind)


# --- export.* -------------------------------------------------------------------------------------------------------


class ExportCheckParams(BaseModel):
    sequence_id: int
    platform: str = Field(description="Key preset nền tảng: fb-reels, fb-video, yt-shorts, yt-long, tiktok…")
    lang: str | None = Field(default=None, description="Mặc định = ngôn ngữ của dự án")
    split_parts: bool = Field(default=False, description="Quá dài thì sẽ cắt phần → chỉ cảnh báo thay vì lỗi")
    cue_boxes: list[dict[str, Any]] | None = Field(
        default=None, description="Hộp chữ phụ đề theo px `{x, y, w, h, start_ms?}` trên khung bản dựng; có thì kiểm vùng an toàn")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Metadata đăng bài cần kiểm (title, description, hashtags, keywords); trống = mẫu từ tập")


class ExportCreateParams(BaseModel):
    sequence_ids: list[int] = Field(min_length=1)
    platforms: list[str] = Field(default_factory=list, description="Trống = nền tảng đích của tập/dự án")
    langs: list[Annotated[str, Field(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")]] = Field(default_factory=list, description="Trống = ngôn ngữ của dự án")
    options: package.ExportOptions = Field(default_factory=package.ExportOptions)
    dry_run: bool = Field(default=False, description="Chỉ trả danh sách gói sẽ tạo + kiểm tra ước tính, không dựng/ghi gì")


class ExportListParams(BaseModel):
    project_id: int | None = None
    sequence_id: int | None = None
    status: str | None = Field(default=None, description="pending/running/done/failed/cancelled/superseded")
    limit: int = Field(default=100, ge=1, le=500)


class ExportIdParams(BaseModel):
    id: int


@op("export.check", summary="Kiểm tra một tập trước khi xuất: độ dài, dung lượng, loudness, phụ đề ngoài vùng an toàn, "
    "metadata vượt giới hạn, nhạc thiếu giấy phép → {errors[], warnings[]}", params=ExportCheckParams,
    read_only=True, idempotent=True, mcp="tool")
async def export_check(ctx: OpContext, p: ExportCheckParams) -> dict:
    return await package.precheck(ctx.services, p.sequence_id, p.platform, p.lang, split_parts=p.split_parts,
                                  boxes=p.cue_boxes, metadata_override=p.metadata)


@op("export.create", summary="Tạo gói xuất cho (tập x nền tảng x ngôn ngữ): dựng bản cuối, đổi khung, cắt phần, bìa, "
    "phụ đề, metadata, manifest → job export.package; dry_run chỉ liệt kê + kiểm tra ước tính",
    params=ExportCreateParams, long_running=True, mcp="tool")
async def export_create(ctx: OpContext, p: ExportCreateParams) -> dict:
    services = ctx.services
    project, units = await anyio.to_thread.run_sync(package.load_targets, services, p.sequence_ids, p.platforms, p.langs)
    if p.dry_run or ctx.dry_run:
        out = []
        for u in units:
            rep = await package.precheck(services, u["sequence_id"], u["platform"], u["lang"], split_parts=p.options.split_parts)
            out.append({**u, "folder": package.rel_path(services, package.package_folder(
                services, project["key"], u["seq_key"], u["platform"], u["lang"])), **rep})
        return {"dry_run": True, "packages": out}
    batch = uuid.uuid4().hex[:12]

    def go() -> dict:
        ids = package.create_rows(services, project, units, p.options, batch)
        job = jobs.submit(services, package.JOB_TYPE, {"export_ids": ids, "batch": batch}, lane="ffmpeg",
                          project_id=project["id"], sequence_id=p.sequence_ids[0] if len(p.sequence_ids) == 1 else None,
                          max_attempts=2)
        return {"job_id": job["id"], "export_ids": ids, "batch": batch}

    return await anyio.to_thread.run_sync(go)


@op("export.list", summary="Liệt kê gói xuất (mới nhất trước), lọc theo dự án/tập/trạng thái", params=ExportListParams,
    read_only=True, idempotent=True, mcp="tool")
def export_list(ctx: OpContext, p: ExportListParams) -> list[dict]:
    with ctx.services.db.session() as s:
        stmt = select(ExportPackage).order_by(ExportPackage.id.desc())
        if p.project_id is not None:
            stmt = stmt.where(ExportPackage.project_id == p.project_id)
        if p.status:
            stmt = stmt.where(ExportPackage.status == p.status)
        else:
            stmt = stmt.where(ExportPackage.status != "superseded")
        rows = s.scalars(stmt.limit(p.limit * (5 if p.sequence_id is not None else 1))).all()
        if p.sequence_id is not None:
            rows = [r for r in rows if p.sequence_id in (r.sequence_ids or [])]
        return [package.row_dict(r) for r in rows[:p.limit]]


def _row(ctx: OpContext, export_id: int) -> ExportPackage:
    with ctx.services.db.session() as s:
        row = s.get(ExportPackage, export_id)
        if row is None:
            raise NotFound(f"Không tìm thấy gói xuất #{export_id}.", code="export_not_found")
        s.expunge(row)
        return row


@op("export.get", summary="Chi tiết một gói xuất (trạng thái, kiểm tra, metadata)", params=ExportIdParams,
    read_only=True, idempotent=True, mcp="tool")
def export_get(ctx: OpContext, p: ExportIdParams) -> dict:
    return package.row_dict(_row(ctx, p.id))


@op("export.open_folder", summary="Đường dẫn thư mục gói xuất trên máy chạy SagaReel (để mở trong trình quản lý file)",
    params=ExportIdParams, read_only=True, idempotent=True, viewer=False)
def export_open_folder(ctx: OpContext, p: ExportIdParams) -> dict:
    row = _row(ctx, p.id)
    folder = (ctx.services.settings.outputs_dir / row.folder).resolve()
    if not folder.is_dir():
        raise ValidationFailed(f"Thư mục {row.folder} chưa tồn tại (gói chưa xong?).", code="export_folder_missing")
    return {"path": str(folder), "zip_url": f"/api/export/{row.id}/zip"}

