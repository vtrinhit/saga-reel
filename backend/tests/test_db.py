from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from sagareel.db.base import Base
from sagareel.db.models import JobLog, LlmTask, ProviderConnection, Template
from sagareel.db.session import Database

M0_TABLES = {
    "global_settings",
    "provider_connection",
    "llm_task",
    "template",
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
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_json_column_keeps_vietnamese(db):
    with db.session() as s:
        s.add(Template(key="t", kind="system", body="Ễ ộ ữ ẩ", locked=False, builtin_hash=""))
    with db.session() as s:
        assert s.scalar(select(Template.body)) == "Ễ ộ ữ ẩ"


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
            s.add(Template(key="t", kind="system", body="x", locked=False, builtin_hash=""))
            s.flush()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.session() as s:
        assert s.scalar(select(Template)) is None


def test_foreign_keys_enforced_after_migrate(db):
    with pytest.raises(IntegrityError):
        with db.session() as s:
            s.add(JobLog(job_id=999, level="info", message="mồ côi"))


def test_updated_at_changes_on_update(db):
    # Mốc cũ cố định: đồng hồ Windows có độ phân giải ~15 ms nên hai lần utcnow() liên tiếp có thể trùng nhau.
    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with db.session() as s:
        t = Template(key="t", kind="system", body="a", locked=False, builtin_hash="", updated_at=old)
        s.add(t)
    with db.session() as s:
        assert s.scalar(select(Template.updated_at)) == old
        s.scalar(select(Template)).body = "b"
    with db.session() as s:
        t2 = s.scalar(select(Template))
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
