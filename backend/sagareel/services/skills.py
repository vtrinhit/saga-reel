"""File skill vận hành (spec §17): bảng `skill_file` (lịch sử qua UoW) phản chiếu ra `.claude/skills/sagareel-operator/**`.

Đồng bộ (`sync`, chạy ở mọi op skill):
- File trên đĩa chưa có dòng → nhập vào DB (change set `skill.sync`).
- Dòng có, file khác: nếu file vẫn đúng bản app ghi lần cuối (hash lưu ở `system_state`) → DB đổi (vd. hoàn tác) →
  ghi lại file; ngược lại file bị sửa ngoài app (git pull, editor) → nhập file vào DB (có lịch sử).
- `skill.update` ghi DB rồi ghi file. Chỉ `.md` nằm trong thư mục skill; không `..`, không đường dẫn tuyệt đối.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound
from sagareel.db.models import SkillFile
from sagareel.services import entities
from sagareel.services import system_state as ss

SKILL_REL = Path(".claude") / "skills" / "sagareel-operator"
MAX_BODY = 256 * 1024
MIRROR_PREFIX = "skill_mirror:"
_PATH_RE = re.compile(r"^[A-Za-z0-9_\-./]+\.md$")


def skill_dir(settings: Any) -> Path:
    return Path(settings.root) / SKILL_REL


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_path(path: str) -> str:
    p = (path or "").replace("\\", "/").strip()
    pp = PurePosixPath(p)
    if (not _PATH_RE.match(p) or pp.is_absolute() or ".." in pp.parts or any(part.startswith(".") for part in pp.parts)
            or len(p) > 200):
        raise AppError("skill_path_invalid", f"Đường dẫn skill '{path}' không hợp lệ.",
                       hint="Dùng đường dẫn tương đối kết thúc bằng .md, vd. SKILL.md hoặc playbooks/montage.md.",
                       status=422)
    return str(pp)


def _read_disk(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if root.is_dir():
        for f in sorted(root.rglob("*.md")):
            rel = f.relative_to(root).as_posix()
            try:
                out[check_path(rel)] = f.read_text(encoding="utf-8").replace("\r\n", "\n")
            except AppError:
                continue
    return out


def _write_disk(root: Path, path: str, body: str) -> None:
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8", newline="\n")


def sync(ctx: OpContext) -> None:
    root = skill_dir(ctx.services.settings)
    disk = _read_disk(root)
    to_write: list[tuple[str, str]] = []
    u = entities.uow(ctx, "Đồng bộ file skill", "skill.sync")
    with u as s:
        rows = {r.path: r for r in s.scalars(select(SkillFile).where(SkillFile.deleted_at.is_(None)))}
        for path, body in disk.items():
            row = rows.get(path)
            if row is None:
                s.add(SkillFile(path=path, body=body))
                ss.set(s, MIRROR_PREFIX + path, _hash(body))
            elif row.body != body:
                if ss.get(s, MIRROR_PREFIX + path) == _hash(body):
                    to_write.append((path, row.body))  # DB đổi (hoàn tác…) → file theo DB
                    ss.set(s, MIRROR_PREFIX + path, _hash(row.body))
                else:
                    row.body = body  # file sửa ngoài app → nhập vào DB
                    ss.set(s, MIRROR_PREFIX + path, _hash(body))
        for path, row in rows.items():
            if path not in disk:
                to_write.append((path, row.body))
                ss.set(s, MIRROR_PREFIX + path, _hash(row.body))
    for path, body in to_write:
        _write_disk(root, path, body)


def _out(row: SkillFile, *, body: bool = True) -> dict[str, Any]:
    d = {"path": row.path, "size": len(row.body.encode("utf-8")), "updated_at": row.updated_at}
    if body:
        d["body"] = row.body
    return d


def list_files(ctx: OpContext) -> dict[str, Any]:
    sync(ctx)
    with ctx.services.db.session() as s:
        rows = s.scalars(select(SkillFile).where(SkillFile.deleted_at.is_(None)).order_by(SkillFile.path)).all()
        return {"items": [_out(r, body=False) for r in rows], "dir": SKILL_REL.as_posix()}


def get(ctx: OpContext, path: str) -> dict[str, Any]:
    path = check_path(path)
    sync(ctx)
    with ctx.services.db.session() as s:
        row = s.scalar(select(SkillFile).where(SkillFile.path == path, SkillFile.deleted_at.is_(None)))
        if row is None:
            raise NotFound(f"Không có file skill '{path}'.", code="skill_not_found", hint="Xem skill.list.")
        return _out(row)


def update(ctx: OpContext, path: str, body: str, expected_updated_at=None) -> dict[str, Any]:
    path = check_path(path)
    body = body.replace("\r\n", "\n")
    if len(body.encode("utf-8")) > MAX_BODY:
        raise AppError("skill_too_large", "File skill quá lớn (tối đa 256 KB).", status=413)
    sync(ctx)
    u = entities.uow(ctx, f"Sửa file skill {path}", "skill.update")
    with u as s:
        row = s.scalar(select(SkillFile).where(SkillFile.path == path))
        if row is None:
            row = SkillFile(path=path, body=body)
            s.add(row)
        else:
            entities.check_fresh("skill_file", row, expected_updated_at)
            row.deleted_at = None
            if row.body != body:
                row.body = body
        ss.set(s, MIRROR_PREFIX + path, _hash(body))
        s.flush()
        out = _out(row)
    _write_disk(skill_dir(ctx.services.settings), path, body)
    out["change_set_id"] = u.change_set_id
    return out
