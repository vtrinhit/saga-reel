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
    # Test quên --data-dir cũng không bao giờ đụng data/sagareel.db thật.
    monkeypatch.setenv("SAGAREEL_DATA_DIR", str(tmp_path / "chua-dat-data-dir"))


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


def _local_agent_rows(settings) -> list[ApiToken]:
    db = Database(settings.db_path)
    try:
        with db.session() as s:
            return list(s.scalars(select(ApiToken).where(ApiToken.name == "local-agent").order_by(ApiToken.id)))
    finally:
        db.dispose()


def test_reset_owner_revokes_everything_clears_viewer_and_recreates_local_token(seeded, run):
    before = _state(seeded)
    code, out, err, asked = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\r\n")
    assert code == 0, err
    assert asked == []
    after = _state(seeded)
    assert security.verify_password(NEW_PW, after["hashes"]["owner"])
    assert "viewer" not in after["hashes"]  # khôi phục sau xâm nhập: mật khẩu người xem bị xoá
    for role in ("owner", "viewer"):  # mọi phiên + mọi token của MỌI vai trò
        assert all(after["sessions"][f"{role}-s{i}"] is not None for i in range(2))
        assert all(after["tokens"][f"{role}-t{i}"] is not None for i in range(2))
        assert after["tokens"][f"{role}-t1"] == before["tokens"][f"{role}-t1"]  # đã thu hồi từ trước: giữ mốc cũ
    # Token agent cục bộ mới: file + dòng local_only còn hiệu lực, khớp hash.
    raw = (seeded.data_dir / "local-agent.token").read_text(encoding="utf-8").strip()
    rows = _local_agent_rows(seeded)
    assert len(rows) == 1 and rows[0].revoked_at is None and rows[0].local_only is True
    assert rows[0].role == "owner" and rows[0].token_hash == security.sha256_hex(raw)
    assert "Đã đặt lại mật khẩu" in out and "owner" in out
    assert "4 phiên" in out and "2 token" in out
    assert "Đã xoá mật khẩu người xem" in out
    assert NEW_PW not in out + err and raw not in out + err


def test_reset_owner_replaces_existing_local_agent_token(seeded, run):
    from sagareel import local_agent

    db = Database(seeded.db_path)
    raw_old, digest = security.new_secret_token()
    with db.session() as s:
        s.add(ApiToken(name="local-agent", token_hash=digest, role="owner", local_only=True))
    db.dispose()
    local_agent.write_token_file(seeded.data_dir / "local-agent.token", raw_old)
    code, out, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 0, err
    raw = (seeded.data_dir / "local-agent.token").read_text(encoding="utf-8").strip()
    assert raw != raw_old
    rows = _local_agent_rows(seeded)
    assert [r.revoked_at is None for r in rows] == [False, True]
    assert "3 token" in out  # owner-t0, viewer-t0, local-agent cũ


def test_reset_owner_keep_viewer(seeded, run):
    before = _state(seeded)
    code, out, err, _ = run(["--keep-viewer", "--data-dir", str(seeded.data_dir), "--password-stdin"],
                            stdin=NEW_PW + "\n")
    assert code == 0, err
    after = _state(seeded)
    assert after["hashes"]["viewer"] == before["hashes"]["viewer"]
    assert all(after["sessions"][f"viewer-s{i}"] is not None for i in range(2))  # phiên viewer vẫn bị thu hồi
    assert after["tokens"]["viewer-t0"] is not None
    assert "Giữ mật khẩu người xem" in out


def test_reset_owner_keep_viewer_refuses_viewer_password(seeded, run):
    before = _state(seeded)
    code, _, err, _ = run(["--keep-viewer", "--data-dir", str(seeded.data_dir), "--password-stdin"],
                          stdin="mat-khau-xem-222\n")
    assert code == 2 and err.startswith("password_reused: ")
    assert _state(seeded) == before
    # Không giữ viewer: mật khẩu người xem cũ bị xoá nên trùng cũng không sao.
    code, _, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin="mat-khau-xem-222\n")
    assert code == 0, err


