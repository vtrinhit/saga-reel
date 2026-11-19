"""Task 8: host tin cậy, phân biệt cục bộ/từ xa, đăng nhập, token API, CSRF."""

from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from sagareel.app import create_app
from sagareel.core import security
from sagareel.core.errors import AppError
from sagareel.db.base import utcnow
from sagareel.db.models import ApiToken, AuthSession

BASE = "http://127.0.0.1:8990"
PUBLIC = "studio.example.com"
REMOTE_H = {"Host": PUBLIC}


# --- Fixtures ---------------------------------------------------------------------------------------------------


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def local_client(app):
    with TestClient(app, base_url=BASE, client=("127.0.0.1", 50000)) as c:
        r = c.post("/api/ops/settings.set_global", json={"values": {"remote.public_hosts": [PUBLIC]}})
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture
def remote_client(app, local_client):
    # Tunnel (cloudflared) nối từ 127.0.0.1 nhưng luôn gắn CF-Connecting-IP. Dùng chung app (lifespan do local_client).
    c = TestClient(app, base_url=BASE, client=("127.0.0.1", 50001), headers={"CF-Connecting-IP": "1.2.3.4"})
    try:
        yield c
    finally:
        c.close()


def _op(client, name, body=None, headers=None):
    return client.post(f"/api/ops/{name}", json=body if body is not None else {}, headers=headers)


@pytest.fixture
def owner_password(local_client) -> str:
    pw = "mat-khau-chu-123"
    r = _op(local_client, "auth.set_password", {"role": "owner", "password": pw})
    assert r.status_code == 200, r.text
    return pw


@pytest.fixture
def viewer_password(local_client, owner_password) -> str:
    pw = "mat-khau-xem-456"
    assert _op(local_client, "settings.set_global", {"values": {"remote.allow_viewer": True}}).status_code == 200
    r = _op(local_client, "auth.set_password", {"role": "viewer", "password": pw})
    assert r.status_code == 200, r.text
    return pw


