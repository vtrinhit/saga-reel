"""W0: schema M1–M7 (migration 0004) — upgrade từ rỗng và từ 0003 có dữ liệu, mọi bảng tồn tại, FTS5, seed asset_kind."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import inspect, select, text

from sagareel.db.base import Base
from sagareel.db.models import AssetKind, Clip, Project, Sequence, Take
from sagareel.db.session import Database, alembic_config

M1_M7_TABLES = {
    "project", "group_node", "sequence", "scene", "asset_kind", "asset", "asset_variant", "clip", "dialogue_line",
    "take", "voice_profile", "timeline_item", "sound", "preset", "bible_item", "bible_evidence", "render",
    "render_chunk", "export_package", "upload_session", "channel_study", "skill_file",
}
TRACKED = {
    "project", "group_node", "sequence", "scene", "asset_kind", "asset", "asset_variant", "clip", "dialogue_line",
    "voice_profile", "timeline_item", "sound", "preset", "bible_item", "channel_study", "skill_file",
}


def _upgrade(database: Database, rev: str) -> None:
    """Như `Database.migrate()` nhưng tới revision tuỳ chọn."""
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


def test_all_tables_exist_after_upgrade_from_empty(db):
    tables = set(inspect(db.engine).get_table_names())
    assert M1_M7_TABLES <= tables
    assert "bible_fts" in tables
    assert M1_M7_TABLES <= set(Base.metadata.tables)


def test_tracked_flags_match_contract():
    for name in M1_M7_TABLES:
        mapper_cls = next(m.class_ for m in Base.registry.mappers if m.local_table.name == name)
        assert bool(getattr(mapper_cls, "__tracked__", False)) == (name in TRACKED), name
        cols = set(Base.metadata.tables[name].c.keys())
        if name in TRACKED:
            assert {"id", "created_at", "updated_at", "deleted_at"} <= cols
    assert "updated_at" in Base.metadata.tables["take"].c and "deleted_at" not in Base.metadata.tables["take"].c


def test_every_fk_column_is_indexed():
    for name in M1_M7_TABLES:
        table = Base.metadata.tables[name]
        indexed = {next(c.name for c in ix.columns) for ix in table.indexes}
        for fk in table.foreign_keys:
            assert fk.parent.name in indexed, f"{name}.{fk.parent.name} thiếu index"


def test_upgrade_from_0003_keeps_existing_data(tmp_path):
    database = Database(tmp_path / "old.db")
    try:
        _upgrade(database, "0003")
        with database.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO template (key, kind, body, locked, builtin_hash, created_at, updated_at) "
                "VALUES ('cũ', 'system', 'Nội dung có dấu', 0, '', '2026-11-19 00:00:00', '2026-11-19 00:00:00')"
            ))
            conn.execute(text(
                "INSERT INTO api_token (name, token_hash, role, local_only, created_at) "
                "VALUES ('agent', 'h', 'owner', 0, '2026-11-19 00:00:00')"
            ))
        database.migrate()
        with database.engine.connect() as conn:
            assert conn.execute(text("SELECT body FROM template WHERE key='cũ'")).scalar() == "Nội dung có dấu"
            assert conn.execute(text("SELECT count(*) FROM api_token")).scalar() == 1
        assert M1_M7_TABLES <= set(inspect(database.engine).get_table_names())
        with database.session() as s:
            s.add(Project(key="p", name="Dự án"))
        database.migrate()  # idempotent
    finally:
        database.dispose()


def test_global_asset_kinds_seeded(db):
    with db.session() as s:
        rows = s.scalars(select(AssetKind).where(AssetKind.project_id.is_(None)).order_by(AssetKind.key)).all()
        assert [r.key for r in rows] == ["character", "location", "prop"]
        assert all(r.label and r.deleted_at is None for r in rows)
        labels = {r.key: r.label for r in rows}
        assert labels["character"] == "Nhân vật"


def test_fts5_insert_and_search(db):
    with db.engine.begin() as conn:
        conn.execute(text("INSERT INTO bible_fts(rowid, summary, quote) VALUES (7, :s, :q)"),
                     {"s": "An là cô gái bán hoa ở chợ Đồng Xuân", "q": "“Hoa này tươi lắm”"})
        conn.execute(text("INSERT INTO bible_fts(rowid, summary, quote) VALUES (8, 'Bình là thợ rèn', '')"))
    with db.engine.connect() as conn:
        hits = conn.execute(text("SELECT rowid FROM bible_fts WHERE bible_fts MATCH :q"), {"q": "hoa"}).scalars().all()
        assert hits == [7]
        # Tìm không dấu vẫn khớp (remove_diacritics). Lưu ý: "đ" không phải dấu nên "dong" không khớp "Đồng" —
        # service tìm kiếm (M5) tự đổi đ→d ở cả hai phía nếu cần.
        assert conn.execute(text("SELECT rowid FROM bible_fts WHERE bible_fts MATCH 'xuan'")).scalars().all() == [7]
    with db.engine.begin() as conn:  # content-less nhưng xoá được theo rowid (service tự đồng bộ)
        conn.execute(text("DELETE FROM bible_fts WHERE rowid = 7"))
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT rowid FROM bible_fts WHERE bible_fts MATCH 'hoa'")).scalars().all() == []


def test_unique_key_within_parent(db):
    from sqlalchemy.exc import IntegrityError

    with db.session() as s:
        p1, p2 = Project(key="a", name="A"), Project(key="b", name="B")
        s.add_all([p1, p2])
        s.flush()
        s.add_all([Sequence(project_id=p1.id, key="ep1", title="Tập 1"),
                   Sequence(project_id=p2.id, key="ep1", title="Tập 1")])
    with pytest.raises(IntegrityError), db.session() as s:
        pid = s.scalar(select(Project.id).where(Project.key == "a"))
        s.add(Sequence(project_id=pid, key="ep1", title="trùng"))


def test_defaults_and_json(db):
    with db.session() as s:
        p = Project(key="x", name="X")
        s.add(p)
        s.flush()
        seq = Sequence(project_id=p.id, key="s", title="T")
        s.add(seq)
        s.flush()
        s.add(Clip(sequence_id=seq.id, key="c1", rank="a0", assets=[{"asset_id": 1, "role": "appearance"}]))
        s.add(Take(owner_type="clip", owner_id=1, part="image"))
    with db.session() as s:
        p = s.scalar(select(Project))
        assert (p.aspect, p.resolution, p.fps, p.language, p.languages, p.genre_key) == (
            "9:16", "1080p", 30, "vi", ["vi"], "series")
        c = s.scalar(select(Clip))
        assert (c.prompt_mode, c.engine, c.duration_ms, c.link, c.speed, c.grok_audio, c.review) == (
            "synced", "ai_video", 6000, "cut", 1.0, "ambient", "pending")
        assert c.assets == [{"asset_id": 1, "role": "appearance"}]
        t = s.scalar(select(Take))
        assert t.status == "queued" and t.cost == 0 and t.updated_at is not None


def test_handlers_load_all_idempotent():
    from sagareel import handlers

    handlers.load_all()
    handlers.load_all()


def test_compile_contract_stubs():
    from sagareel.compile import contracts

    cp = contracts.CompiledPrompt(text="x", refs=[contracts.RefItem(1, "appearance", "Ảnh 1: ngoại hình của An", "anchor")])
    assert cp.warnings == [] and cp.provider_caps == {}
    # Task A đã thay stub bằng compiler thật (test ở test_compiler.py).
