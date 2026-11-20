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

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from datetime import datetime, timedelta

import anyio
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sagareel import local_agent
from sagareel.container import Services
from sagareel.core import security
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Forbidden, NotFound, ValidationFailed
from sagareel.core.security import LoginLimiter, Principal, RateKey, too_many_attempts  # noqa: F401 (tái xuất)
from sagareel.db.base import utcnow
from sagareel.db.models import ApiToken, AuthSecret, AuthSession
from sagareel.services.settings import global_layer

logger = logging.getLogger("sagareel.auth")

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


def token_suspended(name: str) -> AppError:
    return AppError(
        "token_suspended",
        f"Agent '{name}' đang bị tạm dừng: token API này tạm thời không được dùng.",
        hint="Chủ sở hữu bật lại trong giao diện (Cài đặt → Agent) hoặc gọi token.resume / agents.resume_all từ máy "
             "cục bộ. Agent hãy dừng việc và chờ, đừng thử lại liên tục.",
        status=403,
    )


def authenticate_token(services: Services, raw: str, cfg: AccessConfig, local: bool = False) -> Principal | None:
    """Token API (`Authorization: Bearer`) hợp lệ → Principal `agent:<tên>`; sai/hết hạn/thu hồi → None.

    `local` = request cục bộ thật (`security.is_local`, không phụ thuộc `remote.require_login_local`). Token
    `local_only` (agent cục bộ) qua tunnel/proxy/Host công khai/IP khác → None (401), kể cả khi còn hiệu lực.
    """
    now = utcnow()
    with services.db.session() as s:
        row = s.scalars(select(ApiToken).where(ApiToken.token_hash == security.sha256_hex(raw))).first()
        if row is None or row.revoked_at is not None or _expired(row.expires_at, now):
            return None
        if row.local_only and not local:
            return None
        if not _role_allowed(row.role, cfg):
            return None
        if row.suspended:
            raise token_suspended(row.name)
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
            and not getattr(row, "suspended", False)
            and not _expired(row.expires_at, utcnow())
            and row.role == principal.role
            and _role_allowed(row.role, cfg)
        )


# --- Đăng nhập ----------------------------------------------------------------------------------------------------


# Thay được trong test (ghi lại độ trễ thay vì ngủ thật).
_sleep = anyio.sleep

T = TypeVar("T")


async def guarded_verify(services: Services, rate: RateKey | None, check: Callable[[], T | None]) -> T | None:
    """Chạy `check` (kiểm mật khẩu, scrypt) qua bộ chống dò: hạn mức theo khoá (429), làm chậm theo peer, tối đa 3
    lần kiểm đồng thời. `check` trả None/False = sai (tính một lần sai); giá trị khác = đúng (trả chỗ)."""
    limiter = services.login_limiter
    if rate is None:  # không qua HTTP (CLI trong tiến trình): coi như cục bộ
        rate = RateKey("local:-", "", True)
    tickets = []
    if rate.key:
        tickets.append(limiter.acquire(rate.key))
    if not rate.local:
        ticket, delay = limiter.reserve_peer(rate.peer)
        tickets.append(ticket)
        if delay > 0:
            await _sleep(delay)
    result = await anyio.to_thread.run_sync(check, limiter=limiter.verify_limiter)
    if result is not None and result is not False:
        for t in tickets:
            limiter.release(t)
    return result


def _read_hashes(services: Services) -> dict[str, str]:
    with services.db.session() as s:
        return {r.role: r.password_hash for r in s.scalars(select(AuthSecret))}


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


def _create_session(services: Services, role: str, remember: bool, ip: str, user_agent: str) -> LoginResult:
    raw, digest = security.new_session_token()
    expires_at = utcnow() + (REMEMBER_TTL if remember else SESSION_TTL)
    with services.db.session() as s:
        s.add(AuthSession(
            token_hash=digest, role=role, expires_at=expires_at, remember=remember,
            ip=ip[:64], user_agent=user_agent[:USER_AGENT_MAX],
        ))
    return LoginResult(role=role, raw_token=raw, expires_at=expires_at, remember=remember)


async def login(
    services: Services, password: str, remember: bool, ip: str, user_agent: str, rate: RateKey | None
) -> LoginResult:
    """Thử mật khẩu chủ rồi (nếu bật vai trò người xem) mật khẩu người xem; đúng → tạo phiên mới."""
    # Không dùng cache: quyết định vai trò theo trạng thái mới nhất.
    cfg = await anyio.to_thread.run_sync(load_access_config, services)
    hashes = await anyio.to_thread.run_sync(_read_hashes, services)
    if "owner" not in hashes:
        raise password_not_set()

    def check() -> str | None:
        if security.verify_password(password, hashes["owner"]):
            return "owner"
        if cfg.allow_viewer and "viewer" in hashes and security.verify_password(password, hashes["viewer"]):
            return "viewer"
        return None

    role = await guarded_verify(services, rate, check)
    if role is None:
        raise invalid_credentials()
    return await anyio.to_thread.run_sync(_create_session, services, role, remember, ip, user_agent)


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


