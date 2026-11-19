"""Đăng nhập, phiên, token API và cấu hình truy cập từ xa (spec §5.7, §23.2).

- Bảng `auth_secret`, `session`, `api_token` là `Plain`: ghi bằng `db.session()` thường, **không bao giờ** qua
  UnitOfWork → hash mật khẩu/token không vào revision, không hoàn tác được.
- Chỉ lưu hash: mật khẩu băm scrypt, token phiên/token API lưu sha256. Token API thô chỉ trả về một lần lúc tạo.
- Vai trò `viewer` chỉ tồn tại khi `remote.allow_viewer=true` **và** đã đặt mật khẩu người xem; kiểm lại ở mọi
  request (tắt `allow_viewer` cắt ngay phiên và token viewer đang có, sau tối đa 5 s nếu đổi qua đường khác
  `settings.set_global`).
- Hàm ở đây đồng bộ (dùng DB trực tiếp); code async gọi qua `anyio.to_thread.run_sync`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sagareel.container import Services
from sagareel.core import security
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Forbidden, NotFound, ValidationFailed
from sagareel.core.security import Principal
from sagareel.db.base import utcnow
from sagareel.db.models import ApiToken, AuthSecret, AuthSession
from sagareel.services.settings import global_layer

ROLES = ("owner", "viewer")
SESSION_TTL = timedelta(hours=12)
REMEMBER_TTL = timedelta(days=30)
LAST_USED_RESOLUTION = timedelta(seconds=60)  # ghi `last_used_at` tối đa mỗi phút/token: không giành khoá ghi
USER_AGENT_MAX = 300


# --- Cấu hình truy cập (cache 5 s) ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessConfig:
    public_hosts: frozenset[str]
    require_login_local: bool
    allow_viewer: bool
    owner_password_set: bool
    viewer_password_set: bool
    client_ip_header: str = "cf-connecting-ip"

    @property
    def trusted_hosts(self) -> frozenset[str]:
        return security.LOOPBACK_HOSTS | self.public_hosts

    @property
    def viewer_enabled(self) -> bool:
        return self.allow_viewer and self.viewer_password_set

    @property
    def local_login_required(self) -> bool:
        # Chưa có mật khẩu chủ thì bỏ qua cờ: bật nhầm không được tự khoá chủ ngoài cửa.
        return self.require_login_local and self.owner_password_set


def _secret_roles(s: Session) -> set[str]:
    return set(s.scalars(select(AuthSecret.role)))


def load_access_config(services: Services) -> AccessConfig:
    """Đọc thẳng từ DB (không cache)."""
    resolver = services.resolver
    with services.db.session() as s:
        layers = [global_layer(s)]
        roles = _secret_roles(s)
    value = lambda key: resolver.resolve(key, layers).value  # noqa: E731
    return AccessConfig(
        public_hosts=security.normalize_public_hosts(value("remote.public_hosts"))
        | security.normalize_public_hosts(list(services.settings.public_hosts)),
        require_login_local=value("remote.require_login_local") is True,
        allow_viewer=value("remote.allow_viewer") is True,
        owner_password_set="owner" in roles,
        viewer_password_set="viewer" in roles,
        client_ip_header=ip_header if isinstance(ip_header := value("remote.client_ip_header"), str) else "",
    )


def access_config(services: Services) -> AccessConfig:
    """Cấu hình truy cập, cache 5 s trên `services.auth_cache` (xoá ngay khi đổi cài đặt/mật khẩu)."""
    cfg = services.auth_cache.get()
    if cfg is None:
        generation = services.auth_cache.generation()
        cfg = load_access_config(services)
        services.auth_cache.put(cfg, generation)
    return cfg


async def access_config_async(services: Services) -> AccessConfig:
    cfg = services.auth_cache.get()
    if cfg is not None:
        return cfg
    return await anyio.to_thread.run_sync(access_config, services)


def invalidate(services: Services) -> None:
    services.auth_cache.invalidate()


# --- Xác thực request ---------------------------------------------------------------------------------------------


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at <= now


def _role_allowed(role: str, cfg: AccessConfig) -> bool:
    return role == "owner" or (role == "viewer" and cfg.viewer_enabled)


def authenticate_token(services: Services, raw: str, cfg: AccessConfig) -> Principal | None:
    """Token API (`Authorization: Bearer`) hợp lệ → Principal `agent:<tên>`; sai/hết hạn/thu hồi → None."""
    now = utcnow()
    with services.db.session() as s:
        row = s.scalars(select(ApiToken).where(ApiToken.token_hash == security.sha256_hex(raw))).first()
        if row is None or row.revoked_at is not None or _expired(row.expires_at, now):
            return None
        if not _role_allowed(row.role, cfg):
            return None
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION:
            row.last_used_at = now
        return Principal(role=row.role, actor=f"agent:{row.name}", via="token", token_id=row.id)


def authenticate_session(services: Services, raw: str, cfg: AccessConfig) -> Principal | None:
    """Cookie `sr_session` hợp lệ → Principal `user:<role>`; sai/hết hạn/thu hồi → None."""
    now = utcnow()
    with services.db.session() as s:
        row = s.scalars(select(AuthSession).where(AuthSession.token_hash == security.sha256_hex(raw))).first()
        if row is None or row.revoked_at is not None or _expired(row.expires_at, now):
            return None
        if not _role_allowed(row.role, cfg):
            return None
        return Principal(role=row.role, actor=f"user:{row.role}", via="session", session_id=row.id)


def principal_still_valid(services: Services, principal: Principal | None) -> bool:
    """Kiểm lại principal của kết nối dài (SSE): phiên/token chưa thu hồi/hết hạn, vai trò vẫn được phép."""
    if principal is None:
        return False
    cfg = access_config(services)
    if principal.via == "local":
        return not cfg.local_login_required
    model = {"session": AuthSession, "token": ApiToken}.get(principal.via)
    row_id = principal.session_id if principal.via == "session" else principal.token_id
    if model is None or row_id is None:
        return False
    with services.db.session() as s:
        row = s.get(model, row_id)
        return (
            row is not None
            and row.revoked_at is None
            and not _expired(row.expires_at, utcnow())
            and row.role == principal.role
            and _role_allowed(row.role, cfg)
        )


# --- Đăng nhập ----------------------------------------------------------------------------------------------------


def too_many_attempts(retry_after: int) -> AppError:
    return AppError(
        "too_many_attempts",
        "Nhập sai mật khẩu quá nhiều lần. Vui lòng thử lại sau.",
        hint=f"Thử lại sau khoảng {retry_after} giây.",
        status=429,
        extra={"retry_after": retry_after},
    )


class LoginLimiter:
    """Chống dò mật khẩu, cửa sổ 60 s.

    - **Theo IP** (`security.client_ip`, chỉ tin header `remote.client_ip_header`): cứng 5 lần sai/phút → 429. Lần thử
      được "giữ chỗ" trước khi kiểm mật khẩu (gửi đồng thời cũng không vượt được); đăng nhập đúng thì trả chỗ.
    - **Theo kết nối nguồn (peer)**: sau tunnel mọi client chung peer 127.0.0.1, nên ở mức này **không từ chối** (từ
      chối = kẻ tấn công khoá được chủ ngoài cửa) mà **làm chậm dần**: mỗi lần sai quá `free` lần trong cửa sổ thêm
      `step` giây, tối đa `max_delay`. Mật khẩu đúng vẫn qua. Request cục bộ (`is_local`) không bị tính, không bị chậm.
    """

    def __init__(
        self, per_ip: int = 5, window: float = 60.0, free: int = 5, step: float = 0.1, max_delay: float = 3.0
    ) -> None:
        self.per_ip, self.window = per_ip, window
        self.free, self.step, self.max_delay = free, step, max_delay
        self._lock = threading.Lock()
        self._ip: dict[str, deque[float]] = {}
        self._peer: dict[str, deque[float]] = {}

    def _prune(self, table: dict[str, deque[float]], key: str, now: float) -> deque[float]:
        if len(table) > 10_000:
            for k in [k for k, dq in table.items() if not dq or dq[-1] <= now - self.window]:
                del table[k]
        dq = table.setdefault(key, deque())
        while dq and dq[0] <= now - self.window:
            dq.popleft()
        return dq

    def acquire(self, ip: str) -> tuple[str, float]:
        """Giữ chỗ một lần thử cho `ip`; đã đủ 5 lần sai (kể cả đang thử) trong 60 s → `too_many_attempts`."""
        now = time.monotonic()
        with self._lock:
            dq = self._prune(self._ip, ip, now)
            if len(dq) >= self.per_ip:
                raise too_many_attempts(max(1, int(dq[0] + self.window - now) + 1))
            dq.append(now)
        return ip, now

    def release(self, ticket: tuple[str, float]) -> None:
        ip, stamp = ticket
        with self._lock:
            dq = self._ip.get(ip)
            if dq is not None:
                try:
                    dq.remove(stamp)
                except ValueError:
                    pass

    def delay(self, peer: str) -> float:
        """Số giây phải chờ trước khi kiểm mật khẩu, theo số lần sai gần đây từ `peer`."""
        with self._lock:
            n = len(self._prune(self._peer, peer, time.monotonic()))
        return min(self.max_delay, self.step * max(0, n - self.free))

    def record_peer_failure(self, peer: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(self._peer, peer, now).append(now)


@dataclass(frozen=True)
class LoginResult:
    role: str
    raw_token: str
    expires_at: datetime
    remember: bool


def password_not_set() -> AppError:
    return AppError(
        "password_not_set",
        "Chưa đặt mật khẩu nên chưa thể đăng nhập từ xa.",
        hint="Đặt mật khẩu chủ sở hữu trên máy chạy SagaReel (truy cập http://127.0.0.1:8990).",
        status=403,
    )


def invalid_credentials() -> AppError:
    return AppError("invalid_credentials", "Mật khẩu không đúng.", status=401)


def login(services: Services, password: str, remember: bool, ip: str, user_agent: str) -> LoginResult:
    """Thử mật khẩu chủ rồi (nếu bật vai trò người xem) mật khẩu người xem; đúng → tạo phiên mới."""
    cfg = load_access_config(services)  # không dùng cache: quyết định vai trò theo trạng thái mới nhất
    with services.db.session() as s:
        hashes = {r.role: r.password_hash for r in s.scalars(select(AuthSecret))}
    if "owner" not in hashes:
        raise password_not_set()
    role: str | None = None
    if security.verify_password(password, hashes["owner"]):
        role = "owner"
    elif cfg.allow_viewer and "viewer" in hashes and security.verify_password(password, hashes["viewer"]):
        role = "viewer"
    if role is None:
        raise invalid_credentials()

    raw, digest = security.new_session_token()
    expires_at = utcnow() + (REMEMBER_TTL if remember else SESSION_TTL)
    with services.db.session() as s:
        s.add(AuthSession(
            token_hash=digest, role=role, expires_at=expires_at, remember=remember,
            ip=ip[:64], user_agent=user_agent[:USER_AGENT_MAX],
        ))
    return LoginResult(role=role, raw_token=raw, expires_at=expires_at, remember=remember)


def logout(services: Services, raw: str) -> None:
    with services.db.session() as s:
        s.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == security.sha256_hex(raw), AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )


def password_status(cfg: AccessConfig) -> dict[str, bool]:
    return {"owner": cfg.owner_password_set, "viewer": cfg.viewer_password_set}


# --- Thao tác (gọi từ ops/auth_ops.py) ----------------------------------------------------------------------------


def _no_token(ctx: OpContext, what: str) -> None:
    """Token API không được tự tạo thêm thông tin đăng nhập (chống agent tự gia hạn quyền / khoá chủ ngoài cửa)."""
    if ctx.via == "token":
        raise Forbidden(f"Token API không được phép {what}; hãy làm trong giao diện hoặc trên máy cục bộ.")


def invalid_current_password() -> AppError:
    return Forbidden(
        "Mật khẩu hiện tại của chủ sở hữu không đúng.",
        hint="Nhập mật khẩu chủ sở hữu hiện tại vào `current_password` để đổi mật khẩu.",
        code="invalid_current_password",
    )


def set_password(ctx: OpContext, role: str, password: str, current_password: str | None = None) -> dict:
    """Đặt/đổi mật khẩu một vai trò.

    - Mật khẩu chủ lần đầu chỉ đặt được từ máy cục bộ; mật khẩu người xem cần có mật khẩu chủ trước.
    - Vai trò đã có mật khẩu (đổi mật khẩu) → bắt buộc `current_password` = mật khẩu **chủ** hiện tại; đổi xong thu hồi
      mọi phiên **và** token API của vai trò đó (kể cả phiên đang gọi — đăng nhập lại bằng mật khẩu mới).
    """
    _no_token(ctx, "đặt mật khẩu")
    services = ctx.services
    with services.db.session() as s:
        hashes = {r.role: r.password_hash for r in s.scalars(select(AuthSecret))}
    if "owner" not in hashes:
        if ctx.via != "local":
            raise Forbidden(
                "Mật khẩu lần đầu chỉ đặt được trên máy cục bộ.",
                hint="Mở http://127.0.0.1:8990 ngay trên máy chạy SagaReel để đặt mật khẩu.",
                code="local_only",
            )
        if role != "owner":
            raise Forbidden("Hãy đặt mật khẩu chủ sở hữu trước.", code="owner_password_required")
    changing = role in hashes
    if changing and not (current_password and security.verify_password(current_password, hashes["owner"])):
        raise invalid_current_password()
    other = "viewer" if role == "owner" else "owner"
    if other in hashes and security.verify_password(password, hashes[other]):
        raise ValidationFailed(
            "Mật khẩu chủ sở hữu và mật khẩu người xem phải khác nhau.",
            extra={"errors": [{"loc": ["password"], "msg": "Trùng mật khẩu của vai trò khác.", "type": "duplicate"}]},
        )
    new_hash = security.hash_password(password)  # băm ngoài transaction ghi
    now = utcnow()
    revoked_tokens = 0
    with services.db.session() as s:
        row = s.scalars(select(AuthSecret).where(AuthSecret.role == role)).first()
        if row is None:
            s.add(AuthSecret(role=role, password_hash=new_hash))
        else:
            row.password_hash = new_hash
        revoked = s.execute(
            update(AuthSession)
            .where(AuthSession.role == role, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        ).rowcount
        if changing:
            revoked_tokens = s.execute(
                update(ApiToken).where(ApiToken.role == role, ApiToken.revoked_at.is_(None)).values(revoked_at=now)
            ).rowcount
    invalidate(services)
    return {"role": role, "revoked_sessions": revoked or 0, "revoked_tokens": revoked_tokens or 0}


def _token_dict(t: ApiToken, now: datetime) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "role": t.role,
        "created_at": t.created_at,
        "expires_at": t.expires_at,
        "last_used_at": t.last_used_at,
        "revoked_at": t.revoked_at,
        "active": t.revoked_at is None and not _expired(t.expires_at, now),
    }


def create_token(ctx: OpContext, name: str, role: str, expires_days: int | None) -> dict:
    """Tạo token API; token thô chỉ có trong kết quả này (DB chỉ giữ sha256)."""
    _no_token(ctx, "tạo token API")
    if role == "viewer" and not load_access_config(ctx.services).viewer_enabled:
        raise ValidationFailed(
            "Vai trò người xem chưa được bật.",
            hint="Bật `remote.allow_viewer` và đặt mật khẩu người xem trước.",
            extra={"errors": [{"loc": ["role"], "msg": "Vai trò người xem chưa được bật.", "type": "viewer_disabled"}]},
        )
    raw, digest = security.new_secret_token()
    now = utcnow()
    expires_at = now + timedelta(days=expires_days) if expires_days else None
    with ctx.services.db.session() as s:
        row = ApiToken(name=name, token_hash=digest, role=role, expires_at=expires_at)
        s.add(row)
        s.flush()
        out = _token_dict(row, now)
    out["token"] = raw
    return out


def list_tokens(ctx: OpContext) -> list[dict]:
    now = utcnow()
    with ctx.services.db.session() as s:
        return [_token_dict(t, now) for t in s.scalars(select(ApiToken).order_by(ApiToken.id))]


def revoke_token(ctx: OpContext, token_id: int) -> dict:
    with ctx.services.db.session() as s:
        row = s.get(ApiToken, token_id)
        if row is None:
            raise NotFound(f"Không có token API #{token_id}.")
        if row.revoked_at is None:
            row.revoked_at = utcnow()
        return _token_dict(row, utcnow())


def _session_dict(r: AuthSession, now: datetime) -> dict:
    return {
        "id": r.id,
        "role": r.role,
        "created_at": r.created_at,
        "expires_at": r.expires_at,
        "remember": r.remember,
        "ip": r.ip,
        "user_agent": r.user_agent,
        "revoked_at": r.revoked_at,
        "active": r.revoked_at is None and not _expired(r.expires_at, now),
    }


def list_sessions(ctx: OpContext) -> list[dict]:
    now = utcnow()
    with ctx.services.db.session() as s:
        return [_session_dict(r, now) for r in s.scalars(select(AuthSession).order_by(AuthSession.id.desc()))]


def revoke_session(ctx: OpContext, session_id: int) -> dict:
    with ctx.services.db.session() as s:
        row = s.get(AuthSession, session_id)
        if row is None:
            raise NotFound(f"Không có phiên đăng nhập #{session_id}.")
        if row.revoked_at is None:
            row.revoked_at = utcnow()
        return _session_dict(row, utcnow())
