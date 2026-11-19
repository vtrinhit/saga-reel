"""Task BF — migration 0006: cột mới (token tạm dừng, take.selected_by, spend.project_id, media trash) + điền
spend.project_id từ job."""

from __future__ import annotations

from alembic import command
from sagareel.db.session import Database, alembic_config
from sqlalchemy import text

NOW = "2026-11-20 00:00:00.000000"


def _upgrade(database: Database, rev: str) -> None:
    cfg = alembic_config(database.url)
    with database.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, rev)
            conn.commit()
        finally:
            conn.rollback()
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def test_upgrade_backfills_spend_project(tmp_path):
    database = Database(tmp_path / "old.db")
    _upgrade(database, "0005")
    with database.engine.begin() as conn:
        jid = conn.execute(text(
            "INSERT INTO job (created_at, updated_at, type, status, priority, lane, project_id, params, progress, "
            "stage, external_state, attempt, max_attempts, result, cost_estimate, cost_actual) VALUES "
            "(:n, :n, 'generate.image', 'succeeded', 0, 'x', 7, '{}', 1, '', '{}', 0, 3, '{}', '{}', '{}') "
            "RETURNING id"), {"n": NOW}).scalar()
        conn.execute(text("INSERT INTO spend (created_at, kind, units, job_id) VALUES (:n, 'image', 1, :j)"),
                     {"n": NOW, "j": jid})
        conn.execute(text("INSERT INTO spend (created_at, kind, units, job_id) VALUES (:n, 'image', 1, NULL)"),
                     {"n": NOW})
        conn.execute(text("INSERT INTO api_token (created_at, name, token_hash, role, local_only) VALUES "
                          "(:n, 'a', 'h', 'owner', 0)"), {"n": NOW})
    _upgrade(database, "head")
    with database.engine.begin() as conn:
        assert [r[0] for r in conn.execute(text("SELECT project_id FROM spend ORDER BY id"))] == [7, None]
        assert conn.execute(text("SELECT suspended FROM api_token")).scalar() == 0
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(media)"))}
        assert {"trashed_at", "purged_at"} <= cols
        assert "selected_by" in {r[1] for r in conn.execute(text("PRAGMA table_info(take)"))}
