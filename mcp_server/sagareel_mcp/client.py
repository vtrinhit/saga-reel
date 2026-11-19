"""Client HTTP tới app SagaReel đang chạy (REST `/api/ops`), cùng quy tắc xác thực với CLI.

- URL: `SAGAREEL_URL` (mặc định `http://127.0.0.1:8990`).
- Token: `SAGAREEL_TOKEN` nếu đặt. Không đặt **và** URL là loopback → token agent cục bộ trong
  `<data>/local-agent.token` (đọc bằng `sagareel.local_agent`, module chỉ stdlib). Token file KHÔNG BAO GIỜ được gửi
  tới URL không phải loopback. Gặp 401 với token file → đọc lại file một lần (app khởi động lại có thể đã đổi token).
- Token không bao giờ được log hay in ra.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

URL_ENV = "SAGAREEL_URL"
TOKEN_ENV = "SAGAREEL_TOKEN"
DEFAULT_URL = "http://127.0.0.1:8990"
CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 300.0

# mcp_server/sagareel_mcp/client.py → <repo>/backend (chứa package sagareel; chỉ dùng sagareel.local_agent).
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


class AppError(Exception):
    """Lỗi chuẩn của app `{error_code, detail, hint, extra}` hoặc lỗi kết nối do client tự đặt mã."""

    def __init__(self, code: str, detail: str, hint: str | None = None, extra: dict | None = None,
                 status: int | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.hint = hint
        self.extra = extra or {}
        self.status = status

    def text(self) -> str:
        """Dạng như CLI: `error_code: detail (gợi ý: hint)` + chi tiết lỗi validate."""
        line = f"{self.code}: {self.detail}"
        if self.hint:
            line += f" (gợi ý: {self.hint})"
        lines = [line]
        extra = dict(self.extra)
        errors = extra.pop("errors", None)
        if isinstance(errors, list):
            for err in errors:
                if isinstance(err, dict):
                    loc = ".".join(str(x) for x in err.get("loc", [])) or "(params)"
                    lines.append(f"  - {loc}: {err.get('msg', '')}")
                else:
                    lines.append(f"  - {err}")
        elif errors is not None:
            extra["errors"] = errors
        if extra:
            lines.append("extra: " + json.dumps(extra, ensure_ascii=False))
        return "\n".join(lines)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def base_url() -> str:
    url = (os.environ.get(URL_ENV) or DEFAULT_URL).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AppError("invalid_url", f"{URL_ENV} không hợp lệ: {url!r}.", hint=f"Dùng dạng {DEFAULT_URL}.")
    return url


def read_file_token() -> str | None:
    """Token agent cục bộ qua `sagareel.local_agent` (stdlib-only). Không import được → None."""
    if BACKEND_DIR.is_dir() and str(BACKEND_DIR) not in sys.path:
        sys.path.append(str(BACKEND_DIR))
    try:
        from sagareel import local_agent
    except ImportError:
        return None
    return local_agent.read_local_token()


@dataclass
class TokenInfo:
    token: str | None
    source: str  # "env" | "file" | "none"


def resolve_token(url: str) -> TokenInfo:
    """Env trước; file chỉ khi URL loopback (không bao giờ gửi token file ra máy khác)."""
    env = os.environ.get(TOKEN_ENV)
    if env:
        return TokenInfo(env, "env")
    if is_loopback(urlsplit(url).hostname or ""):
        tok = read_file_token()
        if tok:
            return TokenInfo(tok, "file")
    return TokenInfo(None, "none")


class SagaReelClient:
    """Async client: `catalog()`, `call(op, params)`. Lỗi → `AppError` (không bao giờ ném lỗi httpx thô)."""

    def __init__(self, url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url or base_url()
        self._auth = resolve_token(self.url)
        loopback = is_loopback(urlsplit(self.url).hostname or "")
        self._http = httpx.AsyncClient(
            base_url=self.url,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "sagareel-mcp"},
            timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
            trust_env=not loopback,  # HTTP_PROXY không loại trừ 127.0.0.1
            follow_redirects=False,
        )

    @property
    def token(self) -> str | None:
        """Token app đang dùng (để HTTP transport của MCP so khớp bearer). Không được log."""
        return self._auth.token

    @property
    def token_source(self) -> str:
        return self._auth.source

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth.token}"} if self._auth.token else {}

    async def _send(self, method: str, path: str, *, body: Any = None, read_timeout: float | None = None
                    ) -> httpx.Response:
        kwargs: dict[str, Any] = {"headers": self._headers()}
        if body is not None:
            kwargs["json"] = body
        if read_timeout is not None:
            kwargs["timeout"] = httpx.Timeout(CONNECT_TIMEOUT_S, read=read_timeout)
        try:
            return await self._http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise AppError("app_not_running", f"Không kết nối được SagaReel Studio tại {self.url}.",
                           hint=f"Chạy `sagareel serve` hoặc đặt {URL_ENV} đúng địa chỉ app, rồi gọi `sr_reload`."
                           ) from None
        except httpx.TimeoutException:
            raise AppError("request_timeout", f"App không trả lời {method} {path} kịp thời gian chờ.",
                           hint="App vẫn chạy nhưng bận; thử lại sau.") from None
        except httpx.TransportError as e:
            raise AppError("connection_failed", f"Mất kết nối tới SagaReel Studio: {type(e).__name__}.",
                           hint="Kiểm tra app còn chạy không rồi thử lại.") from None

    async def request(self, method: str, path: str, *, body: Any = None, read_timeout: float | None = None) -> Any:
        r = await self._send(method, path, body=body, read_timeout=read_timeout)
        if r.status_code == 401 and self._auth.source in ("file", "none"):
            # App có thể vừa khởi động lại (token file mới) hoặc vừa đặt mật khẩu chủ: đọc lại token file một lần.
            fresh = resolve_token(self.url)
            if fresh.token and fresh.token != self._auth.token:
                self._auth = fresh
                r = await self._send(method, path, body=body, read_timeout=read_timeout)
        return _result(r, method, path)

    async def catalog(self) -> list[dict]:
        data = await self.request("GET", "/api/ops")
        if not isinstance(data, list):
            raise AppError("invalid_response", "GET /api/ops không trả danh sách thao tác.",
                           hint=f"Kiểm tra {URL_ENV} có trỏ đúng SagaReel Studio không.")
        return data

    async def call(self, op: str, params: dict | None = None, *, read_timeout: float | None = None) -> Any:
        return await self.request("POST", f"/api/ops/{op}", body=params or {}, read_timeout=read_timeout)


def _result(r: httpx.Response, method: str, path: str) -> Any:
    if r.status_code >= 400:
        try:
            data = r.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("error_code"), str):
            detail = data.get("detail") or data.get("message") or ""
            raise AppError(data["error_code"], str(detail), hint=data.get("hint"),
                           extra=data.get("extra") if isinstance(data.get("extra"), dict) else None,
                           status=r.status_code)
        raise AppError("http_error", f"Máy chủ trả HTTP {r.status_code} cho {method} {path}.",
                       hint=f"Kiểm tra {URL_ENV} có trỏ đúng SagaReel Studio không.", status=r.status_code)
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        raise AppError("invalid_response", f"Máy chủ trả dữ liệu không phải JSON cho {method} {path}.",
                       hint=f"Kiểm tra {URL_ENV} có trỏ đúng SagaReel Studio không.") from None
