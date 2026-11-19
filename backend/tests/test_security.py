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
# Trình duyệt từ xa nói https với tunnel → cookie `Secure` phải được gửi lại; client test cũng dùng https.
REMOTE_BASE = "https://127.0.0.1:8990"
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
    c = TestClient(app, base_url=REMOTE_BASE, client=("127.0.0.1", 50001), headers={"CF-Connecting-IP": "1.2.3.4"})
    c.portal = local_client.portal  # cùng event loop với lifespan (EventBus đã bind vào loop đó) — cần cho SSE
    try:
        yield c
    finally:
        c.close()


def _extra_client(app, base_url, port, headers=None) -> TestClient:
    """Client phụ dùng chung app (không chạy lại lifespan — lifespan do `local_client` giữ)."""
    return TestClient(app, base_url=base_url, client=("127.0.0.1", port), headers=headers)


def _op(client, name, body=None, headers=None):
    """POST một op; client đã đăng nhập bằng cookie thì tự gửi X-SR-CSRF (trừ khi test tự đặt header này)."""
    headers = dict(headers or {})
    if "sr_csrf" in client.cookies and not any(k.lower() == "x-sr-csrf" for k in headers):
        headers["X-SR-CSRF"] = client.cookies["sr_csrf"]
    return client.post(f"/api/ops/{name}", json=body if body is not None else {}, headers=headers)


def _local_login(client, password):
    r = client.post("/api/auth/login", json={"password": password, "remember": False})
    assert r.status_code == 200, r.text
    return r


@pytest.fixture
def owner_password(local_client) -> str:
    pw = "mat-khau-chu-123"
    r = _op(local_client, "auth.set_password", {"role": "owner", "password": pw})
    assert r.status_code == 200, r.text
    # `remote.require_login_local` mặc định bật → có mật khẩu chủ rồi thì máy cục bộ cũng phải đăng nhập.
    assert _op(local_client, "system.info").status_code == 401
    _local_login(local_client, pw)
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
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required"
    # Kể cả token owner hợp lệ từ xa cũng không được đặt mật khẩu lần đầu.
    h = {**REMOTE_H, "Authorization": f"Bearer {owner_token}"}
    r = _op(remote_client, "auth.set_password", {"role": "owner", "password": "abcdefgh1"}, headers=h)
    assert r.status_code == 403 and r.json()["error_code"] == "forbidden"  # token không được đặt mật khẩu
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


def test_local_status(local_client):
    s = local_client.get("/api/auth/status").json()
    assert s == {"authenticated": True, "role": "owner", "via": "local", "local": True,
                 "password_set": {"owner": False, "viewer": False}}


