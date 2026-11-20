"""Phục vụ file media và thumbnail.

- `GET /media/{media_id}`: `FileResponse` (Starlette tự xử lý `Range` → 206); `?download=1` thêm
  `Content-Disposition: attachment` — chỉ chủ (`role == "owner"`); còn lại (viewer, không có principal) nhận 403 `forbidden` (xem trực tiếp và thumbnail
  vẫn được).
- `GET /media/{media_id}/thumb?w=320`: JPEG cache theo `(sha256, w)` ở `cache_dir/thumbs`; `w` làm tròn lên
  {160, 320, 480, 720}. Ảnh: Pillow; video: khung đầu qua ffmpeg.

Xác thực do middleware làm (`request.state.principal`); route chỉ kiểm thêm vai trò cho `?download=1`.
"""

from __future__ import annotations

from pathlib import Path

import anyio
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from sagareel.container import Services
from sagareel.core.errors import ErrorPayload, Forbidden, NotFound
from sagareel.db.models import Media
from sagareel.services import media as media_svc

# Lỗi mô tả bằng hình `payload()` (như routes_ops) — FastAPI không tự thêm HTTPValidationError sai hình.
router = APIRouter(
    tags=["media"],
    responses={
        422: {"model": ErrorPayload, "description": "Dữ liệu đầu vào không hợp lệ (`validation_failed`)"},
        "default": {"model": ErrorPayload, "description": "Lỗi chuẩn `{ok: false, error_code, …}`"},
    },
)


# Chỉ các mime này được trình duyệt hiển thị trực tiếp (raster/video/âm thanh — không SVG, không HTML/XML/văn bản).
INLINE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp",
    "video/mp4", "video/quicktime", "video/webm", "video/x-matroska", "video/x-msvideo",
    "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac", "audio/ogg", "audio/flac",
})
# File người dùng tải lên phục vụ cùng origin với app: không đoán kiểu, không chạy script, không nhúng/điều hướng.
MEDIA_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def safe_file_response(path: Path, mime: str, *, filename: str | None = None) -> FileResponse:
    """FileResponse an toàn cho nội dung do người dùng đưa vào: mime ngoài `INLINE_MIMES` → octet-stream + attachment."""
    base = (mime or "").split(";", 1)[0].strip().lower()
    if base in INLINE_MIMES:
        return FileResponse(path, media_type=base, filename=filename, headers=dict(MEDIA_HEADERS),
                            content_disposition_type="attachment" if filename else "inline")
    return FileResponse(path, media_type="application/octet-stream", headers=dict(MEDIA_HEADERS),
                        filename=filename or path.name, content_disposition_type="attachment")


def _load(services: Services, media_id: int) -> tuple[Media, Path]:
    with services.db.session() as s:
        m = media_svc.get(s, media_id)
    return m, media_svc.file_path(services.settings, m)


@router.get("/media/{media_id}", response_class=FileResponse, summary="Tải file media (hỗ trợ Range)")
async def get_media(request: Request, media_id: int, download: bool = False) -> FileResponse:
    principal = getattr(request.state, "principal", None)
    if download and (principal is None or principal.role != "owner"):  # chặn mặc định: chỉ chủ được tải file gốc
        raise Forbidden("Chỉ chủ được tải file gốc; người xem chỉ xem trực tiếp hoặc ảnh thu nhỏ.")
    services: Services = request.app.state.services
    m, path = await anyio.to_thread.run_sync(_load, services, media_id)
    return safe_file_response(path, m.mime, filename=f"sagareel-{m.id}{path.suffix}" if download else None)


@router.get("/media/{media_id}/thumb", response_class=FileResponse, summary="Ảnh thu nhỏ JPEG của ảnh/video")
async def get_thumb(
    request: Request,
    media_id: int,
    w: int = Query(320, ge=1, description="Chiều rộng mong muốn; làm tròn lên 160/320/480/720 (tối đa 720)"),
) -> FileResponse:
    services: Services = request.app.state.services
    m, src = await anyio.to_thread.run_sync(_load, services, media_id)
    if m.kind not in media_svc.THUMB_KINDS:
        raise NotFound(f"Media #{m.id} ({m.kind}) không có ảnh thu nhỏ.", code="thumb_unavailable")
    width = media_svc.snap_width(w)
    out = media_svc.thumb_cache_path(services.settings, m.sha256, width)
    if not out.is_file():
        if m.kind == "image":
            await anyio.to_thread.run_sync(media_svc.image_thumb, src, out, width)
        else:
            await media_svc.video_thumb(src, out, width)
    return safe_file_response(out, "image/jpeg")
