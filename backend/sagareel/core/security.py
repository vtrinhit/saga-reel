"""Hàm bảo mật thuần (spec §5.7): băm mật khẩu, token bí mật, phân biệt cục bộ/từ xa, host/origin tin cậy.

Không truy cập DB. Phần dùng DB (phiên, token, cài đặt `remote.*`) ở `services/auth.py`; middleware ở
`api/middleware.py`.

**Cục bộ** = IP client là loopback **và** `Host` đúng một trong `127.0.0.1:<port>|localhost:<port>|[::1]:<port>`
**và** không có header chuyển tiếp nào. Tunnel (Cloudflare Tunnel, Tailscale, ngrok…) nối vào từ 127.0.0.1 nhưng luôn
gắn header chuyển tiếp nên bị coi là từ xa. Bộ chuyển tiếp TCP thô (ssh -L/-R, socat, frp kiểu tcp) thì **không** phân
biệt được — khi dùng chúng phải bật `remote.require_login_local`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import posixpath
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

import anyio
from starlette.requests import HTTPConnection

from sagareel.core.errors import AppError

# --- Mật khẩu -----------------------------------------------------------------------------------------------------

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SALT_BYTES = 16
DKLEN = 64
_MAX_SCRYPT_N = 2**20  # chuỗi lưu trong DB bị sửa tay cũng không làm treo/ngốn RAM máy chủ
_SCRYPT_MAXMEM = 256 * 1024 * 1024


def hash_password(pw: str) -> str:
    """`scrypt$16384$8$1$<salt_b64>$<hash_b64>` (salt 16 byte ngẫu nhiên)."""
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DKLEN)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(dk)}"


def verify_password(pw: str, stored: str) -> bool:
    """So mật khẩu với chuỗi đã băm (so sánh hằng thời gian). Chuỗi hỏng/không nhận ra → `False`, không ném lỗi."""
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        if not (2 <= n <= _MAX_SCRYPT_N and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        if not salt or not expected:
            return False
        dk = hashlib.scrypt(
            pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=_SCRYPT_MAXMEM
        )
    except (ValueError, TypeError, binascii.Error, MemoryError):
        return False
    return hmac.compare_digest(dk, expected)


# --- Token bí mật -------------------------------------------------------------------------------------------------

TOKEN_PREFIX = "sr_"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_secret_token() -> tuple[str, str]:
    """`(raw, sha256_hex(raw))`; raw = `sr_` + 32 byte urlsafe. Chỉ hash được lưu."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, sha256_hex(raw)


def new_session_token() -> tuple[str, str]:
    """Token phiên cookie `(raw, sha256_hex(raw))`."""
    raw = secrets.token_urlsafe(32)
    return raw, sha256_hex(raw)


def csrf_for_session(raw_session: str) -> str:
    """Token CSRF gắn với phiên: HMAC(khoá = token phiên, thông điệp cố định).

    Kẻ chỉ ghi đè được cookie `sr_csrf` (vd. từ subdomain) không tính ra được giá trị này vì không biết token phiên
    (cookie HttpOnly). Lộ token CSRF cũng không lộ token phiên.
    """
    mac = hmac.new(raw_session.encode("utf-8"), b"sagareel-csrf-v1", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def same_secret(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --- Principal ----------------------------------------------------------------------------------------------------

Role = Literal["owner", "viewer"]
Via = Literal["local", "session", "token"]


@dataclass(frozen=True)
class Principal:
    """Ai đang gọi; đặt ở `request.state.principal` (None = chưa xác thực)."""

    role: Role
    actor: str
    via: Via
    session_id: int | None = None
    token_id: int | None = None


# --- Host / cục bộ ------------------------------------------------------------------------------------------------

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Có bất kỳ header nào trong đây (kể cả rỗng) → request đi qua proxy/tunnel → từ xa. Năm header đầu theo spec, phần
# sau thêm cho chắc (chỉ có thể làm request "từ xa" hơn, không bao giờ ngược lại).
FORWARDING_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "forwarded",
    "cf-connecting-ip",
    "x-real-ip",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-forwarded-server",
    "x-original-forwarded-for",
    "true-client-ip",
    "cf-ray",
    "cf-visitor",
    "tailscale-user-login",
    "via",
)

_HOST_LABEL_RE = re.compile(r"^[a-z0-9_]([a-z0-9_.-]*[a-z0-9_])?$")


def host_name(value: str | None) -> str | None:
    """Tên host (chữ thường, bỏ cổng, bỏ ngoặc IPv6) từ giá trị header `Host`; không hợp lệ → `None`."""
    if not value:
        return None
    v = value.strip().lower()
    if v.startswith("["):
        end = v.find("]")
        if end == -1:
            return None
        name, rest = v[1:end], v[end + 1:]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        try:
            ipaddress.IPv6Address(name)
        except ValueError:
            return None
        return name
    name, sep, port = v.partition(":")
    if sep and not port.isdigit():
        return None
    if not name or not _HOST_LABEL_RE.match(name):
        return None
    return name


def normalize_public_hosts(value: Any) -> frozenset[str]:
    """Giá trị `remote.public_hosts` (kiểu json, có thể là bất cứ gì) → tập tên host hợp lệ.

    Chỉ nhận list chuỗi; chuỗi có thể là `host`, `host:port` hoặc URL (`https://host/…`). Phần tử khác bị bỏ qua.
    """
    if not isinstance(value, (list, tuple)):
        return frozenset()
    out = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        s = item.strip()
        if "://" in s:
            s = urlsplit(s).netloc.rpartition("@")[2]
        name = host_name(s)
        if name:
            out.add(name)
    return frozenset(out)


def _is_loopback_ip(value: str | None) -> bool:
    if not value:
        return False
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def has_forwarding_headers(conn: HTTPConnection) -> bool:
    return any(h in conn.headers for h in FORWARDING_HEADERS)


def local_host_values(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})


