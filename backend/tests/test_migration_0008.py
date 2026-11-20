"""Migration 0008 (rà soát bảo mật): key project/tập và đường dẫn skill không phân biệt hoa thường; dữ liệu trùng có
sẵn được gỡ trước khi tạo index."""

from __future__ import annotations

import pytest
from alembic import command
from sagareel.db.session import Database, alembic_config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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


def _insert(conn, table: str, **vals) -> int:
    """Chèn một dòng, tự điền cột NOT NULL không có mặc định (chuỗi rỗng / 0 / '{}' cho JSON)."""
    row = {"created_at": NOW, "updated_at": NOW, **vals}
    for _cid, name, type_, notnull, default, pk in conn.execute(text(f"PRAGMA table_info({table})")):
        if pk or name in row or not notnull or default is not None:
            continue
        row[name] = 0 if "INT" in type_.upper() or "BOOL" in type_.upper() else ("{}" if type_.upper() == "TEXT" else "")
    cols = ", ".join(row)
    return conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({', '.join(':' + c for c in row)}) RETURNING id"),
                        row).scalar()


def _project(conn, key: str) -> int:
    return _insert(conn, "project", key=key, name="P")


def _sequence(conn, pid: int, key: str) -> int:
    return _insert(conn, "sequence", project_id=pid, key=key, title="T")


def test_upgrade_dedupes_case_variants(tmp_path):
    database = Database(tmp_path / "old.db")
    _upgrade(database, "0006")
    with database.engine.begin() as conn:
        p1 = _project(conn, "Demo")
        p2 = _project(conn, "demo")
        _sequence(conn, p1, "ep1")
        s2 = _sequence(conn, p1, "EP1")
        for path in ("SKILL.md", "skill.md"):
            conn.execute(text("INSERT INTO skill_file (created_at, updated_at, path, body) VALUES (:n, :n, :p, '')"),
                         {"n": NOW, "p": path})
    _upgrade(database, "head")
    with database.engine.begin() as conn:
        keys = dict(conn.execute(text("SELECT id, key FROM project")).all())
        assert keys[p1] == "Demo" and keys[p2] == f"demo-{p2}"
        assert conn.execute(text("SELECT key FROM sequence WHERE id = :i"), {"i": s2}).scalar() == f"EP1-{s2}"
        live = conn.execute(text("SELECT path FROM skill_file WHERE deleted_at IS NULL")).scalars().all()
        assert live == ["skill.md"]
    with pytest.raises(IntegrityError), database.engine.begin() as conn:
        _project(conn, "DEMO")
    database.dispose()
