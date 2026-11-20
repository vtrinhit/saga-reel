"""Bỏ tính năng ngân sách: không còn op/khoá cài đặt/cột `project.budget`; ước tính (dry run) vẫn đủ số lượt nhưng
không còn khối `budget`; spec cũ còn `budget` vẫn đọc được; migration 0009 dọn khoá `budget.*` và cột."""

from __future__ import annotations

import json

import anyio
from alembic import command
from sagareel import ops
from sagareel.core.ops import REGISTRY, invoke
from sagareel.core.resolver import default_resolver
from sagareel.db.session import Database, alembic_config
from sagareel.services import sequences
from sagareel.services.spec import models as m
from sqlalchemy import text

ops.load_all()

NOW = "2026-11-20 00:00:00.000000"


def _inv(name, payload, ctx):
    return anyio.run(invoke, name, payload, ctx)


def test_no_budget_ops_or_settings():
    names = set(REGISTRY)
    assert not any(n.startswith("budget.") for n in names)
    assert not any(k.startswith("budget.") for k in default_resolver().schema)


def test_dry_run_steps_and_unsplit_estimate(owner_ctx, project):
    sid = sequences.create(owner_ctx, project, "Tập 1", script="")["id"]
    plan = _inv("pipeline.run", {"sequence_ids": [sid], "dry_run": True}, owner_ctx)
    keys = [x["key"] for x in plan["steps"]]
    assert keys == plan["step_keys"] and "generate_images" in keys
    img = next(x for x in plan["steps"] if x["key"] == "generate_images")
    assert img["units"]["images"] > 0  # tập chưa chia clip → ước tính theo thể loại
    assert plan["sequences"][0]["clips_estimated"] is True and plan["estimate"]["clips_estimated"] is True
    assert plan["estimate"]["images"] == img["units"]["images"] and "note" in plan
    render = [x for x in plan["steps"] if x["key"].startswith("render")]
    assert all(x["units"]["renders"] == 1 for x in render)
    assert "budget" not in plan["estimate"]


def test_legacy_spec_budget_block_is_ignored():
    block = m.ProjectBlock(key="demo", name="Demo", budget={"images_total": 3})
    assert "budget" not in block.model_dump()


def _upgrade(database: Database, rev: str, *, down: bool = False) -> None:
    cfg = alembic_config(database.url)
    with database.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            cfg.attributes["connection"] = conn
            (command.downgrade if down else command.upgrade)(cfg, rev)
            conn.commit()
        finally:
            conn.rollback()
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}


def test_migration_0009_drops_budget_column_and_keys(tmp_path):
    database = Database(tmp_path / "old.db")
    _upgrade(database, "0008")
    with database.engine.begin() as conn:
        cols = _cols(conn, "global_settings")
        conn.execute(text(f"INSERT INTO global_settings (id, data, {', '.join(c for c in cols if c.endswith('_at'))}) "
                          f"VALUES (1, :d, {', '.join(':n' for c in cols if c.endswith('_at'))})"),
                     {"n": NOW, "d": json.dumps({"budget.images_total": 5, "video.duration": 10})})
        conn.execute(text(
            "INSERT INTO project (created_at, updated_at, key, name, description, genre_key, aspect, resolution, fps, "
            "language, languages, target_platforms, style_block, negative_block, settings, budget) VALUES (:n, :n, "
            "'demo', 'Demo', '', 'series', '9:16', '1080p', 30, 'vi', '[\"vi\"]', '[]', '', '', :s, '{\"x\": 1}')"),
            {"n": NOW, "s": json.dumps({"budget.enabled": False, "image.model": "m"})})
    _upgrade(database, "head")
    with database.engine.begin() as conn:
        assert "budget" not in _cols(conn, "project")
        assert json.loads(conn.execute(text("SELECT data FROM global_settings")).scalar()) == {"video.duration": 10}
        assert json.loads(conn.execute(text("SELECT settings FROM project")).scalar()) == {"image.model": "m"}
        assert "ux_project_key" in {r[1] for r in conn.execute(text("PRAGMA index_list(project)"))}
        assert "NOCASE" in conn.execute(text("SELECT sql FROM sqlite_master WHERE name='project'")).scalar()
        assert "spend" in {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    _upgrade(database, "0008", down=True)
    with database.engine.begin() as conn:
        assert "budget" in _cols(conn, "project")
        assert conn.execute(text("SELECT budget FROM project")).scalar() == "{}"