def is_local(request: HTTPConnection) -> bool:
    """Request đến thẳng từ máy này (không qua proxy/tunnel). Cổng lấy từ `AppSettings.port`."""
    client = request.client
    if client is None or not _is_loopback_ip(client.host):
        return False
    port = request.app.state.services.settings.port
    if request.headers.get("host", "").strip().lower() not in local_host_values(port):
        return False
    return not has_forwarding_headers(request)


def _valid_ip(value: str | None) -> str | None:
    """Chuỗi IP chuẩn hoá, hoặc None nếu không phải IP."""
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def trusted_client_ip(conn: HTTPConnection, trusted_header: str) -> str | None:
    """IP thật của client lấy từ header tin cậy (`remote.client_ip_header`), hoặc None.

    Chỉ tin đúng **một** header do tunnel/proxy ghi đè, và chỉ khi kết nối đến từ loopback (tunnel trên máy này).
    Nhiều dòng header hoặc nhiều giá trị cách dấu phẩy → lấy giá trị **cuối** (proxy gần nhất thêm vào). Giá trị không
    phải IP → coi như không có header. X-Forwarded-For / X-Real-IP không bao giờ được tin trừ khi chính nó là header
    đã cấu hình.
    """
    name = trusted_header.strip().lower()
    peer = conn.client.host if conn.client else ""
    if not name or not _is_loopback_ip(peer):
        return None
    values = conn.headers.getlist(name)
    if not values:
        return None
    return _valid_ip(values[-1].split(",")[-1])


def client_ip(conn: HTTPConnection, trusted_header: str = "") -> str:
    """IP để ghi vào phiên (hiển thị): IP từ header tin cậy, không có thì IP socket. Không dùng để quyết định quyền."""
    peer = conn.client.host if conn.client else ""
    return trusted_client_ip(conn, trusted_header) or peer[:64]


@dataclass(frozen=True)
class RateKey:
    """Khoá chống dò mật khẩu của một request (middleware tính, đặt ở `request.state.rate_key`).

    - `key`: khoá hạn mức cứng (5 lần sai/phút → 429); `None` = chỉ làm chậm (không từ chối).
    - `peer`: IP socket (bộ đếm làm chậm dần); `local`: request cục bộ → không bị làm chậm, không tính vào `peer`.
    """

    key: str | None
    peer: str
    local: bool


def rate_key(conn: HTTPConnection, trusted_header: str) -> RateKey:
    """- cục bộ → `local:<peer>` (bucket riêng: lưu lượng từ xa không bao giờ lấp được);
    - có IP hợp lệ từ header tin cậy → `ip:<ip>`;
    - không có, peer là loopback (proxy không gắn header, tunnel không đặt header đã cấu hình, header rỗng) → `None`:
      mọi khách từ xa chung peer này, từ chối cứng = khoá được chủ ngoài cửa, nên chỉ làm chậm;
    - kết nối trực tiếp không qua loopback → `ip:<peer>`.
    """
    peer = conn.client.host if conn.client else ""
    if is_local(conn):
        return RateKey(f"local:{peer}", peer, True)
    ip = trusted_client_ip(conn, trusted_header)
    if ip:
        return RateKey(f"ip:{ip}", peer, False)
    if _is_loopback_ip(peer) or _valid_ip(peer) is None:
        return RateKey(None, peer, False)
    return RateKey(f"ip:{_valid_ip(peer)}", peer, False)


