"""Middleware bảo mật (pure ASGI): host tin cậy → xác thực → CSRF (spec §5.7).

1. **Host tin cậy**: tên host (bỏ cổng) ∈ loopback ∪ `remote.public_hosts`; sai/thiếu → 400 `untrusted_host`.
2. **Xác thực** → `request.state.principal` (`core.security.Principal` hoặc `None`):
   - có `Authorization: Bearer` → token API là nguồn duy nhất (token sai không bao giờ rơi về "chủ cục bộ");
   - cục bộ (`security.is_local`) và không bắt đăng nhập cục bộ → owner/local;
   - cookie `sr_session` hợp lệ → vai trò của phiên.
   Đường dẫn được bảo vệ (`security.is_protected_path`: `/api*` trừ health + 3 route auth, `/media*`, OpenAPI/docs)
   mà không có principal → 401 `auth_required`. File SPA luôn được phục vụ (SPA tự hiện màn đăng nhập).
3. **CSRF** cho method không an toàn (và websocket):
   - `Sec-Fetch-Site: cross-site` → chặn với mọi principal; phần dưới bỏ qua khi xác thực bằng Bearer;
   - có `Origin` thì phải là origin tin cậy (`security.origin_trusted`) — áp cả cho chủ cục bộ: trang lạ mở trong
     trình duyệt của chủ không gửi lệnh ghi tới 127.0.0.1:8990 được;
   - xác thực bằng cookie → header `X-SR-CSRF` phải bằng cookie `sr_csrf` **và** bằng token gắn với phiên
     (`security.csrf_for_session`). Riêng `/api/auth/login` chỉ kiểm Origin.
   Sai → 403 `csrf_failed`.

Middleware nằm **trong** `UnhandledErrorMiddleware` nhưng **ngoài** handler lỗi của FastAPI, nên tự trả JSON chuẩn
`payload()` cho lỗi của mình.
"""

from __future__ import annotations

import logging

import anyio
from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from sagareel.core import security
from sagareel.core.errors import AppError, internal_error, payload
from sagareel.core.security import Principal
from sagareel.services import auth

logger = logging.getLogger("sagareel.security")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SESSION_COOKIE = "sr_session"
CSRF_COOKIE = "sr_csrf"
CSRF_HEADER = "x-sr-csrf"
LOGIN_PATH = "/api/auth/login"
_MAX_TOKEN_LEN = 512


def bearer_token(value: str | None) -> str | None:
    """Token từ header `Authorization: Bearer <token>`; không phải Bearer → None; Bearer rỗng/quá dài → ""."""
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token if 0 < len(token) <= _MAX_TOKEN_LEN else ""


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] not in ("http", "websocket"):
            return  # loại scope lạ: không cho vào app
        try:
            error = await self._check(scope)
        except Exception:
            logger.exception("Lỗi khi kiểm tra bảo mật %s %s", scope.get("method"), scope.get("path"))
            error = internal_error()
        if error is not None:
            await self._reject(scope, receive, send, error)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, error: AppError) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse(payload(error), status_code=error.status)(scope, receive, send)

    async def _check(self, scope: Scope) -> AppError | None:
        conn = HTTPConnection(scope)
        services = scope["app"].state.services
        cfg = await auth.access_config_async(services)
        state = scope.setdefault("state", {})
        state["principal"] = None

        # (1) Host tin cậy.
        host = security.host_name(conn.headers.get("host"))
        if host is None or host not in cfg.trusted_hosts:
            return security.untrusted_host()

        # (2) Xác thực.
        protected = security.is_protected_path(scope["path"])
        principal: Principal | None = None
        raw_session: str | None = None
        bearer = bearer_token(conn.headers.get("authorization"))
        if bearer is not None:
            if bearer:
                principal = await anyio.to_thread.run_sync(auth.authenticate_token, services, bearer, cfg)
            if principal is None and protected:
                return security.auth_required("Token API không hợp lệ, đã hết hạn hoặc đã bị thu hồi.")
        elif security.is_local(conn) and not cfg.local_login_required:
            principal = Principal(role="owner", actor="user:owner", via="local")
        else:
            raw_session = conn.cookies.get(SESSION_COOKIE) or None
            if raw_session:
                principal = await anyio.to_thread.run_sync(auth.authenticate_session, services, raw_session, cfg)
        state["principal"] = principal
        if principal is None and protected:
            return security.auth_required()

        # (3) CSRF.
        unsafe = scope["type"] == "websocket" or scope.get("method", "GET").upper() not in SAFE_METHODS
        if not unsafe:
            return None
        if conn.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
            # Trình duyệt khẳng định yêu cầu đến từ site khác: chặn với mọi principal (kể cả Bearer).
            return security.csrf_failed("Yêu cầu ghi gửi từ một trang web khác (cross-site) bị chặn.")
        if principal is not None and principal.via == "token":
            return None
        origin = conn.headers.get("origin")
        if origin is not None and not security.origin_trusted(origin, cfg.public_hosts, services.settings.port):
            return security.csrf_failed("Yêu cầu ghi đến từ một trang web không được tin cậy (Origin lạ).")
        if (
            principal is not None
            and principal.via == "session"
            and scope["type"] == "http"
            and scope["path"] != LOGIN_PATH  # khớp đúng đường dẫn thô (không nới cho biến thể)
        ):
            header = conn.headers.get(CSRF_HEADER)
            cookie = conn.cookies.get(CSRF_COOKIE)
            expected = security.csrf_for_session(raw_session or "")
            if not (security.same_secret(header, cookie) and security.same_secret(header, expected)):
                return security.csrf_failed("Thiếu hoặc sai mã chống giả mạo yêu cầu (CSRF).")
        return None
