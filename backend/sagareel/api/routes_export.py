"""`GET /api/export/{id}/zip` — tải cả gói xuất dạng zip stream (không tạo file tạm).

Chỉ chủ (`role == "owner"`) được tải, giống `?download=1` của `/media/{id}` (spec §23.2: người xem không tải file gốc).
Không hỗ trợ `Range` (độ dài zip không biết trước); tải từng file lẻ trong gói qua media nếu cần tua/tiếp tục.
"""

from __future__ import annotations

from urllib.parse import quote

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from sagareel.container import Services
from sagareel.core.errors import Conflict, ErrorPayload, Forbidden, NotFound
from sagareel.db.models import ExportPackage
from sagareel.services.export import package

router = APIRouter(
    tags=["export"],
    responses={"default": {"model": ErrorPayload, "description": "Lỗi chuẩn `{ok: false, error_code, …}`"}},
)


def _locate(services: Services, export_id: int):
    with services.db.session() as s:
        row = s.get(ExportPackage, export_id)
        if row is None or row.status == "superseded":
            raise NotFound(f"Không tìm thấy gói xuất #{export_id}.", code="export_not_found")
        if row.status != "done":
            raise Conflict(f"Gói xuất #{export_id} chưa xong (trạng thái {row.status}).", code="export_not_ready")
        root = services.settings.outputs_dir.resolve()
        folder = (root / row.folder).resolve()
        if not folder.is_relative_to(root) or not folder.is_dir():
            raise NotFound(f"Thư mục của gói xuất #{export_id} không còn trên đĩa.", code="export_folder_missing")
        return folder, f"{row.folder.replace('/', '_')}"


@router.get("/api/export/{export_id}/zip", response_class=StreamingResponse, summary="Tải gói xuất dạng zip (stream)")
async def export_zip(request: Request, export_id: int) -> StreamingResponse:
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.role != "owner":
        raise Forbidden("Chỉ chủ được tải gói xuất; người xem chỉ xem trực tiếp.")
    services: Services = request.app.state.services
    folder, name = await anyio.to_thread.run_sync(_locate, services, export_id)
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'",
               "Content-Disposition": f"attachment; filename=\"export-{export_id}.zip\"; filename*=UTF-8''{quote(name)}.zip"}
    return StreamingResponse(package.iter_zip(folder), media_type="application/zip", headers=headers)
