from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from sagareel.db.base import Base
from sagareel.db.models import JobLog, LlmTask, ProviderConnection, SkillFile
from sagareel.db.session import Database

M0_TABLES = {
    "global_settings",
    "provider_connection",
    "llm_task",
    "skill_file",
    "job",
    "job_log",
    "media",
    "spend",
    "change_set",
    "revision",
    "auth_secret",
    "session",
    "api_token",
}


def test_migrate_creates_tables_and_matches_models(db):
    from alembic.migration import MigrationContext
    from alembic.autogenerate import compare_metadata
    with db.engine.connect() as conn:
        from sagareel.db.session import include_name  # bỏ qua bảng ảo FTS5 (bible_fts*) không có model

        mc = MigrationContext.configure(conn, opts={"include_name": include_name})
        assert compare_metadata(mc, Base.metadata) == []
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_json_column_keeps_vietnamese(db):
    with db.session() as s:
        s.add(SkillFile(path="t", body="Ễ ộ ữ ẩ"))
    with db.session() as s:
        assert s.scalar(select(SkillFile.body)) == "Ễ ộ ữ ẩ"


def test_migrate_is_idempotent(db):
    db.migrate()


def test_m0_tables_exist(db):
    tables = set(inspect(db.engine).get_table_names())
    assert M0_TABLES <= tables
    assert M0_TABLES <= set(Base.metadata.tables)


def test_sqlite_pragmas(db):
    with db.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1  # NORMAL


def test_json_dict_stored_as_text_without_ascii_escape(db):
    with db.session() as s:
        s.add(ProviderConnection(
            kind="llm", adapter="openai_compat", name="llm-1", base_url="", api_key="",
            default_model="", options={"ghi_chú": "Tiếng Việt"},
        ))
    with db.engine.connect() as conn:
        raw = conn.exec_driver_sql("SELECT options FROM provider_connection").scalar()
    assert raw == '{"ghi_chú": "Tiếng Việt"}'
    with db.session() as s:
        pc = s.scalar(select(ProviderConnection))
        assert pc.options == {"ghi_chú": "Tiếng Việt"}
        assert pc.concurrency == 2 and pc.enabled is True and pc.health == {}
        assert pc.created_at.tzinfo is not None and pc.deleted_at is None


def test_json_columns_track_in_place_changes(db):
    with db.session() as s:
        s.add(ProviderConnection(kind="llm", adapter="a", name="c1"))
        s.add(LlmTask(key="k", label="K"))
    with db.session() as s:
        # Giữ tham chiếu tới object: identity map chỉ giữ yếu, object bị thu hồi thì thay đổi bị mất.
        pc = s.scalar(select(ProviderConnection))
        task = s.scalar(select(LlmTask))
        pc.options["x"] = 1
        task.fallbacks.append({"connection": "c2"})
    with db.session() as s:
        assert s.scalar(select(ProviderConnection)).options == {"x": 1}
        assert s.scalar(select(LlmTask)).fallbacks == [{"connection": "c2"}]


def test_session_rolls_back_on_error(db):
    try:
        with db.session() as s:
            s.add(SkillFile(path="t", body="x"))
            s.flush()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.session() as s:
        assert s.scalar(select(SkillFile)) is None


def test_foreign_keys_enforced_after_migrate(db):
    with pytest.raises(IntegrityError):
        with db.session() as s:
            s.add(JobLog(job_id=999, level="info", message="mồ côi"))


def test_updated_at_changes_on_update(db):
    # Mốc cũ cố định: đồng hồ Windows có độ phân giải ~15 ms nên hai lần utcnow() liên tiếp có thể trùng nhau.
    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with db.session() as s:
        t = SkillFile(path="t", body="a", updated_at=old)
        s.add(t)
    with db.session() as s:
        assert s.scalar(select(SkillFile.updated_at)) == old
        s.scalar(select(SkillFile)).body = "b"
    with db.session() as s:
        t2 = s.scalar(select(SkillFile))
        assert t2.updated_at > old and t2.created_at == t.created_at
        assert t2.updated_at.tzinfo is not None


def test_api_key_column_is_secret():
    assert ProviderConnection.__table__.c.api_key.info.get("secret") is True
    assert getattr(ProviderConnection, "__tracked__", False) is True


def test_app_startup_migrates(client, settings):
    services = client.app.state.services
    assert isinstance(services.db, Database)
    assert "job" in inspect(services.db.engine).get_table_names()
    assert settings.db_path.exists()


# --- Khoá ghi ngay (BEGIN IMMEDIATE) ---------------------------------------------------------------------------

def _in_txn(s):
    return s.connection().connection.driver_connection.in_transaction


def test_immediate_session_takes_write_lock_at_begin(db):
    with db.session(immediate=True) as s:
        s.scalars(select(SkillFile)).all()  # chỉ đọc nhưng đã giữ khoá ghi
        assert _in_txn(s)
        with db.engine.connect() as other:
            other.exec_driver_sql("PRAGMA busy_timeout=0")
            with pytest.raises(Exception, match="locked"):
                other.exec_driver_sql("BEGIN IMMEDIATE")
            other.exec_driver_sql("SELECT 1").scalar()  # đọc vẫn được (WAL)
        s.add(SkillFile(path="imm", body="x"))
    with db.session() as s:
        assert s.scalar(select(SkillFile.path)) == "imm"


def test_normal_session_stays_deferred_and_pragmas_hold(db):
    with db.session() as s:
        s.scalars(select(SkillFile)).all()
        assert not _in_txn(s)
        assert s.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    with db.session(immediate=True) as s:
        assert s.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert s.connection().exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_immediate_session_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with db.session(immediate=True) as s:
            s.add(SkillFile(path="rb", body="x"))
            s.flush()
            raise RuntimeError
    with db.session() as s:
        assert s.scalars(select(SkillFile)).all() == []
    db.migrate()  # migrate vẫn chạy được (idempotent) khi đã có listener begin