def origin_trusted(origin: str, public_hosts: frozenset[str], port: int) -> bool:
    """`Origin` hợp lệ: `http(s)://<host công khai>` (mọi cổng), hoặc chính địa chỉ loopback của app (đúng cổng).

    Origin loopback khác cổng (ứng dụng cục bộ khác) không được tin.
    """
    try:
        parts = urlsplit(origin.strip())
        if parts.scheme not in ("http", "https") or parts.path not in ("", "/") or parts.query or parts.username:
            return False
        name = (parts.hostname or "").lower()
        o_port = parts.port
    except ValueError:
        return False
    if not name:
        return False
    if name in public_hosts:
        return True
    if name in LOOPBACK_HOSTS:
        return parts.scheme == "http" and o_port == port
    return False


# --- Đường dẫn ----------------------------------------------------------------------------------------------------

PUBLIC_API_PATHS = frozenset({"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/status"})
_PROTECTED_PREFIXES = ("/api", "/media", "/openapi.json", "/docs", "/redoc")
_SLASHES_RE = re.compile(r"/{2,}")


def normalize_path(path: str) -> str:
    """Dạng so sánh của đường dẫn: dấu gạch ngược → `/`, giải mã `%xx` (tối đa 3 lớp), gộp `//`, chữ thường.

    Không xử lý `.`/`..` (xem `is_protected_path`).
    """
    p = path.replace("\\", "/")
    for _ in range(3):
        q = unquote(p)
        if q == p:
            break
        p = q.replace("\\", "/")
    return _SLASHES_RE.sub("/", "/" + p.lstrip("/")).lower()


def _protected_form(p: str) -> bool:
    if p in PUBLIC_API_PATHS:
        return False
    return any(p == pre or p.startswith(pre + "/") for pre in _PROTECTED_PREFIXES)


def is_protected_path(path: str) -> bool:
    """Mặc định chặn: `/api*` (trừ health + 3 route auth, khớp đúng), `/media*`, OpenAPI/docs. Còn lại là file SPA.

    Xét cả dạng chưa và đã giải `.`/`..`; dạng nào được bảo vệ thì chặn (vd. `/media/../api/health` và
    `/api/ops/../health` đều bị chặn — route `{path:path}` có thể khớp đường dẫn thô).
    """
    p = normalize_path(path)
    resolved = posixpath.normpath(p)
    return _protected_form(p) or _protected_form(resolved if resolved.startswith("/") else "/" + resolved)


# --- Lỗi chuẩn ----------------------------------------------------------------------------------------------------


def untrusted_host() -> AppError:
    return AppError(
        "untrusted_host",
        "Tên miền (Host) của yêu cầu không nằm trong danh sách được phép.",
        hint="Thêm tên miền công khai vào cài đặt `remote.public_hosts` (đổi từ máy cục bộ).",
        status=400,
    )


def auth_required(detail: str = "Cần đăng nhập để truy cập.") -> AppError:
    return AppError(
        "auth_required",
        detail,
        hint="Đăng nhập bằng mật khẩu, hoặc gửi `Authorization: Bearer <token API>`.",
        status=401,
    )


def csrf_failed(detail: str) -> AppError:
    return AppError(
        "csrf_failed",
        detail,
        hint="Tải lại trang rồi thử lại. Yêu cầu ghi từ trình duyệt phải gửi header `X-SR-CSRF` bằng cookie `sr_csrf`.",
        status=403,
    )


# --- Chống dò mật khẩu --------------------------------------------------------------------------------------------


def too_many_attempts(retry_after: int) -> AppError:
    return AppError(
        "too_many_attempts",
        "Nhập sai mật khẩu quá nhiều lần. Vui lòng thử lại sau.",
        hint=f"Thử lại sau khoảng {retry_after} giây.",
        status=429,
        extra={"retry_after": retry_after},
    )


Ticket = tuple[str, str, float]  # (bảng, khoá, mốc thời gian)