@pytest.fixture
def viewer_token(local_client, viewer_password) -> str:
    r = _op(local_client, "token.create", {"name": "bot-xem", "role": "viewer"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def owner_token(local_client) -> str:
    r = _op(local_client, "token.create", {"name": "agent1", "role": "owner"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _login(client, password, remember=True, headers=None):
    return client.post(
        "/api/auth/login", json={"password": password, "remember": remember}, headers={**REMOTE_H, **(headers or {})}
    )


def _csrf_headers(client) -> dict:
    return {**REMOTE_H, "Origin": f"https://{PUBLIC}", "X-SR-CSRF": client.cookies["sr_csrf"]}


# --- Test trong brief -------------------------------------------------------------------------------------------


def test_local_is_owner_without_login(local_client):
    assert local_client.get("/api/auth/status").json()["via"] == "local"


def test_tunnel_on_loopback_is_remote_and_needs_login(remote_client):
    r = remote_client.post("/api/ops/system.info", json={})
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required"


def test_untrusted_host_rejected(local_client):
    r = local_client.get("/api/health", headers={"Host": "evil.com"})
    assert r.status_code == 400 and r.json()["error_code"] == "untrusted_host"


def test_login_then_write_needs_csrf(remote_client, owner_password):
    remote_client.post(
        "/api/auth/login", json={"password": owner_password, "remember": True}, headers={"Host": "studio.example.com"}
    )
    h = {"Host": "studio.example.com", "Origin": "https://studio.example.com"}
    assert remote_client.post("/api/ops/settings.set_global", json={"values": {}}, headers=h).json()[
        "error_code"
    ] == "csrf_failed"
    h["X-SR-CSRF"] = remote_client.cookies["sr_csrf"]
    assert remote_client.post("/api/ops/settings.set_global", json={"values": {}}, headers=h).status_code == 200


def test_bearer_token_role_viewer_cannot_write(remote_client, viewer_token):
    h = {**REMOTE_H, "Authorization": f"Bearer {viewer_token}"}
    r = _op(remote_client, "settings.set_global", {"values": {}}, headers=h)
    assert r.status_code == 403 and r.json()["error_code"] == "forbidden"
    # Đọc thì được, không cần CSRF (Bearer).
    assert _op(remote_client, "system.info", headers=h).status_code == 200


def test_login_rate_limited(remote_client, owner_password):
    for _ in range(5):
        r = _login(remote_client, "sai-mat-khau")
        assert r.status_code == 401 and r.json()["error_code"] == "invalid_credentials"
    r = _login(remote_client, owner_password)  # lần thứ 6, kể cả đúng mật khẩu
    assert r.status_code == 429 and r.json()["error_code"] == "too_many_attempts"
    assert "sr_session" not in remote_client.cookies


def test_token_raw_shown_once(local_client):
    r = _op(local_client, "token.create", {"name": "agent1", "role": "owner", "expires_days": 30})
    assert r.status_code == 200
    raw = r.json()["token"]
    assert raw.startswith("sr_")
    listed = _op(local_client, "token.list").json()
    assert len(listed) == 1 and listed[0]["name"] == "agent1" and listed[0]["active"] is True
    text = str(listed)
    assert raw not in text and hashlib.sha256(raw.encode()).hexdigest() not in text
    assert "token" not in listed[0] and "token_hash" not in listed[0]


def test_first_password_only_from_local(remote_client, owner_token):
    # Không đăng nhập được → 401.
    r = _op(remote_client, "auth.set_password", {"role": "owner", "password": "abcdefgh1"}, headers=REMOTE_H)
    assert r.status_code in (401, 403)
    # Kể cả token owner hợp lệ từ xa cũng không được đặt mật khẩu lần đầu.
    h = {**REMOTE_H, "Authorization": f"Bearer {owner_token}"}
    r = _op(remote_client, "auth.set_password", {"role": "owner", "password": "abcdefgh1"}, headers=h)
    assert r.status_code == 403
    assert remote_client.get("/api/auth/status", headers=REMOTE_H).json()["password_set"]["owner"] is False


# --- Hàm thuần của core.security --------------------------------------------------------------------------------


def test_hash_password_format_and_verify():
    stored = security.hash_password("mật khẩu ✓")
    algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
    assert (algo, n, r, p) == ("scrypt", "16384", "8", "1")
    assert len(base64.b64decode(salt_b64)) == 16
    assert security.verify_password("mật khẩu ✓", stored)
    assert not security.verify_password("mật khẩu", stored)
    assert security.hash_password("x") != security.hash_password("x")  # salt ngẫu nhiên


@pytest.mark.parametrize(
    "stored",
    ["", "plain", "scrypt$16384$8$1$!!$!!", "bcrypt$16384$8$1$AAAA$AAAA", "scrypt$abc$8$1$AAAA$AAAA",
     "scrypt$1073741824$8$1$AAAA$AAAA", "scrypt$16384$8$1$AAAA"],
)
def test_verify_password_malformed_is_false(stored):
    assert security.verify_password("x", stored) is False


def test_new_secret_token():
    raw, digest = security.new_secret_token()
    assert raw.startswith("sr_") and len(raw) >= 40
    assert digest == hashlib.sha256(raw.encode()).hexdigest()
    assert security.new_secret_token()[0] != raw


def _req(client=("127.0.0.1", 1234), host="127.0.0.1:8990", headers=None, port=8990) -> Request:
    raw = [(b"host", host.encode())] if host is not None else []
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    app = SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace(port=port))))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": client, "app": app})


@pytest.mark.parametrize("client, host", [
    (("127.0.0.1", 1), "127.0.0.1:8990"),
    (("127.0.0.1", 1), "LOCALHOST:8990"),
    (("::1", 1), "[::1]:8990"),
    (("127.5.6.7", 1), "localhost:8990"),
])
def test_is_local_true(client, host):
    assert security.is_local(_req(client=client, host=host))


@pytest.mark.parametrize("kwargs", [
    {"client": ("testclient", 50000)},
    {"client": None},
    {"client": ("192.168.1.5", 1)},
    {"client": ("::ffff:8.8.8.8", 1)},
    {"host": "studio.example.com"},
    {"host": "127.0.0.1"},           # thiếu cổng
    {"host": "127.0.0.1:8991"},      # sai cổng
    {"host": None},
    {"headers": {"X-Forwarded-For": "1.2.3.4"}},
    {"headers": {"X-Forwarded-Host": "a.com"}},
    {"headers": {"Forwarded": "for=1.2.3.4"}},
    {"headers": {"CF-Connecting-IP": "1.2.3.4"}},
    {"headers": {"X-Real-IP": "1.2.3.4"}},
    {"headers": {"X-Forwarded-For": ""}},  # có header là đủ, kể cả rỗng
])
def test_is_local_false(kwargs):
    assert not security.is_local(_req(**kwargs))


def test_is_local_uses_port_from_settings():
    assert security.is_local(_req(host="localhost:9000", port=9000))
    assert not security.is_local(_req(host="localhost:8990", port=9000))


@pytest.mark.parametrize("value, expected", [
    ("Studio.Example.com:443", "studio.example.com"),
    ("[::1]:8990", "::1"),
    ("localhost", "localhost"),
    ("", None),
    ("a:b", None),
    ("[::1", None),
    ("::1", None),
    ("a b.com", None),
])
def test_host_name(value, expected):
    assert security.host_name(value) == expected


def test_normalize_public_hosts_ignores_garbage():
    assert security.normalize_public_hosts("studio.example.com") == frozenset()
    assert security.normalize_public_hosts(
        ["Studio.Example.com", "https://b.example.com/x", 5, None, "c.example.com:8443", ""]
    ) == {"studio.example.com", "b.example.com", "c.example.com"}


# --- Host tin cậy / đường dẫn được bảo vệ ------------------------------------------------------------------------


def test_public_host_is_trusted_and_health_is_open(remote_client):
    r = remote_client.get("/api/health", headers=REMOTE_H)
    assert r.status_code == 200
    assert remote_client.get("/api/health", headers={"Host": "other.example.com"}).status_code == 400


def test_missing_host_rejected(local_client):
    r = local_client.get("/api/health", headers={"Host": ""})
    assert r.status_code == 400 and r.json()["error_code"] == "untrusted_host"
    assert set(r.json()) == {"ok", "error_code", "detail", "hint", "status", "extra"}


@pytest.mark.parametrize("path", [
    "/api/ops", "/api", "/api/events", "/media/a.png", "/media/thumbs/x.jpg", "/openapi.json", "/docs", "/redoc",
    "//api/ops", "/API/ops", "/api/health/", "/api/auth/other",
])
def test_remote_protected_paths(remote_client, path):
    r = remote_client.get(BASE + path, headers=REMOTE_H)  # URL đầy đủ: "//api" không bị hiểu là URL không scheme
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required"


def test_spa_paths_not_blocked(remote_client):
    # Chưa có frontend build → 404, nhưng không bị chặn 401 (SPA tự hiện màn đăng nhập).
    assert remote_client.get("/", headers=REMOTE_H).status_code != 401
    assert remote_client.get("/assets/app.js", headers=REMOTE_H).status_code != 401


def test_remote_status_unauthenticated(remote_client):
    s = remote_client.get("/api/auth/status", headers=REMOTE_H).json()
    assert s == {"authenticated": False, "role": None, "via": None, "local": False,
                 "password_set": {"owner": False, "viewer": False}}


def test_local_status(local_client, owner_password):
    s = local_client.get("/api/auth/status").json()
    assert s == {"authenticated": True, "role": "owner", "via": "local", "local": True,
                 "password_set": {"owner": True, "viewer": False}}


def test_public_host_change_takes_effect_immediately(local_client, remote_client):
    assert remote_client.get("/api/health", headers=REMOTE_H).status_code == 200
    assert _op(local_client, "settings.set_global", {"values": {"remote.public_hosts": []}}).status_code == 200
    assert remote_client.get("/api/health", headers=REMOTE_H).status_code == 400


# --- CSRF cho chủ cục bộ (không cần đăng nhập) ------------------------------------------------------------------


def test_local_cross_site_write_blocked(local_client):
    r = _op(local_client, "settings.set_global", {"values": {}}, headers={"Origin": "https://evil.com"})
    assert r.status_code == 403 and r.json()["error_code"] == "csrf_failed"
    r = _op(local_client, "settings.set_global", {"values": {}}, headers={"Origin": "null"})
    assert r.status_code == 403
    # Origin cùng host loopback nhưng khác cổng (ứng dụng cục bộ khác) cũng bị chặn.
    r = _op(local_client, "settings.set_global", {"values": {}}, headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 403


def test_local_same_origin_or_no_origin_write_ok(local_client):
    assert _op(local_client, "settings.set_global", {"values": {}}).status_code == 200
    h = {"Origin": "http://127.0.0.1:8990"}
    assert _op(local_client, "settings.set_global", {"values": {}}, headers=h).status_code == 200


def test_local_cross_site_read_allowed(local_client):
    # GET an toàn; CORS của trình duyệt ngăn trang lạ đọc kết quả.
    assert local_client.get("/api/ops", headers={"Origin": "https://evil.com"}).status_code == 200


# --- Đăng nhập, phiên, CSRF ---------------------------------------------------------------------------------------


def test_login_cookies_attributes(remote_client, owner_password, app):
    r = _login(remote_client, owner_password, remember=True, headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200 and r.json()["role"] == "owner"
    cookies = {c.split("=", 1)[0]: c for c in r.headers.get_list("set-cookie")}
    sess, csrf = cookies["sr_session"].lower(), cookies["sr_csrf"].lower()
    assert "httponly" in sess and "samesite=lax" in sess and "secure" in sess and "max-age=2592000" in sess
    assert "httponly" not in csrf and "samesite=lax" in csrf and "secure" in csrf
    assert "domain=" not in sess and "path=/" in sess


def test_login_not_remember_is_browser_session_with_12h_db_expiry(remote_client, owner_password, app):
    r = _login(remote_client, owner_password, remember=False)
    assert r.status_code == 200
    sess = next(c for c in r.headers.get_list("set-cookie") if c.startswith("sr_session="))
    assert "max-age" not in sess.lower() and "secure" not in sess.lower()
    with app.state.services.db.session() as s:
        row = s.scalars(select(AuthSession)).one()
        raw = sess.split(";", 1)[0].split("=", 1)[1]
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()  # chỉ lưu hash
        left = row.expires_at - utcnow()
        assert timedelta(hours=11, minutes=59) < left <= timedelta(hours=12)
        assert row.ip == "1.2.3.4" and row.remember is False


def test_login_wrong_password(remote_client, owner_password):
    r = _login(remote_client, "khong-dung")
    assert r.status_code == 401 and r.json()["error_code"] == "invalid_credentials"
    assert "sr_session" not in remote_client.cookies


def test_login_without_any_password(remote_client):
    r = _login(remote_client, "gi-cung-duoc")
    assert r.status_code in (401, 403)
    assert "sr_session" not in remote_client.cookies


def test_session_read_and_status(remote_client, owner_password):
    _login(remote_client, owner_password)
    assert _op(remote_client, "system.info", headers=_csrf_headers(remote_client)).status_code == 200
    s = remote_client.get("/api/auth/status", headers=REMOTE_H).json()
    assert s["authenticated"] and s["role"] == "owner" and s["via"] == "session" and s["local"] is False
    assert remote_client.get("/api/ops", headers=REMOTE_H).status_code == 200  # GET không cần CSRF


def test_csrf_wrong_token_or_origin(remote_client, owner_password):
    _login(remote_client, owner_password)
    h = _csrf_headers(remote_client)
    assert _op(remote_client, "settings.set_global", {"values": {}}, headers={**h, "X-SR-CSRF": "x"}).status_code == 403
    r = _op(remote_client, "settings.set_global", {"values": {}}, headers={**h, "Origin": "https://evil.com"})
    assert r.status_code == 403 and r.json()["error_code"] == "csrf_failed"
    # Cookie sr_csrf bị ghi đè (vd. từ subdomain) + header khớp cookie đó vẫn không qua: token gắn với phiên.
    remote_client.cookies.set("sr_csrf", "forged")
    r = _op(remote_client, "settings.set_global", {"values": {}}, headers={**h, "X-SR-CSRF": "forged"})
    assert r.status_code == 403


def test_login_from_foreign_origin_rejected(remote_client, owner_password):
    r = _login(remote_client, owner_password, headers={"Origin": "https://evil.com"})
    assert r.status_code == 403 and r.json()["error_code"] == "csrf_failed"


def test_logout_revokes_session(remote_client, owner_password):
    _login(remote_client, owner_password)
    old = remote_client.cookies["sr_session"]
    r = remote_client.post("/api/auth/logout", headers=_csrf_headers(remote_client))
    assert r.status_code == 200
    remote_client.cookies.set("sr_session", old)
    r = _op(remote_client, "system.info", headers=REMOTE_H)
    assert r.status_code == 401


def test_session_list_and_revoke(local_client, remote_client, owner_password):
    _login(remote_client, owner_password)
    sessions = _op(local_client, "session.list").json()
    assert len(sessions) == 1 and sessions[0]["active"] and sessions[0]["role"] == "owner"
    assert "token_hash" not in sessions[0]
    assert _op(local_client, "session.revoke", {"id": sessions[0]["id"]}).status_code == 200
    assert _op(remote_client, "system.info", headers=_csrf_headers(remote_client)).status_code == 401
    assert _op(local_client, "session.list").json()[0]["active"] is False
    assert _op(local_client, "session.revoke", {"id": 999}).status_code == 404


def test_password_change_revokes_sessions_of_role(local_client, remote_client, owner_password):
    _login(remote_client, owner_password)
    assert _op(local_client, "auth.set_password", {"role": "owner", "password": "mat-khau-moi-789"}).status_code == 200
    assert _op(remote_client, "system.info", headers=_csrf_headers(remote_client)).status_code == 401
    assert _login(remote_client, owner_password).status_code == 401
    assert _login(remote_client, "mat-khau-moi-789").status_code == 200


def test_remote_owner_session_can_change_password(remote_client, owner_password):
    _login(remote_client, owner_password)
    r = _op(remote_client, "auth.set_password", {"role": "owner", "password": "doi-tu-xa-000"},
            headers=_csrf_headers(remote_client))
    assert r.status_code == 200


def test_password_validation(local_client, owner_password):
    assert _op(local_client, "auth.set_password", {"role": "owner", "password": "short"}).status_code == 422
    assert _op(local_client, "auth.set_password", {"role": "admin", "password": "abcdefgh1"}).status_code == 422
    # Mật khẩu người xem trùng mật khẩu chủ → không bao giờ đăng nhập được vai trò viewer.
    r = _op(local_client, "auth.set_password", {"role": "viewer", "password": owner_password})
    assert r.status_code == 422


# --- Người xem ----------------------------------------------------------------------------------------------------


def test_viewer_login(remote_client, viewer_password):
    r = _login(remote_client, viewer_password)
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    h = _csrf_headers(remote_client)
    assert _op(remote_client, "system.info", headers=h).status_code == 200
    assert _op(remote_client, "settings.set_global", {"values": {}}, headers=h).status_code == 403
    for name in ("token.list", "session.list", "token.create", "auth.set_password"):
        body = {"name": "x", "role": "owner"} if name == "token.create" else {}
        if name == "auth.set_password":
            body = {"role": "viewer", "password": "abcdefgh12"}
        assert _op(remote_client, name, body, headers=h).status_code == 403, name


def test_viewer_disabled_blocks_login_and_existing_access(local_client, remote_client, viewer_password, viewer_token):
    _login(remote_client, viewer_password)
    h = _csrf_headers(remote_client)
    assert _op(remote_client, "system.info", headers=h).status_code == 200
    assert _op(local_client, "settings.set_global", {"values": {"remote.allow_viewer": False}}).status_code == 200
    assert _op(remote_client, "system.info", headers=h).status_code == 401
    bh = {**REMOTE_H, "Authorization": f"Bearer {viewer_token}"}
    assert _op(remote_client, "system.info", headers=bh).status_code == 401
    remote_client.cookies.clear()
    assert _login(remote_client, viewer_password).status_code == 401


def test_viewer_token_requires_viewer_enabled(local_client, owner_password):
    r = _op(local_client, "token.create", {"name": "bot", "role": "viewer"})
    assert r.status_code == 422


# --- Token API ----------------------------------------------------------------------------------------------------


def test_bearer_owner_token_actor_and_no_csrf(remote_client, owner_token, app):
    h = {**REMOTE_H, "Authorization": f"Bearer {owner_token}", "Origin": "https://some-agent-ui.example"}
    r = _op(remote_client, "settings.set_global", {"values": {"jobs.ffmpeg_concurrency": 3}}, headers=h)
    assert r.status_code == 200
    from sagareel.db.models import ChangeSet

    with app.state.services.db.session() as s:
        assert s.scalars(select(ChangeSet.actor).order_by(ChangeSet.id.desc())).first() == "agent:agent1"
        assert s.scalars(select(ApiToken)).one().last_used_at is not None


def test_invalid_bearer_rejected(remote_client, local_client):
    h = {**REMOTE_H, "Authorization": "Bearer sr_khong_ton_tai"}
    r = _op(remote_client, "system.info", headers=h)
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required"
    # Token sai cũng không được "nâng" thành chủ cục bộ.
    assert _op(local_client, "system.info", headers={"Authorization": "Bearer sai"}).status_code == 401


def test_token_revoke_and_expiry(local_client, remote_client, owner_token, app):
    h = {**REMOTE_H, "Authorization": f"Bearer {owner_token}"}
    assert _op(remote_client, "system.info", headers=h).status_code == 200
    tid = _op(local_client, "token.list").json()[0]["id"]
    assert _op(local_client, "token.revoke", {"id": tid}).status_code == 200
    assert _op(remote_client, "system.info", headers=h).status_code == 401
    assert _op(local_client, "token.revoke", {"id": 12345}).status_code == 404

    raw = _op(local_client, "token.create", {"name": "het-han", "role": "owner", "expires_days": 1}).json()["token"]
    with app.state.services.db.session() as s:
        row = s.scalars(select(ApiToken).where(ApiToken.name == "het-han")).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
    assert _op(remote_client, "system.info", headers={**REMOTE_H, "Authorization": f"Bearer {raw}"}).status_code == 401


def test_token_cannot_mint_credentials(remote_client, owner_token, owner_password):
    h = {**REMOTE_H, "Authorization": f"Bearer {owner_token}"}
    assert _op(remote_client, "token.create", {"name": "x", "role": "owner"}, headers=h).status_code == 403
    r = _op(remote_client, "auth.set_password", {"role": "owner", "password": "abcdefgh12"}, headers=h)
    assert r.status_code == 403


def test_token_name_validated(local_client):
    assert _op(local_client, "token.create", {"name": "", "role": "owner"}).status_code == 422
    assert _op(local_client, "token.create", {"name": "a" * 65, "role": "owner"}).status_code == 422
    assert _op(local_client, "token.create", {"name": "x", "role": "owner", "expires_days": 0}).status_code == 422


def test_bearer_on_media_and_events(remote_client, owner_token):
    h = {**REMOTE_H, "Authorization": f"Bearer {owner_token}"}
    assert remote_client.get("/media/khong-co.png", headers=h).status_code == 404  # qua xác thực, chưa có route


# --- Bắt đăng nhập cả ở máy cục bộ -------------------------------------------------------------------------------


def test_require_login_local(local_client, owner_password):
    assert _op(local_client, "settings.set_global", {"values": {"remote.require_login_local": True}}).status_code == 200
    r = _op(local_client, "system.info")
    assert r.status_code == 401
    assert local_client.get("/api/auth/status").json()["local"] is True
    r = local_client.post("/api/auth/login", json={"password": owner_password, "remember": False})
    assert r.status_code == 200
    h = {"X-SR-CSRF": local_client.cookies["sr_csrf"]}
    assert _op(local_client, "system.info", headers=h).status_code == 200
    r = _op(local_client, "settings.set_global", {"values": {"remote.require_login_local": False}}, headers=h)
    assert r.status_code == 200


def test_require_login_local_ignored_without_owner_password(local_client):
    assert _op(local_client, "settings.set_global", {"values": {"remote.require_login_local": True}}).status_code == 200
    assert _op(local_client, "system.info").status_code == 200  # không tự khoá mình ngoài cửa


# --- OpContext lấy từ principal ------------------------------------------------------------------------------------


def test_context_from_request_fails_closed():
    from sagareel.api.routes_ops import context_from_request

    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "state": {},
                   "app": SimpleNamespace(state=SimpleNamespace(services=None))})
    with pytest.raises(AppError) as ei:
        context_from_request(req)
    assert ei.value.status == 401 and ei.value.code == "auth_required"


def test_context_from_request_uses_principal():
    from sagareel.api.routes_ops import context_from_request

    p = security.Principal(role="viewer", actor="agent:x", via="token")
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "state": {"principal": p},
                   "app": SimpleNamespace(state=SimpleNamespace(services="svc"))})
    ctx = context_from_request(req)
    assert (ctx.actor, ctx.role, ctx.via, ctx.services) == ("agent:x", "viewer", "token", "svc")


def test_serve_disables_uvicorn_proxy_headers(monkeypatch, tmp_path):
    import uvicorn

    from sagareel import __main__ as entry

    monkeypatch.setenv("SAGAREEL_DATA_DIR", str(tmp_path / "data"))
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    assert entry._serve([]) == 0
    assert seen["proxy_headers"] is False and seen["host"] == "127.0.0.1"


def test_security_check_crash_returns_standard_500(local_client, monkeypatch):
    from sagareel.services import auth

    def boom(*a, **k):
        raise RuntimeError("hỏng")

    local_client.app.state.services.auth_cache.invalidate()
    monkeypatch.setattr(auth, "access_config", boom)
    r = local_client.get("/api/health")
    assert r.status_code == 500 and r.json()["error_code"] == "internal_error"
