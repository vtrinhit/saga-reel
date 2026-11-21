"""Task S — migration 0005: unique một phần (chưa xoá), unique dòng toàn cục, cột mới chuyển từ JSON, render_chunk
unique, key cho thoại/timeline/âm thanh. Nâng cấp từ 0004 có dữ liệu."""

from __future__ import annotations

import json

import pytest
from alembic import command
from sagareel.db.base import utcnow
from sagareel.db.models import (
    AssetKind,
    Clip,
    DialogueLine,
    Preset,
    Project,
    Sequence,
    Sound,
    TimelineItem,
)
from sagareel.db.session import Database, alembic_config
from sqlalchemy import select, text
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


def _ins(conn, table: str, **values) -> int:
    values.setdefault("created_at", NOW)
    cols = ", ".join(f"\"{k}\"" for k in values)
    marks = ", ".join(f":{k}" for k in values)
    return conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({marks}) RETURNING id"), values).scalar()


def _tracked(**v):
    return {"updated_at": NOW, **v}


@pytest.fixture
def old_db(tmp_path):
    database = Database(tmp_path / "old.db")
    _upgrade(database, "0004")
    ids: dict[str, int] = {}
    with database.engine.begin() as conn:
        pid = ids["project"] = _ins(conn, "project", **_tracked(
            key="demo", name="Demo", description="", genre_key="series", aspect="9:16", resolution="1080p", fps=30,
            language="vi", languages='["vi"]', target_platforms="[]", style_block="", negative_block="",
            settings="{}", budget="{}"))
        sid = ids["sequence"] = _ins(conn, "sequence", **_tracked(
            project_id=pid, key="ep1", number=1, title="Tập 1", synopsis="", script="", status="draft",
            workflow_key="", gates="{}", pipeline="{}", target_platforms="[]", settings="{}"))
        clip_cols = {"sequence_id": sid, "rank": "a0", "title": "", "image_prompt": "", "image_prompt_src": "{}",
                         "motion_prompt": "", "motion_prompt_src": "{}", "prompt_mode": "synced", "assets": "[]",
                         "extra_ref_media_ids": "[]", "engine": "ai_video", "motion_preset": "", "duration_ms": 6000,
                         "link": "cut", "transition": "{}", "trim_in_ms": 0, "trim_out_ms": 0, "speed": 1.0, "grok_audio": "ambient",
                         "subtitle_mode": "auto", "flags": "{}", "review": "pending", "notes": "", "shot_size": "", "angle": "",
                         "camera_move": "", "lens": "", "composition": "", "eyeline": "", "screen_dir": "", "first_frame_desc": "",
                         "last_frame_desc": "", "sfx_desc": "", "beats": "[]"}
        ids["clip"] = _ins(conn, "clip", **_tracked(
            **clip_cols, key="c0001",
            custom=json.dumps({"axis_side": "left", "cut_in": "match_action", "cut_out": "cutaway", "x": 1}),
            settings=json.dumps({"connection": "maivideo", "model": "m-2", "image.resolution": "1k"})))
        ids["deleted_clip"] = _ins(conn, "clip", **_tracked(**{**clip_cols, "rank": "a1"}, key="c0002", custom="{}",
                                                            settings="{}", deleted_at=NOW))
        ids["take"] = _ins(conn, "take", owner_type="clip", owner_id=ids["clip"], part="image", view="",
                           status="failed", inputs=json.dumps({"prompt": "p", "seed": 42, "error_code": "boom",
                                                               "verdict_reason": "mờ"}),
                           cost=0.0, verdict="regenerate", notes="", auto_score="{}", lang="", updated_at=NOW)
        ids["line"] = _ins(conn, "dialogue_line", **_tracked(
            clip_id=ids["clip"], rank="a0", text="Xin chào", emotion="", source="tts", voice_override="{}",
            offset_ms=0, subtitle=1, translations="{}"))
        ids["sound"] = _ins(conn, "sound", **_tracked(project_id=None, media_id=1, kind="music", title="Nhạc",
                                                      tags="[]", source="", license="", attribution=""))
        ids["item"] = _ins(conn, "timeline_item", **_tracked(sequence_id=sid, track="music", offset_ms=0,
                                                             params="{}"))
        for _ in range(2):  # khối trùng (sequence, variant, fingerprint)
            ids["chunk"] = _ins(conn, "render_chunk", sequence_id=sid, variant="final", index=0, fingerprint="fp",
                                media_id=1, duration_ms=1000)
        for _ in range(2):  # preset toàn cục trùng (NULL project không bị uq 0004 chặn)
            ids["preset"] = _ins(conn, "preset", **_tracked(project_id=None, kind="transition", key="dup", name="Dup",
                                                            data="{}", builtin=0, builtin_hash=""))
    yield database, ids
    database.dispose()


