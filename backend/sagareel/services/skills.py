"""File skill vận hành (spec §17): bảng `skill_file` (lịch sử qua UoW) phản chiếu ra `.claude/skills/sagareel-operator/**`.

Đồng bộ (`sync`): lúc khởi động app (`sync_on_startup`), trước `skill.update` và op `skill.sync`; `skill.list/get` chỉ đọc:
- File trên đĩa chưa có dòng → nhập vào DB (change set `skill.sync`).
- Dòng có, file khác: nếu file vẫn đúng bản app ghi lần cuối (hash lưu ở `system_state`) → DB đổi (vd. hoàn tác) →
  ghi lại file; ngược lại file bị sửa ngoài app (git pull, editor) → nhập file vào DB (có lịch sử).
- `skill.update` ghi DB rồi ghi file. Chỉ `.md` nằm trong thư mục skill; không `..`, không đường dẫn tuyệt đối.
"""

from __future__ import annotations

import hashlib
import logging
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
logger = logging.getLogger("sagareel.skills")


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
    """Ghi một file skill. Kiểm lại đường dẫn ngay tại chỗ ghi (dòng DB có thể tới từ hoàn tác/sửa tay, không chỉ từ
    `check_path` ở đầu vào) và chặn mọi thứ giải ra ngoài thư mục skill (symlink, `..`)."""
    path = check_path(path)
    base = root.resolve()
    f = (root / path).resolve()
    if not f.is_relative_to(base) or f == base:
        raise AppError("skill_path_invalid", f"Đường dẫn skill '{path}' nằm ngoài thư mục skill.", status=422)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8", newline="\n")


def sync(ctx: OpContext) -> None:
    root = skill_dir(ctx.services.settings)
    disk = _read_disk(root)
    to_write: list[tuple[str, str]] = []
    u = entities.uow(ctx, "Đồng bộ file skill", "skill.sync")
    with u as s:
        rows: dict[str, SkillFile] = {}
        for r in s.scalars(select(SkillFile).where(SkillFile.deleted_at.is_(None))):
            try:
                rows[check_path(r.path).lower()] = r
            except AppError:
                logger.warning("Bỏ qua file skill #%s: đường dẫn không hợp lệ.", r.id)
        disk_rows = {path.lower(): path for path in disk}
        for path, body in disk.items():
            row = rows.get(path.lower())
            if row is None:
                row = _find(s, path, alive=False)  # vd. hoàn tác lệnh tạo file: dòng đã xoá mềm → hồi sinh
                if row is not None:
                    row.deleted_at = None
                    row.path = path
                    row.body = body
                else:
                    s.add(SkillFile(path=path, body=body))
                ss.set(s, MIRROR_PREFIX + path, _hash(body))
            elif row.body != body:
                if ss.get(s, MIRROR_PREFIX + path) == _hash(body):
                    to_write.append((path, row.body))  # DB đổi (hoàn tác…) → file theo DB
                    ss.set(s, MIRROR_PREFIX + path, _hash(row.body))
                else:
                    row.body = body  # file sửa ngoài app → nhập vào DB
                    ss.set(s, MIRROR_PREFIX + path, _hash(body))
        for low, row in rows.items():
            if low not in disk_rows:
                to_write.append((row.path, row.body))
                ss.set(s, MIRROR_PREFIX + row.path, _hash(row.body))
    for path, body in to_write:
        try:
            _write_disk(root, path, body)
        except AppError:
            logger.warning("Bỏ qua file skill '%s': đường dẫn không hợp lệ.", path)


def _find(s: Any, path: str, *, alive: bool) -> SkillFile | None:
    """Dòng theo đường dẫn (cột COLLATE NOCASE: không phân biệt hoa thường); `alive=False` → chỉ dòng đã xoá mềm."""
    q = select(SkillFile).where(SkillFile.path == path)
    q = q.where(SkillFile.deleted_at.is_(None) if alive else SkillFile.deleted_at.is_not(None))
    return s.scalars(q.order_by(SkillFile.id.desc()).limit(1)).first()


def _out(row: SkillFile, *, body: bool = True) -> dict[str, Any]:
    d = {"path": row.path, "size": len(row.body.encode("utf-8")), "updated_at": row.updated_at}
    if body:
        d["body"] = row.body
    return d


def list_files(ctx: OpContext) -> dict[str, Any]:
    """Chỉ đọc DB (đồng bộ chạy lúc khởi động, trước skill.update và ở op skill.sync)."""
    with ctx.services.db.session() as s:
        rows = s.scalars(select(SkillFile).where(SkillFile.deleted_at.is_(None)).order_by(SkillFile.path.collate("BINARY"))).all()
        return {"items": [_out(r, body=False) for r in rows], "dir": SKILL_REL.as_posix()}


def get(ctx: OpContext, path: str) -> dict[str, Any]:
    path = check_path(path)
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
        row = _find(s, path, alive=True) or _find(s, path, alive=False)
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


def sync_on_startup(services: Any) -> None:
    """Đồng bộ file skill khi khởi động (actor `system:skills`); lỗi chỉ ghi log, không chặn app."""
    import logging

    try:
        sync(OpContext(services=services, actor="system:skills", role="owner"))
    except Exception as e:  # noqa: BLE001
        logging.getLogger("sagareel.skills").warning("Không đồng bộ được file skill (%s).", type(e).__name__)


def sync_report(ctx: OpContext) -> dict[str, Any]:
    """Op `skill.sync`: đồng bộ rồi trả danh sách file."""
    sync(ctx)
    return list_files(ctx)