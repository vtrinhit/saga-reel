"""Rà soát bảo mật — file skill: ghi file tuỳ ý qua entity.patch `skill_file.path` (#1) và đồng bộ hỏng sau hoàn tác
tạo file (#5)."""

from __future__ import annotations

import dataclasses
import logging

import pytest
from sagareel.container import Services
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.db.models import SkillFile
from sagareel.db.session import Database
from sagareel.services import entities, history, skills
from sqlalchemy import select, text


@pytest.fixture
def sctx(settings, tmp_path):
    st = dataclasses.replace(settings, root=tmp_path / "root")
    d = skills.skill_dir(st)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Gốc\n", encoding="utf-8")
    database = Database(tmp_path / "skill.db")
    database.migrate()
    try:
        yield OpContext(services=Services(settings=st, boot_id="s" * 32, db=database), actor="user:owner",
                        role="owner"), d, tmp_path
    finally:
        database.dispose()


def _row_id(ctx, path: str) -> int:
    with ctx.services.db.session() as s:
        return s.scalar(select(SkillFile.id).where(SkillFile.path == path))


def test_entity_patch_cannot_change_skill_path(sctx):
    ctx, _d, _tmp = sctx
    skills.sync(ctx)
    rid = _row_id(ctx, "SKILL.md")
    with pytest.raises(AppError) as e:
        entities.patch(ctx, "skill_file", rid, {"path": "../../evil.md"})
    assert e.value.code == "readonly_field"


def test_sync_skips_poisoned_db_path(sctx, caplog):
    """Đường dẫn xấu lọt vào DB (vd. hoàn tác bản ghi cũ, sửa DB tay) → sync bỏ qua + cảnh báo, không ghi ra ngoài."""
    ctx, _d, tmp = sctx
    skills.sync(ctx)
    rid = _row_id(ctx, "SKILL.md")
    with ctx.services.db.session() as s:
        s.execute(text("UPDATE skill_file SET path = '../../../escaped.md', body = 'pwn' WHERE id = :i"), {"i": rid})
    with caplog.at_level(logging.WARNING, logger="sagareel.skills"):
        skills.sync(ctx)
    assert not (tmp / "escaped.md").exists()
    assert not any(p.name == "escaped.md" for p in tmp.rglob("*.md"))
    assert any("không hợp lệ" in r.getMessage() for r in caplog.records)


def test_write_disk_rechecks_path(sctx):
    _ctx, d, _tmp = sctx
    for bad in ("../x.md", "a/../../x.md", "/abs.md", "x.txt"):
        with pytest.raises(AppError):
            skills._write_disk(d, bad, "x")


def test_sync_after_undo_of_create_revives_row(sctx):
    ctx, _d, _tmp = sctx
    skills.sync(ctx)
    skills.update(ctx, "playbooks/new.md", "# Mới\n")
    history.undo(ctx)  # bản ghi bị xoá mềm, file vẫn còn trên đĩa
    out = skills.sync_report(ctx)  # trước đây: UNIQUE constraint failed
    assert "playbooks/new.md" in [i["path"] for i in out["items"]]
    with ctx.services.db.session() as s:
        rows = s.scalars(select(SkillFile).where(SkillFile.path == "playbooks/new.md")).all()
    assert len(rows) == 1 and rows[0].deleted_at is None


def test_update_matches_path_case_insensitively(sctx):
    """Windows: SKILL.md và skill.md là cùng một file → cùng một dòng."""
    ctx, _d, _tmp = sctx
    skills.sync(ctx)
    skills.update(ctx, "skill.md", "Khác\n")
    with ctx.services.db.session() as s:
        live = s.scalars(select(SkillFile).where(SkillFile.deleted_at.is_(None))).all()
    assert len(live) == 1


def test_db_rejects_case_duplicate_live_paths(sctx):
    ctx, _d, _tmp = sctx
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), ctx.services.db.session() as s:
        s.add(SkillFile(path="A.md", body=""))
        s.add(SkillFile(path="a.md", body=""))
        s.flush()