def test_keep_viewer_only_for_owner(seeded, run):
    code, _, err, _ = run(["--role", "viewer", "--keep-viewer", "--data-dir", str(seeded.data_dir),
                           "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 2 and err.startswith("invalid_arguments: ")


def test_reset_owner_token_file_failure_still_resets(seeded, run, monkeypatch):
    from sagareel import local_agent

    def boom(*a, **kw):
        raise OSError("đĩa đầy")

    monkeypatch.setattr(local_agent, "write_token_file", boom)
    code, out, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 0, err
    assert security.verify_password(NEW_PW, _state(seeded)["hashes"]["owner"])
    assert _local_agent_rows(seeded) == []  # không có token sống mà không ai giữ
    assert "local-agent.token" in err and "khởi động" in err


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
    assert not (seeded.data_dir / "local-agent.token").exists()  # reset viewer không đụng token cục bộ


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
    new = token_file.read_text(encoding="utf-8").strip()  # reset đã tạo lại file
    assert new != old
    app = create_app(settings)
    with make_local_client(app) as c:
        assert token_file.read_text(encoding="utf-8").strip() == new  # app khởi động giữ token reset vừa tạo
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert c.post("/api/ops/system.info", json={}, headers={"Authorization": f"Bearer {new}"}).status_code == 200
        assert c.post("/api/auth/login", json={"password": "mat-khau-cu-111", "remember": False}).status_code == 401
        assert c.post("/api/auth/login", json={"password": NEW_PW, "remember": False}).status_code == 200


def test_module_entry_point_exists():
    assert callable(reset_password.main)


# --- Review vòng 1 --------------------------------------------------------------------------------------------------


def test_outdated_schema_is_refused_before_prompt(settings, run):
    from alembic import command

    from sagareel.db.session import alembic_config

    db = Database(settings.db_path)
    command.upgrade(alembic_config(db.url), "0002")  # DB cũ: chưa có api_token.local_only
    db.dispose()
    token_file = settings.data_dir / "local-agent.token"
    token_file.write_text("sr_" + "o" * 43 + "\n", encoding="utf-8")
    code, _, err, asked = run(["--data-dir", str(settings.data_dir)], answers=[NEW_PW, NEW_PW])
    assert code == 2 and err.startswith("db_not_initialized: ")
    assert "Chạy app một lần (để nâng cấp DB) rồi tắt, sau đó chạy lại lệnh này" in err
    assert asked == []
    assert token_file.read_text(encoding="utf-8") == "sr_" + "o" * 43 + "\n"


def test_commit_failure_restores_token_file_and_db(seeded, run, monkeypatch):
    import sqlite3

    from sagareel import local_agent

    old_raw, digest = security.new_secret_token()
    db = Database(seeded.db_path)
    with db.session() as s:
        s.add(ApiToken(name="local-agent", token_hash=digest, role="owner", local_only=True))
    db.dispose()
    token_file = seeded.data_dir / "local-agent.token"
    local_agent.write_token_file(token_file, old_raw)
    before = _state(seeded)

    def failing_commit(conn):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(reset_password, "_commit", failing_commit)
    code, out, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 2 and err.startswith("db_error: ")
    assert token_file.read_text(encoding="utf-8").strip() == old_raw  # file trả về như cũ
    assert _state(seeded) == before  # transaction đã rollback


def test_commit_failure_removes_new_token_file_when_none_before(seeded, run, monkeypatch):
    import sqlite3

    monkeypatch.setattr(reset_password, "_commit",
                        lambda conn: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    code, _, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 2 and err.startswith("db_error: ")
    assert not (seeded.data_dir / "local-agent.token").exists()


def test_token_file_written_after_row_inserted(seeded, run, monkeypatch):
    """Ghi file sau INSERT (cùng transaction, trước COMMIT): lúc ghi, dòng `local_only` đã có trong transaction."""
    from sagareel import local_agent

    seen = []
    real_write = local_agent.write_token_file
    real_recreate = reset_password._recreate_local_token

    def spy_recreate(conn, path, now):
        def spy_write(p, raw):
            n = conn.execute("SELECT COUNT(*) FROM api_token WHERE token_hash = ? AND local_only = 1",
                             (security.sha256_hex(raw),)).fetchone()[0]
            seen.append(n)
            real_write(p, raw)

        monkeypatch.setattr(local_agent, "write_token_file", spy_write)
        return real_recreate(conn, path, now)

    monkeypatch.setattr(reset_password, "_recreate_local_token", spy_recreate)
    code, out, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 0, err
    assert seen == [1]


def test_help_and_output_mention_real_paths_and_all_roles(seeded, run, tmp_path):
    code, out, _, _ = run(["--help"])
    assert code == 0 and "mọi vai trò" in out
    code, out, err, _ = run(["--data-dir", str(seeded.data_dir), "--password-stdin"], stdin=NEW_PW + "\n")
    assert code == 0, err
    assert str(seeded.data_dir / "local-agent.token") in out