def test_upgrade_from_0004_moves_json_into_columns(old_db):
    database, ids = old_db
    database.migrate()
    with database.engine.connect() as conn:
        row = conn.execute(text("SELECT connection, model, axis_side, cut_in, cut_out, custom, settings FROM clip "
                                "WHERE id = :i"), {"i": ids["clip"]}).one()
        assert row[:5] == ("maivideo", "m-2", "left", "match_action", "cutaway")
        assert json.loads(row[5]) == {"x": 1} and json.loads(row[6]) == {"image.resolution": "1k"}
        t = conn.execute(text("SELECT seed, error_code, verdict_reason, inputs FROM take WHERE id = :i"),
                         {"i": ids["take"]}).one()
        assert t[:3] == (42, "boom", "mờ") and json.loads(t[3]) == {"prompt": "p"}
        assert conn.execute(text("SELECT key FROM dialogue_line")).scalar() == f"l{ids['line']}"
        assert conn.execute(text("SELECT key FROM sound")).scalar() == f"s{ids['sound']}"
        assert conn.execute(text("SELECT key FROM timeline_item")).scalar() == f"t{ids['item']}"
        assert conn.execute(text("SELECT id FROM render_chunk")).scalars().all() == [ids["chunk"]]  # giữ mới nhất
        presets = conn.execute(text("SELECT id, deleted_at FROM preset WHERE key = 'dup' ORDER BY id")).all()
        assert presets[-1] == (ids["preset"], None) and presets[0][1] is not None  # cũ hơn bị xoá mềm


def test_soft_deleted_key_can_be_reused_and_globals_unique(old_db):
    database, ids = old_db
    database.migrate()
    with database.session() as s:  # c0002 đã xoá mềm → tạo lại cùng key được
        s.add(Clip(sequence_id=ids["sequence"], key="c0002", rank="b0"))
    with pytest.raises(IntegrityError), database.session() as s:  # nhưng không trùng key còn sống
        s.add(Clip(sequence_id=ids["sequence"], key="c0001", rank="b1"))
    with pytest.raises(IntegrityError), database.session() as s:  # loại tài sản toàn cục trùng key
        s.add(AssetKind(project_id=None, key="character", label="Nhân vật 2"))
    with pytest.raises(IntegrityError), database.session() as s:
        s.add(Preset(project_id=None, kind="transition", key="dup", name="x"))
    with database.session() as s:  # project khác vẫn dùng cùng key; xoá mềm project rồi tạo lại cùng key
        p = s.scalars(select(Project).where(Project.key == "demo")).one()
        p.deleted_at = utcnow()
        s.flush()
        s.add(Project(key="demo", name="Demo mới"))
        s.add(AssetKind(project_id=ids["project"], key="character", label="Riêng"))
    with database.session() as s:  # key tự sinh cho dòng mới
        seq = s.get(Sequence, ids["sequence"])
        line = DialogueLine(clip_id=ids["clip"], rank="b0", text="Mới")
        item = TimelineItem(sequence_id=seq.id, track="sfx")
        snd = Sound(project_id=None, media_id=1, kind="sfx", title="Bụp")
        s.add_all([line, item, snd])
        s.flush()
        assert line.key.startswith("l-") and item.key.startswith("t-") and snd.key.startswith("s-")


def test_downgrade_restores_json(old_db):
    database, ids = old_db
    database.migrate()
    cfg = alembic_config(database.url)
    with database.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0004")
        conn.commit()
        custom = json.loads(conn.execute(text("SELECT custom FROM clip WHERE id = :i"), {"i": ids["clip"]}).scalar())
        inputs = json.loads(conn.execute(text("SELECT inputs FROM take")).scalar())
    assert custom["axis_side"] == "left" and inputs["seed"] == 42 and inputs["error_code"] == "boom"


def test_services_allow_reusing_soft_deleted_keys(owner_ctx, project):
    from sagareel.core.errors import AppError
    from sagareel.services import clips, history, library, projects, sequences

    seq = sequences.create(owner_ctx, project, "Tập 1")["id"]
    a = clips.create(owner_ctx, seq, {"key": "mo-dau"})
    clips.delete(owner_ctx, clips.delete_preview(owner_ctx, [a["id"]])["confirm_token"])
    b = clips.create(owner_ctx, seq, {"key": "mo-dau"})  # mã của clip đã xoá mềm dùng lại được
    assert b["key"] == "mo-dau" and b["id"] != a["id"]
    with pytest.raises(AppError) as ei:  # hoàn tác xoá clip cũ → trùng mã đang dùng → 409 rõ ràng
        history.undo(owner_ctx, _delete_change_set(owner_ctx, "clip.delete"))
    assert ei.value.code == "key_taken"
    assert clips.create(owner_ctx, seq, {})["key"] not in {"mo-dau"}

    asset = library.asset_create(owner_ctx, project, "character", "Bé An", "AN", "", None)
    library.asset_delete(owner_ctx, library.asset_delete_preview(owner_ctx, asset["id"])["confirm_token"]) \
        if hasattr(library, "asset_delete_preview") else None
    p2 = projects.create(owner_ctx, name="Khác", key="khac")
    projects.delete(owner_ctx, projects.delete_preview(owner_ctx, p2["id"])["confirm_token"])
    assert projects.create(owner_ctx, name="Khác lại", key="khac")["key"] == "khac"


def _delete_change_set(ctx, op_name: str) -> int:
    from sagareel.db.models import ChangeSet

    with ctx.services.db.session() as s:
        return s.scalars(select(ChangeSet.id).where(ChangeSet.op_name == op_name).order_by(ChangeSet.id)).first()
