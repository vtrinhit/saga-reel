"""Nhận khối upload: `PUT /api/upload/{upload_id}?offset=N` (body = bytes thô, ≤ 8 MB).

Xác thực/CSRF do middleware lo (đường dẫn `/api/*` được bảo vệ). Route tự kiểm vai trò chủ (không đi qua `check_role`).
Đọc body theo luồng và dừng ngay khi vượt 8 MB / kích thước khai báo / giới hạn theo loại — không nuốt cả body trước.
`start`/`status`/`finish` là op (`upload.*`).
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Query, Request

from sagareel.container import Services
from sagareel.core.errors import ErrorPayload, Forbidden
from sagareel.services import uploads

router = APIRouter(
    tags=["upload"],
    responses={
        "default": {"model": ErrorPayload, "description": "Lỗi chuẩn `{ok: false, error_code, …}`"},
    },
)


@router.put("/api/upload/{upload_id}", summary="Gửi một khối của upload chia khối (offset phải bằng số byte đã nhận)")
async def put_chunk(request: Request, upload_id: str, offset: int = Query(..., ge=0)) -> dict:
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.role != "owner":
        raise Forbidden("Chỉ chủ được tải file lên.")
    services: Services = request.app.state.services
    declared = request.headers.get("content-length")
    length = int(declared) if declared and declared.isdigit() else None
    # Từ chối sớm (offset sai, quá lớn, phiên không có) trước khi đọc body.
    await anyio.to_thread.run_sync(uploads.check_chunk, services, upload_id, offset, length)
    buf = bytearray()
    async for part in request.stream():
        buf += part
        if len(buf) > uploads.CHUNK_MAX:
            await anyio.to_thread.run_sync(uploads.check_chunk, services, upload_id, offset, len(buf))
    if not buf:
        await anyio.to_thread.run_sync(uploads.check_chunk, services, upload_id, offset, 0)
    view = await anyio.to_thread.run_sync(uploads.append, services, upload_id, offset, bytes(buf))
    return {"ok": True, **view}
