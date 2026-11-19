"""Phục vụ giao diện web đã build (`frontend/dist`) kèm SPA fallback.

- File có thật trong dist → trả file (`/assets/*` của Vite có hash nên cache lâu dài).
- Đường dẫn không có phần mở rộng (deep link như `/settings/providers`) → `index.html` để router phía client xử lý.
- `/api/*` và `/media/*` **không bao giờ** khớp route này (convertor loại trừ), nên lỗi 404 của API vẫn là JSON và
  router nào include sau (vd `/media/{id}`) vẫn nhận được request.
- dist chưa build → 404, không lỗi; kiểm tra lúc request nên build xong là phục vụ được ngay, không cần khởi động lại.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.convertors import Convertor, register_url_convertor

_INDEX = "index.html"
_NO_CACHE = {"Cache-Control": "no-cache"}
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


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


def _resolve_inside(dist: Path, rel: str) -> Path | None:
    """Đường dẫn thật của `rel` nếu nằm trong dist và là file; chặn `..` và đường dẫn tuyệt đối."""
    try:
        root = dist.resolve()
        target = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def build_static_router(dist: Path) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.api_route("/{path:sr_spa_path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(path: str) -> Response:
        index = dist / _INDEX
        if not index.is_file():
            return _not_found()
        if path and path != _INDEX:
            found = _resolve_inside(dist, path)
            if found is not None:
                headers = _IMMUTABLE if PurePosixPath(path).parts[:1] == ("assets",) else None
                return FileResponse(found, headers=headers)
            if PurePosixPath(path).suffix:  # tệp tĩnh không có (js, css, ảnh…) → 404 thật, không trả index
                return _not_found()
        return FileResponse(index, headers=_NO_CACHE, media_type="text/html")

    return router
