"""Thao tác nhóm `upload`: bắt đầu / xem trạng thái / hoàn tất upload chia khối (khối gửi bằng `PUT /api/upload/{id}`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import uploads
from sagareel.services.media import MediaOut

UploadKind = Literal["image", "audio", "video", "subtitle", "other"]


class UploadStartParams(BaseModel):
    name: str = Field(description="Tên file gốc (đuôi file phải khớp loại)")
    size: int = Field(gt=0, description="Tổng số byte của file")
    kind: UploadKind = Field(description="Loại: image (≤50 MB), audio (≤500 MB), video (≤4 GB), subtitle, other (file bảng)")
    project_id: int | None = Field(None, description="Gắn media vào project này (tuỳ chọn)")


class UploadIdParams(BaseModel):
    id: str = Field(description="Id phiên upload do `upload.start` trả")


class UploadFinishParams(BaseModel):
    id: str = Field(description="Id phiên upload")
    sha256: str = Field(description="sha256 hex (64 ký tự) của toàn bộ file để kiểm toàn vẹn")


class UploadStatus(BaseModel):
    id: str
    name: str
    kind: str
    size: int
    received: int = Field(description="Số byte server đã nhận — gửi khối kế tiếp với offset này")
    status: str = Field(description="uploading | done")
    complete: bool
    chunk_max: int = Field(description="Kích thước khối tối đa (byte)")
    media_id: int | None = None


@op("upload.start", summary="Bắt đầu upload chia khối (khối ≤ 8 MB, nối lại được); trả id và offset hiện có",
    params=UploadStartParams, result=UploadStatus)
def upload_start(ctx: OpContext, p: UploadStartParams) -> dict:
    return uploads.start(ctx.services, name=p.name, size=p.size, kind=p.kind, project_id=p.project_id)


@op("upload.status", summary="Xem số byte đã nhận của một upload để nối lại sau khi đứt", params=UploadIdParams,
    result=UploadStatus, read_only=True, idempotent=True)
def upload_status(ctx: OpContext, p: UploadIdParams) -> dict:
    return uploads.status(ctx.services, p.id)


@op("upload.finish", summary="Hoàn tất upload: kiểm kích thước + sha256 rồi đăng ký vào kho media",
    params=UploadFinishParams, result=MediaOut, idempotent=True)
def upload_finish(ctx: OpContext, p: UploadFinishParams) -> dict:
    return uploads.finish(ctx.services, p.id, p.sha256)
