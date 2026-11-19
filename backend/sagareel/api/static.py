"""Phục vụ giao diện web đã build (`frontend/dist`) kèm SPA fallback.

- File có thật trong dist → trả file (`/assets/*` của Vite có hash nên cache lâu dài).
- Đường dẫn không có phần mở rộng (deep link như `/settings/providers`) → `index.html` để router phía client xử lý.
- `/api/*` và `/media/*` **không bao giờ** khớp route này (convertor loại trừ), nên lỗi 404 của API vẫn là JSON và
  router nào include sau (vd `/media/{id}`) vẫn nhận được request.
- dist chưa build → 404, không lỗi; kiểm tra lúc request nên build xong là phục vụ được ngay, không cần khởi động lại.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.convertors import Convertor, register_url_convertor

_INDEX = "index.html"
_NO_CACHE = {"Cache-Control": "no-cache"}
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

# Kiểu nội dung cố định, không phụ thuộc registry Windows (có máy map .js → text/plain làm trình duyệt chặn module).
for _type, _ext in (
    ("text/javascript", ".js"),
    ("text/javascript", ".mjs"),
    ("text/css", ".css"),
    ("font/woff2", ".woff2"),
    ("font/woff", ".woff"),
    ("image/svg+xml", ".svg"),
    ("application/json", ".json"),
    ("application/manifest+json", ".webmanifest"),
):
    mimetypes.add_type(_type, _ext)


class _SpaPathConvertor(Convertor[str]):
    """Mọi đường dẫn trừ `api`, `api/…`, `media`, `media/…`."""

    regex = r"(?!(?:api|media)(?:/|$)).*"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("sr_spa_path", _SpaPathConvertor())


def _not_found() -> Response:
    return JSONResponse(
        {
            "ok": False,
            "error_code": "not_found",
            "detail": "Không tìm thấy trang hoặc tệp này.",
            "hint": None,
            "status": 404,
            "extra": {},
        },
        status_code=404,
    )


def _safe_parts(rel: str) -> tuple[str, ...] | None:
    """Kiểm tra thuần chuỗi (không chạm hệ thống tệp): chặn dấu gạch ngược, `:`, ký tự điều khiển, `..`, đường dẫn tuyệt đối,
    UNC (`//host/share`) và ổ đĩa (`C:`). Trả về các phần đường dẫn an toàn, hoặc None."""
    if not rel or "\\" in rel or ":" in rel or any(ord(ch) < 32 or ord(ch) == 127 for ch in rel):
        return None
    win = PureWindowsPath(rel)
    posix = PurePosixPath(rel)
    if win.drive or win.root or posix.is_absolute():
        return None
    parts = tuple(part for part in rel.split("/") if part not in ("", "."))
    if not parts or ".." in parts:
        return None
    return parts


def _resolve_inside(dist: Path, rel: str) -> Path | None:
    """File thật của `rel` trong dist. Chặn chứa-trong bằng chuỗi (normpath + commonpath) TRƯỚC khi hỏi hệ thống
    tệp, để request lạ không bao giờ làm server mở đường dẫn ngoài dist (vd UNC → SMB ra máy khác)."""
    parts = _safe_parts(rel)
    if parts is None:
        return None
    root = os.path.normpath(os.path.abspath(dist))
    target = os.path.normpath(os.path.join(root, *parts))
    if os.path.commonpath([root, target]) != root:
        return None
    if not os.path.isfile(target):
        return None
    return Path(target)


def build_static_router(dist: Path) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.api_route("/{path:sr_spa_path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(path: str) -> Response:
        index = dist / _INDEX
        if not index.is_file():
            return _not_found()
        if path and path != _INDEX:
            if _safe_parts(path) is None:  # UNC, ổ đĩa, `..`, `\`…: từ chối ngay, không chạm hệ thống tệp
                return _not_found()
            found = _resolve_inside(dist, path)
            if found is not None:
                headers = _IMMUTABLE if PurePosixPath(path).parts[:1] == ("assets",) else None
                return FileResponse(found, headers=headers)
            if PurePosixPath(path).suffix:  # tệp tĩnh không có (js, css, ảnh…) → 404 thật, không trả index
                return _not_found()
        return FileResponse(index, headers=_NO_CACHE, media_type="text/html")

    return router
