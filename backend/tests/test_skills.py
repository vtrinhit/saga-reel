"""skill.list/get/update: bảng `skill_file` (lịch sử) phản chiếu ra `.claude/skills/sagareel-operator/` (root tạm)."""

from __future__ import annotations

import dataclasses
import re
import sys
from pathlib import Path

import pytest
from sagareel.container import Services
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.db.session import Database
from sagareel.services import history, skills

REPO = Path(__file__).resolve().parents[2]
REAL_SKILL = REPO / skills.SKILL_REL


@pytest.fixture
def sctx(settings, tmp_path):
    st = dataclasses.replace(settings, root=tmp_path / "root")
    d = skills.skill_dir(st)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: x\n---\nGốc\n", encoding="utf-8")
    database = Database(tmp_path / "skill.db")
    database.migrate()
    try:
        yield OpContext(services=Services(settings=st, boot_id="s" * 32, db=database), actor="user:owner",
                        role="owner"), d
    finally:
        database.dispose()


def test_list_get_update_mirror_and_undo(sctx):
    ctx, d = sctx
    assert [i["path"] for i in skills.list_files(ctx)["items"]] == ["SKILL.md"]
    assert skills.get(ctx, "SKILL.md")["body"].endswith("Gốc\n")
    out = skills.update(ctx, "playbooks/montage.md", "# Montage\n")
    assert out["change_set_id"] and (d / "playbooks" / "montage.md").read_text(encoding="utf-8") == "# Montage\n"
    skills.update(ctx, "SKILL.md", "Mới\n")
    assert (d / "SKILL.md").read_text(encoding="utf-8") == "Mới\n"
    # Hoàn tác trong DB → lần đọc sau ghi lại file theo DB.
    history.undo(ctx)
    assert skills.get(ctx, "SKILL.md")["body"].endswith("Gốc\n")
    assert (d / "SKILL.md").read_text(encoding="utf-8").endswith("Gốc\n")
    # File sửa ngoài app → nhập vào DB.
    (d / "SKILL.md").write_text("Sửa bằng editor\n", encoding="utf-8")
    assert skills.get(ctx, "SKILL.md")["body"] == "Sửa bằng editor\n"


@pytest.mark.parametrize("bad", ["../x.md", "/etc/x.md", "a/../../b.md", "x.txt", ".hidden/a.md", "C:/x.md"])
def test_path_validation(sctx, bad):
    ctx, _ = sctx
    with pytest.raises(AppError) as e:
        skills.update(ctx, bad, "x")
    assert e.value.code == "skill_path_invalid"


def test_get_missing(sctx):
    ctx, _ = sctx
    with pytest.raises(AppError) as e:
        skills.get(ctx, "nope.md")
    assert e.value.code == "skill_not_found"


def test_operator_skill_files_present_and_reference_real_tools():
    """SKILL.md có frontmatter; mọi `sr_*` nhắc tới trong skill tồn tại trong bộ tool MCP sinh từ catalog sống."""
    from sagareel import ops as ops_pkg
    from sagareel.core import ops

    mcp_dir = REPO / "mcp_server"
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    from sagareel_mcp import tools as mcp_tools

    ops_pkg.load_all()
    tools = mcp_tools.build_tools(ops.catalog())
    files = sorted(REAL_SKILL.rglob("*.md"))
    names = {f.relative_to(REAL_SKILL).as_posix() for f in files}
    assert {"SKILL.md", "playbooks/montage.md", "playbooks/series.md", "playbooks/long-serial.md",
            "playbooks/clone.md", "playbooks/dubbing.md", "playbooks/provider-errors.md"} <= names
    head = (REAL_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert re.match(r"^---\nname: sagareel-operator\ndescription: .+\n", head)
    unknown = set()
    for f in files:
        for name in re.findall(r"\bsr_[a-z0-9_]+", f.read_text(encoding="utf-8")):
            if name not in tools:
                unknown.add(f"{f.name}:{name}")
        for opname in re.findall(r"`([a-z_]+\.[a-z_]+)`", f.read_text(encoding="utf-8")):
            if "." in opname and not opname.endswith((".md", ".yaml", ".json")) and opname not in ops.REGISTRY:
                unknown.add(f"{f.name}:{opname}")
    assert unknown == set()