async def set_password(ctx: OpContext, role: str, password: str, current_password: str | None = None) -> dict:
    """Đặt/đổi mật khẩu một vai trò.

    - Mật khẩu chủ lần đầu chỉ đặt được từ máy cục bộ; mật khẩu người xem cần có mật khẩu chủ trước.
    - Vai trò đã có mật khẩu (đổi mật khẩu) → bắt buộc `current_password` = mật khẩu **chủ** hiện tại (kiểm qua cùng
      bộ chống dò với đăng nhập); đổi xong thu hồi mọi phiên **và** token API của vai trò đó (kể cả phiên đang gọi).
    - Mọi lần chạy scrypt đi qua `login_limiter.verify_limiter`.
    """
    _no_token(ctx, "đặt mật khẩu")
    services = ctx.services
    verify_limiter = services.login_limiter.verify_limiter
    hashes = await anyio.to_thread.run_sync(_read_hashes, services)
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
    if changing:
        owner_hash = hashes["owner"]
        ok = await guarded_verify(
            services,
            ctx.rate_key,
            lambda: bool(current_password) and security.verify_password(current_password, owner_hash),
        )
        if not ok:
            raise invalid_current_password()
    other = "viewer" if role == "owner" else "owner"
    if other in hashes:
        same = await anyio.to_thread.run_sync(
            security.verify_password, password, hashes[other], limiter=verify_limiter
        )
        if same:
            raise ValidationFailed(
                "Mật khẩu chủ sở hữu và mật khẩu người xem phải khác nhau.",
                extra={"errors": [{"loc": ["password"], "msg": "Trùng mật khẩu của vai trò khác.", "type": "duplicate"}]},
            )
    new_hash = await anyio.to_thread.run_sync(security.hash_password, password, limiter=verify_limiter)
    result = await anyio.to_thread.run_sync(_store_password, services, role, new_hash, changing)
    invalidate(services)
    if role == "owner" and changing:
        # Đổi mật khẩu chủ đã thu hồi mọi token owner, kể cả token agent cục bộ → cấp token mới vào file (chỉ ai
        # đọc được thư mục data mới lấy được), CLI cục bộ không phải chờ khởi động lại app.
        await anyio.to_thread.run_sync(ensure_local_agent_token_safe, services)
    return result


def _store_password(services: Services, role: str, new_hash: str, changing: bool) -> dict:
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
        "suspended": bool(t.suspended),
        "local_only": bool(t.local_only),
    }


def _live_tokens(s: Session, name: str | None = None) -> list[ApiToken]:
    q = select(ApiToken).where(ApiToken.revoked_at.is_(None))
    if name is not None:
        q = q.where(ApiToken.name == name)
    return list(s.scalars(q.order_by(ApiToken.id)))


def set_suspended(ctx: OpContext, name: str, suspended: bool) -> list[dict]:
    """Tạm dừng / bật lại mọi token chưa thu hồi mang tên `name` (agent `agent:<name>`). Token API không được tự bật
    lại (một agent không gỡ được lệnh dừng của chủ)."""
    if not suspended:
        _no_token(ctx, "bật lại agent")
    now = utcnow()
    with ctx.services.db.session(immediate=True) as s:
        rows = _live_tokens(s, name)
        if not rows:
            raise NotFound(f"Không có token API nào tên '{name}' còn hiệu lực.", code="token_not_found",
                           hint="Xem token.list.")
        for r in rows:
            r.suspended = suspended
        s.flush()
        return [_token_dict(r, now) for r in rows]


def set_all_suspended(ctx: OpContext, suspended: bool) -> dict:
    """Tạm dừng / bật lại MỌI agent: mọi token API chưa thu hồi (kể cả token agent cục bộ `local-agent`)."""
    if not suspended:
        _no_token(ctx, "bật lại agent")
    now = utcnow()
    with ctx.services.db.session(immediate=True) as s:
        rows = _live_tokens(s)
        changed = [r for r in rows if bool(r.suspended) != suspended]
        for r in changed:
            r.suspended = suspended
        s.flush()
        return {"suspended": suspended, "changed": len(changed),
                "tokens": [_token_dict(r, now) for r in rows]}


