"""`/api/auth/*`: đăng nhập, đăng xuất, trạng thái xác thực (spec §5.7). Ba route này không cần đăng nhập.

Cookie:
- `sr_session`: token phiên ngẫu nhiên (DB chỉ giữ sha256) — HttpOnly, SameSite=Lax, Path=/, không Domain, Secure khi
  request là https, có `X-Forwarded-Proto: https`, hoặc Host thuộc `remote.public_hosts`. "Nhớ thiết bị" → Max-Age 30 ngày; ngược lại cookie
  phiên trình duyệt, phiên DB hết hạn sau 12 giờ.
- `sr_csrf`: token CSRF gắn với phiên (JS đọc được để gửi lại trong header `X-SR-CSRF`), cùng thuộc tính trừ HttpOnly.
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sagareel.api.middleware import CSRF_COOKIE, SESSION_COOKIE
from sagareel.core import security
from sagareel.core.errors import ErrorPayload
from sagareel.services import auth

_ERRORS: dict[int | str, dict[str, Any]] = {"default": {"model": ErrorPayload, "description": "Lỗi chuẩn"}}


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    remember: bool = False


def _secure(request: Request, cfg: auth.AccessConfig) -> bool:
    """Cookie `Secure` khi request là https, `X-Forwarded-Proto: https`, hoặc Host là host công khai (truy cập từ
    Internet luôn phải qua https của tunnel)."""
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    host = security.host_name(request.headers.get("host"))
    return request.url.scheme == "https" or proto == "https" or (host is not None and host in cfg.public_hosts)


def _set_cookies(response: JSONResponse, request: Request, raw: str, remember: bool, secure: bool) -> None:
    common: dict[str, Any] = {
        "path": "/",
        "samesite": "lax",
        "secure": secure,
        "max_age": int(auth.REMEMBER_TTL.total_seconds()) if remember else None,
    }
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, security.csrf_for_session(raw), httponly=False, **common)


def _clear_cookies(response: JSONResponse, secure: bool) -> None:
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", samesite="lax", secure=secure, httponly=httponly)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login", summary="Đăng nhập bằng mật khẩu (chủ sở hữu hoặc người xem)", responses=_ERRORS)
    async def login(request: Request, body: LoginBody) -> JSONResponse:
        services = request.app.state.services
        cfg = await auth.access_config_async(services)
        ip = security.client_ip(request, cfg.client_ip_header)
        rate = getattr(request.state, "rate_key", None) or security.rate_key(request, cfg.client_ip_header)
        # Chống dò: hạn mức theo khoá (429), làm chậm theo peer, tối đa 3 lần kiểm scrypt đồng thời.
        result = await auth.login(
            services, body.password, body.remember, ip, request.headers.get("user-agent", ""), rate
        )
        response = JSONResponse({
            "ok": True,
            "role": result.role,
            "remember": result.remember,
            "expires_at": result.expires_at.isoformat(),
        })
        _set_cookies(response, request, result.raw_token, result.remember, _secure(request, cfg))
        return response

    @router.post("/logout", summary="Đăng xuất: thu hồi phiên hiện tại và xoá cookie", responses=_ERRORS)
    async def logout(request: Request) -> JSONResponse:
        services = request.app.state.services
        raw = request.cookies.get(SESSION_COOKIE)
        if raw:
            await anyio.to_thread.run_sync(auth.logout, services, raw)
        response = JSONResponse({"ok": True})
        _clear_cookies(response, _secure(request, await auth.access_config_async(services)))
        return response

    @router.get("/status", summary="Trạng thái xác thực của request hiện tại", responses=_ERRORS)
    async def status(request: Request) -> dict:
        principal = getattr(request.state, "principal", None)
        cfg = await auth.access_config_async(request.app.state.services)
        return {
            "authenticated": principal is not None,
            "role": principal.role if principal else None,
            "via": principal.via if principal else None,
            "local": security.is_local(request),
            "password_set": auth.password_status(cfg),
            # Chưa có mật khẩu chủ: ai tới được cổng cục bộ cũng là chủ → UI cảnh báo "đừng mở ra Internet".
            "password_required_warning": not cfg.owner_password_set,
        }

    return router
