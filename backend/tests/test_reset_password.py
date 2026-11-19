"""Task 8b: `python -m sagareel reset-password` — đặt lại mật khẩu ngoại tuyến, thẳng trên SQLite."""

from __future__ import annotations

import io
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from sagareel import __main__ as entry
from sagareel.app import create_app
from sagareel.cli import main as cli_main
from sagareel.cli import reset_password
from sagareel.core import security
from sagareel.db.base import utcnow
from sagareel.db.models import ApiToken, AuthSecret, AuthSession
from sagareel.db.session import Database

NEW_PW = "mat-khau-moi-456"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGAREEL_CACHE_DIR", str(tmp_path / "cli-cache"))
    monkeypatch.delenv("SAGAREEL_URL", raising=False)
    monkeypatch.delenv("SAGAREEL_TOKEN", raising=False)
    monkeypatch.delenv("SAGAREEL_DATA_DIR", raising=False)


@pytest.fixture
def seeded(settings):
    """DB tạm đã migrate: mật khẩu owner + viewer, mỗi vai trò 2 phiên + 2 token (1 đã thu hồi sẵn)."""
    db = Database(settings.db_path)
    db.migrate()
    now = utcnow()
    with db.session() as s:
        s.add(AuthSecret(role="owner", password_hash=security.hash_password("mat-khau-cu-111")))
        s.add(AuthSecret(role="viewer", password_hash=security.hash_password("mat-khau-xem-222")))
        for role in ("owner", "viewer"):
            for i in range(2):
                s.add(AuthSession(token_hash=f"{role}-s{i}", role=role, expires_at=now + timedelta(hours=1)))
                s.add(ApiToken(name=f"{role}-t{i}", token_hash=f"{role}-t{i}", role=role,
                               revoked_at=now if i == 1 else None))
    db.dispose()
    return settings


def _state(settings) -> dict:
    db = Database(settings.db_path)
    try:
        with db.session() as s:
            return {
                "hashes": {r.role: r.password_hash for r in s.scalars(select(AuthSecret))},
                "sessions": {r.token_hash: r.revoked_at for r in s.scalars(select(AuthSession))},
                "tokens": {r.token_hash: r.revoked_at for r in s.scalars(select(ApiToken))},
            }
    finally:
        db.dispose()


@pytest.fixture
def run(capsys, monkeypatch):
    def _run(argv, stdin=None, answers=None):
        asked = []
        if stdin is not None:
            monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8"))
        it = iter(answers or [])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": asked.append(prompt) or next(it))
        capsys.readouterr()
        code = entry.main(["reset-password", *argv])
        out = capsys.readouterr()
        return code, out.out, out.err, asked

    return _run


def test_reset_owner_from_stdin_revokes_owner_sessions_and_tokens(seeded, run):
    before = _state(seeded)
    code, out, err, asked = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\r\n")
    assert code == 0, err
    assert asked == []
    after = _state(seeded)
    assert security.verify_password(NEW_PW, after["hashes"]["owner"])
    assert after["hashes"]["viewer"] == before["hashes"]["viewer"]
    assert all(after["sessions"][f"owner-s{i}"] is not None for i in range(2))
    assert all(after["tokens"][f"owner-t{i}"] is not None for i in range(2))
    assert after["tokens"]["owner-t1"] == before["tokens"]["owner-t1"]  # đã thu hồi từ trước: giữ mốc cũ
    assert all(after["sessions"][f"viewer-s{i}"] is None for i in range(2))
    assert after["tokens"]["viewer-t0"] is None
    assert "Đã đặt lại mật khẩu" in out and "owner" in out
    assert "2 phiên" in out and "1 token" in out
    assert NEW_PW not in out + err


def test_reset_prompts_twice_with_getpass(seeded, run):
    code, out, err, asked = run(["--data-dir", str(seeded.data_dir)], answers=[NEW_PW, NEW_PW])
    assert code == 0, err
    assert len(asked) == 2
    assert security.verify_password(NEW_PW, _state(seeded)["hashes"]["owner"])


def test_mismatch_short_and_empty_passwords_change_nothing(seeded, run):
    before = _state(seeded)
    code, _, err, _ = run(["--data-dir", str(seeded.data_dir)], answers=[NEW_PW, NEW_PW + "x"])
    assert code == 2 and err.startswith("password_mismatch: ")
    code, _, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin="ngan\n")
    assert code == 2 and err.startswith("password_too_short: ")
    code, _, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin="")
    assert code == 2 and err.startswith("password_empty: ")
    code, _, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin="x" * 1025)
    assert code == 2 and err.startswith("password_too_long: ")
    assert _state(seeded) == before


def test_reset_viewer_refuses_owner_password(seeded, run):
    before = _state(seeded)
    code, _, err, _ = run(["--role", "viewer", "--data-dir", str(seeded.data_dir), "--password-stdin"],
                          stdin="mat-khau-cu-111\n")
    assert code == 2 and err.startswith("password_reused: ")
    assert _state(seeded) == before


def test_reset_viewer_revokes_only_viewer(seeded, run):
    code, out, err, _ = run(["--role", "viewer", "--data-dir", str(seeded.data_dir), "--password-stdin"],
                            stdin=NEW_PW + "\n")
    assert code == 0, err
    after = _state(seeded)
    assert security.verify_password(NEW_PW, after["hashes"]["viewer"])
    assert security.verify_password("mat-khau-cu-111", after["hashes"]["owner"])
    assert all(after["sessions"][f"viewer-s{i}"] is not None for i in range(2))
    assert after["tokens"]["viewer-t0"] is not None
    assert after["sessions"]["owner-s0"] is None and after["tokens"]["owner-t0"] is None