class LoginLimiter:
    """Chống dò mật khẩu (đăng nhập và `current_password` của `auth.set_password`), cửa sổ 60 s. Mỗi app một bộ.

    - **Theo khoá** (`RateKey.key`): cứng 5 lần/phút → 429. Lần thử được giữ chỗ trước khi kiểm (gửi đồng thời không
      vượt được); đúng mật khẩu thì trả chỗ.
    - **Theo peer** (IP socket, trừ request cục bộ): không bao giờ từ chối — chậm dần. Lần thử đang chạy cũng được giữ
      chỗ trước khi ngủ, nên N request song song tự làm chậm nhau: độ trễ = `step × max(0, n − free)` (n = số lần
      sai + đang thử khác trong cửa sổ), tối đa `max_delay`. Mật khẩu đúng vẫn qua.
    - **Kiểm mật khẩu** (scrypt) chạy trong `verify_limiter` riêng (`anyio.CapacityLimiter(3)`): không chiếm pool thread
      chung, xác thực cookie/token vẫn nhanh khi bị dò dồn dập.
    - Bảng khoá giới hạn `max_keys` (bỏ khoá cũ nhất khi đầy).
    """

    def __init__(
        self,
        per_key: int = 5,
        window: float = 60.0,
        free: int = 5,
        step: float = 0.1,
        max_delay: float = 3.0,
        max_keys: int = 10_000,
        verify_concurrency: int = 3,
    ) -> None:
        self.per_key, self.window = per_key, window
        self.free, self.step, self.max_delay = free, step, max_delay
        self.max_keys = max_keys
        self.verify_limiter = anyio.CapacityLimiter(verify_concurrency)
        self._lock = threading.Lock()
        self._tables: dict[str, dict[str, deque[float]]] = {"key": {}, "peer": {}}

    def table_size(self, table: str) -> int:
        with self._lock:
            return len(self._tables[table])

    def _bucket(self, table: str, key: str, now: float) -> deque[float]:
        t = self._tables[table]
        dq = t.pop(key, None)  # lấy ra rồi chèn lại: khoá vừa dùng nằm cuối (LRU)
        if dq is None:
            if len(t) >= self.max_keys:
                for k in [k for k, d in t.items() if not d or d[-1] <= now - self.window]:
                    del t[k]
                while len(t) >= self.max_keys:
                    del t[next(iter(t))]
            dq = deque()
        t[key] = dq
        while dq and dq[0] <= now - self.window:
            dq.popleft()
        return dq

    def acquire(self, key: str) -> Ticket:
        """Giữ chỗ một lần thử cho `key`; đã đủ 5 (kể cả đang thử) trong 60 s → `too_many_attempts`."""
        now = time.monotonic()
        with self._lock:
            dq = self._bucket("key", key, now)
            if len(dq) >= self.per_key:
                raise too_many_attempts(max(1, int(dq[0] + self.window - now) + 1))
            dq.append(now)
        return "key", key, now

    def reserve_peer(self, peer: str) -> tuple[Ticket, float]:
        """Giữ chỗ trong bộ đếm của `peer`; trả (vé, số giây phải chờ trước khi kiểm mật khẩu)."""
        now = time.monotonic()
        with self._lock:
            dq = self._bucket("peer", peer, now)
            n = len(dq)
            dq.append(now)
        return ("peer", peer, now), min(self.max_delay, self.step * max(0, n - self.free))

    def delay(self, peer: str) -> float:
        """Độ trễ hiện tại của `peer` (không giữ chỗ)."""
        with self._lock:
            n = len(self._bucket("peer", peer, time.monotonic()))
        return min(self.max_delay, self.step * max(0, n - self.free))

    def release(self, ticket: Ticket) -> None:
        """Trả chỗ (mật khẩu đúng: không tính là lần sai)."""
        table, key, stamp = ticket
        with self._lock:
            dq = self._tables[table].get(key)
            if dq is not None:
                try:
                    dq.remove(stamp)
                except ValueError:
                    pass


# --- Cache TTL ----------------------------------------------------------------------------------------------------


class TTLCache:
    """Giữ một giá trị trong `ttl` giây; `invalidate()` xoá ngay (vd. khi cài đặt `remote.*` đổi)."""

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._value: Any = None
        self._expires = 0.0
        self._generation = 0

    def get(self) -> Any:
        with self._lock:
            if self._value is not None and time.monotonic() < self._expires:
                return self._value
            return None

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def put(self, value: Any, generation: int) -> None:
        """Chỉ lưu nếu không có `invalidate()` nào xảy ra kể từ lúc bắt đầu đọc (`generation`)."""
        with self._lock:
            if generation == self._generation:
                self._value = value
                self._expires = time.monotonic() + self.ttl

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._value = None
            self._expires = 0.0