def create_token(ctx: OpContext, name: str, role: str, expires_days: int | None) -> dict:
    """Tạo token API; token thô chỉ có trong kết quả này (DB chỉ giữ sha256)."""
    _no_token(ctx, "tạo token API")
    if name.strip().lower() == local_agent.TOKEN_NAME:
        raise ValidationFailed(
            f"Tên `{local_agent.TOKEN_NAME}` dành riêng cho token agent cục bộ của app.",
            hint="Chọn tên khác cho token.",
            extra={"errors": [{"loc": ["name"], "msg": "Tên dành riêng.", "type": "reserved_name"}]},
        )
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


# --- Token agent cục bộ (`data/local-agent.token`) ----------------------------------------------------------------


def _local_agent_row_valid(row: ApiToken | None) -> bool:
    return (
        row is not None
        and row.name == local_agent.TOKEN_NAME
        and row.role == "owner"
        and row.revoked_at is None
        and row.expires_at is None
        and row.local_only
    )


def _revoke_local_agent_rows(s: Session, now: datetime) -> int:
    return s.execute(
        update(ApiToken)
        .where(ApiToken.name == local_agent.TOKEN_NAME, ApiToken.revoked_at.is_(None))
        .values(revoked_at=now)
    ).rowcount or 0


_LOCAL_AGENT_LOCK = threading.Lock()


def ensure_local_agent_token(services: Services) -> str:
    """Bảo đảm `<data>/local-agent.token` chứa token API owner còn hiệu lực (tên `local-agent`, không hạn).

    File thiếu, nội dung lạ, hoặc token đã thu hồi/không có trong DB → tạo token mới: ghi file trước (ACL riêng người
    dùng), rồi trong một phiên ngắn thu hồi mọi dòng `local-agent` cũ và thêm dòng mới (DB chỉ giữ hash). Không ghi
    được file → thu hồi dòng cũ, không tạo dòng mới (không để token sống mà không ai giữ). Trả `kept|created|failed`;
    **không bao giờ** trả, log hay in token thô.
    """
    with _LOCAL_AGENT_LOCK:  # khởi động và đổi mật khẩu chủ có thể chạy song song: file và DB phải khớp nhau
        return _ensure_local_agent_token(services)


def _ensure_local_agent_token(services: Services) -> str:
    path = local_agent.token_path(services.settings.data_dir)
    raw = local_agent.read_token(path)
    if raw is not None:
        with services.db.session() as s:
            row = s.scalars(select(ApiToken).where(ApiToken.token_hash == security.sha256_hex(raw))).first()
            valid = _local_agent_row_valid(row)
        if valid:
            local_agent.restrict_existing(path)  # file giữ nguyên: vẫn thu quyền lại (lỗi chỉ cảnh báo)
            return "kept"
    raw, digest = security.new_secret_token()
    try:
        local_agent.write_token_file(path, raw)
    except OSError as e:
        logger.warning(
            "Không ghi được file token agent cục bộ %s (%s); CLI/MCP trên máy này cần đặt SAGAREEL_TOKEN.",
            path,
            e.strerror or type(e).__name__,
        )
        with services.db.session() as s:
            _revoke_local_agent_rows(s, utcnow())
        return "failed"
    with services.db.session(immediate=True) as s:
        # Agent cục bộ đang tạm dừng (agents.pause_all) → token mới vẫn tạm dừng (khởi động lại không gỡ lệnh dừng).
        # Xét dòng local-agent mới nhất (kể cả vừa bị thu hồi do đổi mật khẩu chủ).
        paused = bool(s.scalar(select(ApiToken.suspended).where(ApiToken.name == local_agent.TOKEN_NAME)
                               .order_by(ApiToken.id.desc()).limit(1)))
        _revoke_local_agent_rows(s, utcnow())
        s.add(ApiToken(name=local_agent.TOKEN_NAME, token_hash=digest, role="owner", expires_at=None,
                       local_only=True, suspended=paused))
    logger.info("Đã tạo token agent cục bộ mới (file %s).", path)
    return "created"


def ensure_local_agent_token_safe(services: Services) -> str:
    """Như `ensure_local_agent_token` nhưng lỗi bất ngờ chỉ cảnh báo (không chặn khởi động / đổi mật khẩu).

    Chỉ log tên kiểu lỗi: thông điệp lỗi SQL có thể kèm tham số (hash token).
    """
    try:
        return ensure_local_agent_token(services)
    except Exception as e:  # noqa: BLE001
        logger.warning("Không chuẩn bị được token agent cục bộ (%s).", type(e).__name__)
        return "failed"