def test_viewer_reset_needs_owner_password(settings, run):
    db = Database(settings.db_path)
    db.migrate()
    db.dispose()
    code, _, err, _ = run(["--role", "viewer", "--data-dir", str(settings.data_dir), "--password-stdin"],
                          stdin=NEW_PW + "\n")
    assert code == 2 and err.startswith("owner_password_required: ")
    # Owner thì đặt được dù chưa có mật khẩu nào (người có quyền vào thư mục data là chủ máy).
    code, _, err, _ = run(["--data-dir", str(settings.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 0, err
    assert security.verify_password(NEW_PW, _state(settings)["hashes"]["owner"])


def test_clear_viewer_password(seeded, run):
    code, out, err, asked = run(["--role", "viewer", "--clear", "--data-dir", str(seeded.data_dir)])
    assert code == 0, err
    assert asked == []  # xoá không hỏi mật khẩu
    after = _state(seeded)
    assert "viewer" not in after["hashes"] and "owner" in after["hashes"]
    assert all(after["sessions"][f"viewer-s{i}"] is not None for i in range(2))
    assert after["tokens"]["viewer-t0"] is not None
    assert after["sessions"]["owner-s0"] is None
    assert "Đã xoá mật khẩu" in out


def test_clear_owner_is_refused(seeded, run):
    before = _state(seeded)
    code, _, err, _ = run(["--clear", "--data-dir", str(seeded.data_dir)])
    assert code == 2 and err.startswith("invalid_arguments: ")
    code, _, err, _ = run(["--clear", "--role", "owner", "--data-dir", str(seeded.data_dir)])
    assert code == 2 and err.startswith("invalid_arguments: ")
    assert _state(seeded) == before


def test_missing_db_is_not_created(tmp_path, run):
    data = tmp_path / "khong-co"
    code, _, err, asked = run(["--data-dir", str(data), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 2 and err.startswith("db_not_found: ")
    assert not data.exists()


def test_uninitialized_db_is_refused(tmp_path, run):
    data = tmp_path / "data"
    data.mkdir()
    (data / "sagareel.db").write_bytes(b"")
    code, _, err, _ = run(["--data-dir", str(data), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 2 and err.startswith("db_not_initialized: ")


def test_refuses_while_app_holds_db_open(seeded, run):
    """App chạy (engine còn kết nối nhàn rỗi trong pool, không giữ khoá ghi) → từ chối, không hỏi mật khẩu."""
    db = Database(seeded.db_path)
    try:
        with db.session() as s:
            s.scalars(select(AuthSecret)).all()  # để lại một kết nối WAL mở trong pool
        before = _state(seeded)
        code, _, err, asked = run(["--data-dir", str(seeded.data_dir)], answers=[NEW_PW, NEW_PW])
        assert code == 2 and err.startswith("app_running: ")
        assert "Tắt" in err or "tắt" in err
        assert asked == []
        code, _, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
        assert code == 2 and err.startswith("app_running: ")
    finally:
        db.dispose()
    assert _state(seeded)["hashes"] == before["hashes"]


def test_data_dir_from_env(seeded, run, monkeypatch):
    monkeypatch.setenv("SAGAREEL_DATA_DIR", str(seeded.data_dir))
    code, _, err, _ = run(["--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 0, err
    assert security.verify_password(NEW_PW, _state(seeded)["hashes"]["owner"])


def test_console_script_dispatches_without_network(seeded, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO((NEW_PW + "\n").encode()), encoding="utf-8"))

    def no_network(request):  # pragma: no cover - không được gọi
        raise AssertionError("reset-password không được gọi mạng")

    code = cli_main.main(["reset-password", "--data-dir", str(seeded.data_dir), "--password-stdin"],
                         transport=httpx.MockTransport(no_network))
    assert code == 0, capsys.readouterr().err
    assert security.verify_password(NEW_PW, _state(seeded)["hashes"]["owner"])


def test_help_mentions_offline(run):
    code, out, _, _ = run(["--help"])
    assert code == 0 and "reset-password" in out and "--password-stdin" in out


def test_end_to_end_owner_reset_rotates_local_agent_token(settings, make_local_client, run):
    token_file = settings.data_dir / "local-agent.token"
    app = create_app(settings)
    with make_local_client(app) as c:
        assert c.post("/api/ops/auth.set_password", json={"role": "owner", "password": "mat-khau-cu-111"}
                      ).status_code == 200
        old = token_file.read_text(encoding="utf-8").strip()
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {old}"}).status_code == 200
    code, out, err, _ = run(["--data-dir", str(settings.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 0, err
    assert old not in out + err
    app = create_app(settings)
    with make_local_client(app) as c:
        new = token_file.read_text(encoding="utf-8").strip()
        assert new != old
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {new}"}).status_code == 200
        assert c.post("/api/auth/login", json={"password": "mat-khau-cu-111", "remember": False}).status_code == 401
        assert c.post("/api/auth/login", json={"password": NEW_PW, "remember": False}).status_code == 200


def test_module_entry_point_exists():
    assert callable(reset_password.main)