def test_local_status_after_password_is_session(local_client, owner_password):
    s = local_client.get("/api/auth/status").json()
    assert s == {"authenticated": True, "role": "owner", "via": "session", "local": True,
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
    assert "max-age" not in sess.lower()
    with app.state.services.db.session() as s:
        row = s.scalars(select(AuthSession).where(AuthSession.ip == "1.2.3.4")).one()
        raw = sess.split(";", 1)[0].split("=", 1)[1]
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()  # chỉ lưu hash
        left = row.expires_at - utcnow()
        assert timedelta(hours=11, minutes=59) < left <= timedelta(hours=12)
        assert row.ip == "1.2.3.4" and row.remember is False


def test_secure_cookie_on_public_host_even_over_http(local_client, owner_password, app):
    # Tunnel nói http với app nhưng trình duyệt dùng https: Host công khai → luôn Secure.
    c = _extra_client(app, BASE, 50002, {"CF-Connecting-IP": "5.6.7.8"})
    r = _login(c, owner_password)
    c.close()
    assert r.status_code == 200
    assert all("secure" in v.lower() for v in r.headers.get_list("set-cookie"))
    # Đăng nhập cục bộ qua http://127.0.0.1:8990 → không Secure (cookie phải dùng được trên http).
    r = local_client.post("/api/auth/login", json={"password": owner_password})
    sess = next(c for c in r.headers.get_list("set-cookie") if c.startswith("sr_session="))
    assert "secure" not in sess.lower()


def test_login_wrong_password(remote_client, owner_password):
    r = _login(remote_client, "khong-dung")
    assert r.status_code == 401 and r.json()["error_code"] == "invalid_credentials"
    assert "sr_session" not in remote_client.cookies


def test_login_without_any_password(remote_client):
    r = _login(remote_client, "gi-cung-duoc")
    assert r.status_code == 403 and r.json()["error_code"] == "password_not_set"
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
    remote_client.cookies.delete("sr_csrf")
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
    sessions = [x for x in _op(local_client, "session.list").json() if x["ip"] == "1.2.3.4"]
    assert len(sessions) == 1 and sessions[0]["active"] and sessions[0]["role"] == "owner"
    assert "token_hash" not in sessions[0]
    assert _op(local_client, "session.revoke", {"id": sessions[0]["id"]}).status_code == 200
    assert _op(remote_client, "system.info", headers=_csrf_headers(remote_client)).status_code == 401
    by_id = {x["id"]: x for x in _op(local_client, "session.list").json()}
    assert by_id[sessions[0]["id"]]["active"] is False
    assert _op(local_client, "session.revoke", {"id": 999}).status_code == 404


def test_password_change_revokes_sessions_and_tokens_of_role(local_client, remote_client, owner_password):
    _login(remote_client, owner_password)
    tok = _op(local_client, "token.create", {"name": "agent1", "role": "owner"}).json()["token"]
    bh = {**REMOTE_H, "Authorization": f"Bearer {tok}"}
    assert _op(remote_client, "system.info", headers=bh).status_code == 200
    r = _op(local_client, "auth.set_password",
            {"role": "owner", "password": "mat-khau-moi-789", "current_password": owner_password})
    assert r.status_code == 200 and r.json()["revoked_tokens"] == 1 and r.json()["revoked_sessions"] == 2
    assert _op(remote_client, "system.info", headers=_csrf_headers(remote_client)).status_code == 401
    assert _op(remote_client, "system.info", headers=bh).status_code == 401
    assert _op(local_client, "system.info").status_code == 401  # phiên của chính người đổi cũng bị thu hồi
    assert _login(remote_client, owner_password).status_code == 401
    assert _login(remote_client, "mat-khau-moi-789").status_code == 200


def test_password_change_requires_current_owner_password(local_client, remote_client, owner_password):
    _login(remote_client, owner_password)
    body = {"role": "owner", "password": "doi-tu-xa-000"}
    for extra in ({}, {"current_password": "sai-roi-123"}):
        r = _op(remote_client, "auth.set_password", {**body, **extra}, headers=_csrf_headers(remote_client))
        assert r.status_code == 403 and r.json()["error_code"] == "invalid_current_password"
    # Máy cục bộ (đã có mật khẩu) cũng phải nhập mật khẩu hiện tại.
    r = _op(local_client, "auth.set_password", body)
    assert r.status_code == 403 and r.json()["error_code"] == "invalid_current_password"
    r = _op(remote_client, "auth.set_password", {**body, "current_password": owner_password},
            headers=_csrf_headers(remote_client))
    assert r.status_code == 200


def test_viewer_password_change_requires_owner_password(local_client, viewer_password, owner_password):
    body = {"role": "viewer", "password": "xem-moi-12345"}
    r = _op(local_client, "auth.set_password", {**body, "current_password": viewer_password})
    assert r.status_code == 403  # phải là mật khẩu CHỦ, không phải mật khẩu người xem
    assert _op(local_client, "auth.set_password", {**body, "current_password": owner_password}).status_code == 200


def test_viewer_password_needs_owner_first(local_client):
    r = _op(local_client, "auth.set_password", {"role": "viewer", "password": "xem-12345678"})
    assert r.status_code == 403 and r.json()["error_code"] == "owner_password_required"


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


def test_require_login_local_default_on(local_client, owner_password, app):
    # nginx `proxy_pass http://127.0.0.1:8990` không gắn header → trông như máy cục bộ: vẫn phải đăng nhập.
    fresh = _extra_client(app, BASE, 50003)
    r = _op(fresh, "system.info")
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required"
    s = fresh.get("/api/auth/status").json()
    assert s["local"] is True and s["authenticated"] is False
    # Tắt cờ (từ phiên đã đăng nhập) → máy cục bộ lại là chủ không cần đăng nhập.
    r = _op(local_client, "settings.set_global", {"values": {"remote.require_login_local": False}})
    assert r.status_code == 200
    assert fresh.get("/api/auth/status").json()["via"] == "local"
    assert _op(fresh, "system.info").status_code == 200
    fresh.close()


def test_require_login_local_ignored_without_owner_password(local_client):
    r = _op(local_client, "settings.explain").json()
    assert next(x for x in r if x["key"] == "remote.require_login_local")["value"] is True  # mặc định bật
    assert _op(local_client, "system.info").status_code == 200  # chưa có mật khẩu: không tự khoá mình ngoài cửa


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


# --- Fix round 1 --------------------------------------------------------------------------------------------------


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Ghi lại độ trễ chống dò mật khẩu thay vì ngủ thật."""
    from sagareel.api import routes_auth

    recorded: list[float] = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(routes_auth, "_sleep", fake_sleep)
    return recorded


def test_rate_limit_ignores_untrusted_ip_headers(remote_client, owner_password):
    # Header tin cậy mặc định là CF-Connecting-IP (cố định 1.2.3.4): đổi XFF / X-Real-IP không lách được.
    for i in range(5):
        h = {"X-Forwarded-For": f"9.9.9.{i}", "X-Real-IP": f"8.8.8.{i}"}
        assert _login(remote_client, "sai", headers=h).status_code == 401
    r = _login(remote_client, owner_password, headers={"X-Forwarded-For": "7.7.7.7", "X-Real-IP": "6.6.6.6"})
    assert r.status_code == 429


def test_rate_limit_without_trusted_header_uses_socket_peer(local_client, remote_client, owner_password):
    assert _op(local_client, "settings.set_global", {"values": {"remote.client_ip_header": ""}}).status_code == 200
    for i in range(5):
        assert _login(remote_client, "sai", headers={"CF-Connecting-IP": f"10.0.0.{i}"}).status_code == 401
    r = _login(remote_client, owner_password, headers={"CF-Connecting-IP": "10.0.0.99"})
    assert r.status_code == 429


def test_junk_flood_slows_but_never_blocks_owner(remote_client, owner_password, sleeps):
    for i in range(12):  # mỗi IP 1 lần sai: không IP nào chạm hạn mức 5
        assert _login(remote_client, "rac", headers={"CF-Connecting-IP": f"203.0.113.{i}"}).status_code == 401
    r = _login(remote_client, owner_password, headers={"CF-Connecting-IP": "198.51.100.7"})
    assert r.status_code == 200 and r.json()["role"] == "owner"
    assert sleeps and 0 < sleeps[-1] <= 3.0  # bị làm chậm, không bị từ chối


def test_login_limiter_delay_is_capped():
    from sagareel.services.auth import LoginLimiter

    lim = LoginLimiter()
    assert lim.delay("127.0.0.1") == 0
    for _ in range(5):
        lim.record_peer_failure("127.0.0.1")
    assert lim.delay("127.0.0.1") == 0  # 5 lần đầu miễn phí
    lim.record_peer_failure("127.0.0.1")
    assert lim.delay("127.0.0.1") == pytest.approx(0.1)
    for _ in range(500):
        lim.record_peer_failure("127.0.0.1")
    assert lim.delay("127.0.0.1") == 3.0
    assert lim.delay("10.0.0.1") == 0  # peer khác không bị ảnh hưởng


def test_local_login_failures_not_counted_for_peer(local_client, remote_client, owner_password, sleeps):
    for i in range(5):
        assert _login(remote_client, "rac", headers={"CF-Connecting-IP": f"203.0.113.{i}"}).status_code == 401
    for _ in range(5):  # sai từ máy cục bộ (cùng peer 127.0.0.1) — không được làm chậm người dùng từ xa
        assert local_client.post("/api/auth/login", json={"password": "sai-cuc-bo"}).status_code == 401
    r = _login(remote_client, owner_password, headers={"CF-Connecting-IP": "198.51.100.8"})
    assert r.status_code == 200 and sleeps == []


def test_cross_site_fetch_metadata_blocks_writes(local_client, remote_client, owner_token):
    h = {"Sec-Fetch-Site": "cross-site"}
    r = _op(local_client, "settings.set_global", {"values": {}}, headers=h)
    assert r.status_code == 403 and r.json()["error_code"] == "csrf_failed"
    bh = {**REMOTE_H, **h, "Authorization": f"Bearer {owner_token}"}
    r = _op(remote_client, "system.info", headers=bh)
    assert r.status_code == 403 and r.json()["error_code"] == "csrf_failed"
    assert _op(local_client, "system.info", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert local_client.get("/api/ops", headers=h).status_code == 200  # GET không bị chặn


def test_sse_with_bearer(remote_client, owner_token):
    bh = {**REMOTE_H, "Authorization": f"Bearer {owner_token}"}
    with remote_client.stream("GET", "/api/events", headers=bh) as r:
        assert r.status_code == 200
        assert next(ln for ln in r.iter_lines() if ln.startswith("event:")) == "event: server.hello"
    assert remote_client.get("/api/events", headers=REMOTE_H).status_code == 401


def test_sse_closes_when_session_revoked(local_client, remote_client, owner_password, monkeypatch):
    from sagareel.api import routes_events

    monkeypatch.setattr(routes_events, "REVALIDATE_SECONDS", 0.2)
    _login(remote_client, owner_password)
    sid = next(x["id"] for x in _op(local_client, "session.list").json() if x["ip"] == "1.2.3.4")
    with remote_client.stream("GET", "/api/events", headers=REMOTE_H) as r:
        lines = r.iter_lines()
        assert next(ln for ln in lines if ln.startswith("event:")) == "event: server.hello"
        assert _op(local_client, "session.revoke", {"id": sid}).status_code == 200
        rest = list(lines)  # luồng phải tự kết thúc sau lần kiểm lại kế tiếp
    assert not any(ln.startswith("event:") for ln in rest)
    assert local_client.app.state.services.bus.subscriber_count == 0


def test_sse_stays_open_while_valid(local_client, monkeypatch):
    from sagareel.api import routes_events
    from sagareel.services import auth

    monkeypatch.setattr(routes_events, "REVALIDATE_SECONDS", 0.05)
    calls = []
    real = auth.principal_still_valid

    def counting(services, principal):
        calls.append(principal.via)
        return real(services, principal)

    monkeypatch.setattr(auth, "principal_still_valid", counting)
    bus = local_client.app.state.services.bus

    async def publish_later():
        import anyio

        await anyio.sleep(0.3)
        bus.publish("x", {"n": 1})

    with local_client.stream("GET", "/api/events") as r:
        lines = r.iter_lines()
        assert next(ln for ln in lines if ln.startswith("event:")) == "event: server.hello"
        local_client.portal.start_task_soon(publish_later)
        assert next(ln for ln in lines if ln.startswith("event:")) == "event: x"
    assert calls and set(calls) == {"local"}


@pytest.mark.parametrize("path", [
    "/api%2Fops", "/api\\ops", "/API%2fops/system.info", "/media%2F..%2Fapi/health",
])
def test_remote_path_variants_protected_http(remote_client, path):
    r = remote_client.get(REMOTE_BASE + path, headers=REMOTE_H)
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required"


@pytest.mark.parametrize("path, protected", [
    ("/media/../api/health", True),
    ("/api/ops/../health", True),
    ("/api/health/.", True),
    ("/api%252Fops", True),
    ("\\api\\ops", True),
    ("/api/./health", True),
    ("/assets/../index.html", False),
    ("/assets/app.js", False),
    ("/api/health", False),
    ("/api/auth/login", False),
    ("/", False),
])
def test_is_protected_path_variants(path, protected):
    assert security.is_protected_path(path) is protected


@pytest.mark.parametrize("host", ["localhost.:8990", "user@localhost:8990", "studio.example.com.", "127.0.0.1:8990:1"])
def test_odd_host_values_rejected(local_client, host):
    r = local_client.get("/api/health", headers={"Host": host})
    assert r.status_code == 400 and r.json()["error_code"] == "untrusted_host"


def test_websocket_rejected_untrusted_or_unauthenticated(local_client, remote_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as ei:
        with local_client.websocket_connect("/api/ws", headers={"Host": "evil.com"}):
            pass
    assert ei.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as ei:
        with remote_client.websocket_connect("/api/ws", headers=REMOTE_H):
            pass
    assert ei.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as ei:  # cục bộ nhưng Origin lạ (CSWSH)
        with local_client.websocket_connect("/api/ws", headers={"Origin": "https://evil.com"}):
            pass
    assert ei.value.code == 1008


def test_client_ip_trusts_only_configured_header():
    req = _req(headers={"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2", "X-Real-IP": "3.3.3.3"})
    assert security.client_ip(req, "cf-connecting-ip") == "1.1.1.1"
    assert security.client_ip(req, "") == "127.0.0.1"
    assert security.client_ip(req, "x-real-ip") == "3.3.3.3"
    assert security.client_ip(_req(headers={"X-Forwarded-For": "6.6.6.6, 5.5.5.5"}), "x-forwarded-for") == "5.5.5.5"
    # Kết nối không qua loopback (không phải tunnel trên máy này): không tin header nào.
    assert security.client_ip(_req(client=("192.168.1.9", 1), headers={"CF-Connecting-IP": "1.1.1.1"}),
                              "cf-connecting-ip") == "192.168.1.9"
