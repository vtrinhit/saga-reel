"""Task 8b: token cho agent cục bộ (`data/local-agent.token`) tạo lúc khởi động app."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys

import pytest
from sqlalchemy import select

from sagareel import local_agent
from sagareel.app import create_app
from sagareel.config import load_settings, resolve_data_dir
from sagareel.db.models import ApiToken

OWNER_PW = "mat-khau-chu-123"


def _token_file(settings):
    return settings.data_dir / "local-agent.token"


def _rows(app) -> list[ApiToken]:
    with app.state.services.db.session() as s:
        return list(s.scalars(select(ApiToken).where(ApiToken.name == "local-agent").order_by(ApiToken.id)))


def _start(settings, make_local_client):
    app = create_app(settings)
    return app, make_local_client(app)


def test_startup_creates_token_file_and_row(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        raw = _token_file(settings).read_text(encoding="utf-8").strip()
        assert raw.startswith("sr_") and len(raw) > 30
        rows = _rows(app)
        assert len(rows) == 1
        row = rows[0]
        assert row.role == "owner" and row.expires_at is None and row.revoked_at is None
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()  # DB chỉ giữ hash


def test_restart_reuses_valid_token(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        first = _token_file(settings).read_text(encoding="utf-8")
    app, c = _start(settings, make_local_client)
    with c:
        assert _token_file(settings).read_text(encoding="utf-8") == first
        assert len(_rows(app)) == 1


def test_deleted_file_rotates_token_and_revokes_old_row(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        old = _token_file(settings).read_text(encoding="utf-8").strip()
    _token_file(settings).unlink()
    app, c = _start(settings, make_local_client)
    with c:
        new = _token_file(settings).read_text(encoding="utf-8").strip()
        assert new != old
        rows = _rows(app)
        assert len(rows) == 2 and rows[0].revoked_at is not None and rows[1].revoked_at is None
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {new}"}).status_code == 200


@pytest.mark.parametrize("content", ["", "rác không phải token", "sr_" + "a" * 43, "﻿"])
def test_unknown_or_garbage_file_is_replaced(settings, make_local_client, content):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _token_file(settings).write_text(content, encoding="utf-8")
    app, c = _start(settings, make_local_client)
    with c:
        raw = _token_file(settings).read_text(encoding="utf-8").strip()
        assert raw != content.strip() and raw.startswith("sr_")
        assert _rows(app)[-1].token_hash == hashlib.sha256(raw.encode()).hexdigest()


def test_revoked_token_is_replaced_on_restart(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        old = _token_file(settings).read_text(encoding="utf-8").strip()
        tid = _rows(app)[0].id
        assert c.post("/api/ops/token.revoke", json={"id": tid}).status_code == 200
    app, c = _start(settings, make_local_client)
    with c:
        new = _token_file(settings).read_text(encoding="utf-8").strip()
        assert new != old
        assert [r.revoked_at is None for r in _rows(app)] == [False, True]


def test_token_authenticates_when_owner_password_set(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        raw = _token_file(settings).read_text(encoding="utf-8").strip()
        assert c.post("/api/ops/auth.set_password", json={"role": "owner", "password": OWNER_PW}).status_code == 200
        assert c.post("/api/ops/system.info", json={}).status_code == 401  # cục bộ cũng phải xác thực
        r = c.get("/api/auth/status", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 200
        r = c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 200, r.text


def test_raw_token_never_logged_or_listed(settings, make_local_client, caplog):
    caplog.set_level(logging.DEBUG)
    app, c = _start(settings, make_local_client)
    with c:
        raw = _token_file(settings).read_text(encoding="utf-8").strip()
        listed = c.post("/api/ops/token.list", json={}).json()
        assert any(t["name"] == "local-agent" for t in listed)
        assert raw not in str(listed)
        for path in ("/api/ops/system.info", "/api/auth/status"):
            r = c.post(path, json={}) if path.startswith("/api/ops") else c.get(path)
            assert raw not in r.text
    assert raw not in caplog.text
    assert hashlib.sha256(raw.encode()).hexdigest() not in caplog.text


def test_local_agent_name_is_reserved(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        for name in ("local-agent", "Local-Agent"):
            r = c.post("/api/ops/token.create", json={"name": name, "role": "owner"})
            assert r.status_code == 422 and r.json()["error_code"] == "validation_failed", r.text
            assert "token" not in r.json()
        assert len(_rows(app)) == 1


def test_owner_password_change_reissues_local_token(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        assert c.post("/api/ops/auth.set_password", json={"role": "owner", "password": OWNER_PW}).status_code == 200
        old = _token_file(settings).read_text(encoding="utf-8").strip()
        h_old = {"Authorization": f"Bearer {old}"}
        r = c.post("/api/auth/login", json={"password": OWNER_PW, "remember": False})
        assert r.status_code == 200
        r = c.post("/api/ops/auth.set_password",
                   json={"role": "owner", "password": "mat-khau-moi-789", "current_password": OWNER_PW},
                   headers={"X-SR-CSRF": c.cookies["sr_csrf"]})
        assert r.status_code == 200, r.text
        assert r.json()["revoked_tokens"] == 1  # token cục bộ cũ bị thu hồi như mọi token chủ khác
        new = _token_file(settings).read_text(encoding="utf-8").strip()
        assert new != old
        assert c.post("/api/ops/system.info", json={}, headers=h_old).status_code == 401
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {new}"}).status_code == 200


def test_viewer_password_change_keeps_local_token(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        before = _token_file(settings).read_text(encoding="utf-8")
        assert c.post("/api/ops/settings.set_global", json={"values": {"remote.allow_viewer": True}}).status_code == 200
        assert c.post("/api/ops/auth.set_password", json={"role": "owner", "password": OWNER_PW}).status_code == 200
        assert c.post("/api/auth/login", json={"password": OWNER_PW, "remember": False}).status_code == 200
        csrf = {"X-SR-CSRF": c.cookies["sr_csrf"]}
        for body in ({"role": "viewer", "password": "mat-khau-xem-1"},
                     {"role": "viewer", "password": "mat-khau-xem-2", "current_password": OWNER_PW}):
            r = c.post("/api/ops/auth.set_password", json=body, headers=csrf)
            assert r.status_code == 200, r.text
        assert _token_file(settings).read_text(encoding="utf-8") == before
        h = {"Authorization": f"Bearer {before.strip()}"}
        assert c.post("/api/ops/system.info", json={}, headers=h).status_code == 200


def test_acl_failure_only_warns(settings, make_local_client, monkeypatch, caplog):
    def boom(*a, **kw):
        raise OSError("icacls không chạy được")

    monkeypatch.setattr(local_agent, "_restrict_acl", boom)
    caplog.set_level(logging.WARNING)
    app, c = _start(settings, make_local_client)
    with c:
        raw = _token_file(settings).read_text(encoding="utf-8").strip()
        assert raw.startswith("sr_")
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name.startswith("sagareel")]
    assert warnings and raw not in caplog.text


def test_token_file_write_failure_does_not_block_startup(settings, make_local_client, monkeypatch, caplog):
    def boom(*a, **kw):
        raise OSError("đĩa đầy")

    monkeypatch.setattr(local_agent, "write_token_file", boom)
    caplog.set_level(logging.WARNING)
    app, c = _start(settings, make_local_client)
    with c:
        assert c.get("/api/health").status_code == 200
        assert not _token_file(settings).exists()
        assert all(r.revoked_at is not None for r in _rows(app))  # không để lại token sống không ai giữ
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_acl_restricted_before_token_written(tmp_path, monkeypatch):
    seen = []
    real = local_agent._restrict_acl

    def spy(p):
        seen.append(p.read_bytes())  # lúc thu quyền, file tạm phải còn rỗng
        real(p)

    monkeypatch.setattr(local_agent, "_restrict_acl", spy)
    p = tmp_path / "local-agent.token"
    local_agent.write_token_file(p, "sr_" + "c" * 43)
    assert seen == [b""]
    assert p.read_text(encoding="utf-8").strip() == "sr_" + "c" * 43


@pytest.mark.skipif(sys.platform != "win32", reason="ACL Windows")
def test_icacls_nonzero_exit_only_warns(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(local_agent.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 1332, "", "No mapping"))
    caplog.set_level(logging.WARNING)
    p = tmp_path / "local-agent.token"
    local_agent.write_token_file(p, "sr_" + "b" * 43)
    assert p.read_text(encoding="utf-8").strip() == "sr_" + "b" * 43
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "sr_" + "b" * 43 not in caplog.text
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(sys.platform != "win32", reason="ACL Windows")
def test_windows_acl_restricted_to_current_user(settings, make_local_client):
    app, c = _start(settings, make_local_client)
    with c:
        out = subprocess.run(["icacls", str(_token_file(settings))], capture_output=True, text=True).stdout
    assert "(I)" not in out  # bỏ quyền kế thừa từ thư mục
    entries = [line for line in out.splitlines() if ":(" in line]
    assert len(entries) == 1 and "(F)" in entries[0]


@pytest.mark.skipif(sys.platform == "win32", reason="quyền POSIX")
def test_posix_mode_0600(settings, make_local_client):  # pragma: no cover - chỉ chạy ngoài Windows
    app, c = _start(settings, make_local_client)
    with c:
        assert (_token_file(settings).stat().st_mode & 0o777) == 0o600


# --- Helper dùng chung cho CLI/MCP --------------------------------------------------------------------------------


def test_read_token_validates_content(tmp_path):
    p = tmp_path / "local-agent.token"
    assert local_agent.read_token(p) is None  # chưa có file
    p.write_text("sr_" + "A" * 43 + "\r\n", encoding="utf-8")
    assert local_agent.read_token(p) == "sr_" + "A" * 43
    for bad in ("", "abc", "sr_có dấu cách", "sr_" + "a" * 600, "Bearer sr_" + "a" * 43):
        p.write_text(bad, encoding="utf-8")
        assert local_agent.read_token(p) is None
    p.write_bytes(b"\xff\xfe\x00")
    assert local_agent.read_token(p) is None


def test_resolve_data_dir_matches_load_settings(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.delenv("SAGAREEL_DATA_DIR", raising=False)
    assert resolve_data_dir(root) == root / "data"
    monkeypatch.setenv("SAGAREEL_DATA_DIR", "rel/data")
    assert resolve_data_dir(root) == root / "rel" / "data"
    assert not (root / "rel").exists()  # chỉ tính đường dẫn, không tạo thư mục
    s = load_settings(root=root, env_file=tmp_path / ".env.local", outputs_dir=tmp_path / "out")
    assert s.data_dir == resolve_data_dir(root)
    assert local_agent.token_path(s.data_dir) == s.data_dir / "local-agent.token"


# --- Phán quyết điều phối (a): token agent cục bộ chỉ hợp lệ trên request cục bộ thật ------------------------------

PUBLIC = "studio.example.com"


@pytest.fixture
def local_only_app(settings, make_local_client):
    app = create_app(settings)
    with make_local_client(app) as c:
        r = c.post("/api/ops/settings.set_global", json={"values": {"remote.public_hosts": [PUBLIC]}})
        assert r.status_code == 200, r.text
        assert c.post("/api/ops/auth.set_password", json={"role": "owner", "password": OWNER_PW}).status_code == 200
        yield app, c, _token_file(settings).read_text(encoding="utf-8").strip()


def test_local_agent_row_is_marked_local_only(local_only_app):
    app, c, raw = local_only_app
    with app.state.services.db.session() as s:
        tok = s.scalars(select(ApiToken).where(ApiToken.token_hash == hashlib.sha256(raw.encode()).hexdigest())).one()
        assert tok.local_only is True
    r = c.post("/api/ops/token.create", json={"name": "agent-thuong", "role": "owner"},
               headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403  # token không tạo được token (giữ nguyên)


def test_local_agent_token_accepted_on_local_request(local_only_app):
    app, c, raw = local_only_app
    r = c.get("/api/auth/status", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "owner" and body["via"] == "token"
    assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {raw}"}).status_code == 200


@pytest.mark.parametrize("extra", [
    {"CF-Connecting-IP": "1.2.3.4"},
    {"X-Forwarded-For": "1.2.3.4"},
    {"Forwarded": "for=1.2.3.4"},
    {"Host": PUBLIC},
    {"Host": "127.0.0.1:9999"},
])
def test_local_agent_token_rejected_when_not_local(local_only_app, extra):
    app, c, raw = local_only_app
    r = c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {raw}", **extra})
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required", r.text


def test_local_agent_token_rejected_from_non_loopback_peer(local_only_app, settings):
    from fastapi.testclient import TestClient

    app, c, raw = local_only_app
    remote = TestClient(app, base_url="http://127.0.0.1:8990", client=("192.168.1.20", 50000))
    remote.portal = c.portal
    try:
        r = remote.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 401 and r.json()["error_code"] == "auth_required"
    finally:
        remote.close()


def test_local_agent_token_rejected_remotely_even_without_local_login(local_only_app):
    app, c, raw = local_only_app
    h = {"Authorization": f"Bearer {raw}"}
    assert c.post("/api/ops/settings.set_global", json={"values": {"remote.require_login_local": False}},
                  headers=h).status_code == 200
    app.state.services.auth_cache.invalidate()
    assert c.post("/api/ops/system.info", json={}, headers=h).status_code == 200
    r = c.post("/api/ops/system.info", json={}, headers={**h, "CF-Connecting-IP": "1.2.3.4"})
    assert r.status_code == 401


def test_ordinary_token_still_works_through_tunnel(local_only_app):
    app, c, raw = local_only_app
    tok = c.post("/api/ops/token.create", json={"name": "agent1", "role": "owner"},
                 headers={"Authorization": f"Bearer {raw}"})
    assert tok.status_code == 403  # tạo bằng phiên đăng nhập
    assert c.post("/api/auth/login", json={"password": OWNER_PW, "remember": False}).status_code == 200
    tok = c.post("/api/ops/token.create", json={"name": "agent1", "role": "owner"},
                 headers={"X-SR-CSRF": c.cookies["sr_csrf"]})
    assert tok.status_code == 200, tok.text
    r = c.post("/api/ops/system.info", json={},
               headers={"Authorization": f"Bearer {tok.json()['token']}", "CF-Connecting-IP": "1.2.3.4",
                        "Host": PUBLIC})
    assert r.status_code == 200, r.text


def test_migration_0003_marks_existing_local_agent_rows(tmp_path):
    import sqlite3

    from alembic import command

    from sagareel.db.session import Database, alembic_config

    db = Database(tmp_path / "m.db")
    try:
        command.upgrade(alembic_config(db.url), "0002")
        conn = sqlite3.connect(tmp_path / "m.db")
        conn.executemany(
            "INSERT INTO api_token (name, token_hash, role, created_at) VALUES (?, ?, 'owner', '2026-11-19 00:00:00')",
            [("local-agent", "h1"), ("agent1", "h2")],
        )
        conn.commit()
        conn.close()
        db.migrate()
        with db.session() as s:
            assert {t.name: t.local_only for t in s.scalars(select(ApiToken))} == {"local-agent": True,
                                                                                    "agent1": False}
    finally:
        db.dispose()


# --- Review vòng 1 --------------------------------------------------------------------------------------------------


def test_kept_token_file_acl_is_reapplied(settings, make_local_client, monkeypatch):
    app, c = _start(settings, make_local_client)
    with c:
        pass
    calls = []
    monkeypatch.setattr(local_agent, "_restrict_acl", lambda p: calls.append(p))
    app, c = _start(settings, make_local_client)
    with c:
        pass
    assert calls == [_token_file(settings)]


def test_kept_token_acl_failure_only_warns(settings, make_local_client, monkeypatch, caplog):
    app, c = _start(settings, make_local_client)
    with c:
        raw = _token_file(settings).read_text(encoding="utf-8").strip()

    def boom(p):
        raise OSError("không được")

    monkeypatch.setattr(local_agent, "_restrict_acl", boom)
    caplog.set_level(logging.WARNING)
    app, c = _start(settings, make_local_client)
    with c:
        assert _token_file(settings).read_text(encoding="utf-8").strip() == raw
    assert any(r.levelno == logging.WARNING for r in caplog.records) and raw not in caplog.text


def test_concurrent_ensure_keeps_file_and_db_in_sync(settings, make_local_client):
    import threading

    from sagareel.services import auth

    app, c = _start(settings, make_local_client)
    with c:
        services = app.state.services
        for _ in range(3):
            _token_file(settings).unlink()
            barrier = threading.Barrier(4)

            def worker():
                barrier.wait()
                auth.ensure_local_agent_token(services)

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            raw = _token_file(settings).read_text(encoding="utf-8").strip()
            active = [r for r in _rows(app) if r.revoked_at is None]
            assert len(active) == 1 and active[0].token_hash == hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.skipif(sys.platform != "win32", reason="ACL Windows")
def test_icacls_uses_current_user_sid(tmp_path, monkeypatch):
    calls = []
    real_run = local_agent.subprocess.run

    def spy(args, **kw):
        calls.append(list(args))
        return real_run(args, **kw)

    monkeypatch.setattr(local_agent.subprocess, "run", spy)
    local_agent.write_token_file(tmp_path / "local-agent.token", "sr_" + "s" * 43)
    icacls = [a for a in calls if a[0].lower().endswith("icacls.exe")]
    assert icacls and icacls[0][-1].startswith("*S-1-") and icacls[0][-1].endswith(":F")
    assert all(a[0].lower().startswith(os.environ.get("SystemRoot", r"C:\Windows").lower()) for a in calls)


@pytest.mark.skipif(sys.platform != "win32", reason="ACL Windows")
def test_sid_failure_falls_back_with_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(local_agent, "_sid_cache", [])
    monkeypatch.setattr(local_agent, "_current_user_sid", lambda: None)
    caplog.set_level(logging.WARNING)
    p = tmp_path / "local-agent.token"
    local_agent.write_token_file(p, "sr_" + "f" * 43)
    assert p.read_text(encoding="utf-8").strip() == "sr_" + "f" * 43
    assert any("SID" in r.getMessage() for r in caplog.records)


def test_pause_all_survives_local_token_rotation(settings, make_local_client):
    """Task BF: agents.pause_all tạm dừng cả token agent cục bộ; token tạo lại (mất file/khởi động lại) vẫn dừng."""
    app, c = _start(settings, make_local_client)
    with c:
        out = c.post("/api/ops/agents.pause_all", json={}).json()
        assert out["suspended"] is True and out["changed"] >= 1
        old = _token_file(settings).read_text(encoding="utf-8").strip()
        r = c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {old}"})
        assert r.status_code == 403 and r.json()["error_code"] == "token_suspended" and r.json()["hint"]
    _token_file(settings).unlink()
    app, c = _start(settings, make_local_client)
    with c:
        new = _token_file(settings).read_text(encoding="utf-8").strip()
        h = {"Authorization": f"Bearer {new}"}
        assert c.post("/api/ops/system.info", json={}, headers=h).status_code == 403
        assert c.post("/api/ops/agents.resume_all", json={}).status_code == 200
        assert c.post("/api/ops/system.info", json={}, headers=h).status_code == 200
