"""Dự án mẫu: project bình thường có `is_sample=True`, nhân bản ra dự án mới (prompt, phong cách, số liệu, quy trình).

- Dữ liệu seed: `defaults/samples/<slug>/project.yaml` + `prompts/<key>.md|.j2` (tên file = khoá prompt). `genre` không
  ghi trong project.yaml → chép số liệu tham khảo `defaults/genres/<genre_key>.yaml`.
- `seed(db)` (khởi động, idempotent): chỉ tạo mẫu khi CHƯA có project nào mang key đó — kể cả đã xoá mềm (người dùng
  xoá mẫu thì mẫu không tự quay lại); không bao giờ ghi đè chỉnh sửa. `reseed(ctx, keys)` (op `sample.reseed`) tạo lại
  mẫu đã xoá/thiếu một cách tường minh.
- `backfill_once(db)`: một lần duy nhất (cờ `system_state`), chép bộ prompt mẫu cho project cũ chưa có prompt nào
  (narration → Kể chuyện xưa, series → Phim thoại hoạt hình, còn lại → Phim truyện người thật). Project trống tạo về
  sau không bị tự điền.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import exists, select

from sagareel.core.context import OpContext
from sagareel.core.errors import NotFound
from sagareel.core.uow import UnitOfWork
from sagareel.db.models import Project, ProjectPrompt
from sagareel.db.session import Database
from sagareel.services import genres, prompts, system_state

logger = logging.getLogger("sagareel.samples")

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "defaults" / "samples"
SEED_ACTOR = "system:seed"
BACKFILL_FLAG = "prompts_backfilled_at"
GENRE_SAMPLE = {"narration": "ke-chuyen-xua", "series": "phim-thoai-hoat-hinh"}
DEFAULT_SAMPLE = "phim-truyen-nguoi-that"
_FIELDS = ("name", "description", "genre_key", "aspect", "language", "style_block", "negative_block",
           "narrator_voice")


def slug_for_genre(genre_key: str | None) -> str:
    return GENRE_SAMPLE.get(genre_key or "", DEFAULT_SAMPLE)


def _prompt_key(path: Path) -> str:
    return path.name.rsplit(".", 1)[0]


@functools.lru_cache(maxsize=None)
def _definition(slug: str) -> dict[str, Any]:
    folder = SAMPLES_DIR / slug
    raw = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8")) or {}
    d = {k: str(raw.get(k) or "") for k in _FIELDS}
    d["slug"] = slug
    d["key"] = str(raw.get("key") or f"mau-{slug}")
    d["genre_key"] = d["genre_key"] or "series"
    d["aspect"] = d["aspect"] or "9:16"
    d["language"] = d["language"] or "vi"
    d["genre"] = genres.validate(raw.get("genre") or genres.reference(d["genre_key"]) or {})
    d["prompts"] = {_prompt_key(p): p.read_bytes().decode("utf-8").replace("\r\n", "\n")
                    for p in sorted((folder / "prompts").iterdir()) if p.is_file()}
    return d


def slugs() -> list[str]:
    return sorted(p.name for p in SAMPLES_DIR.iterdir() if (p / "project.yaml").is_file())


def definition(slug: str) -> dict[str, Any]:
    if slug not in slugs():
        raise NotFound(f"Không có dữ liệu mẫu '{slug}'.", code="sample_not_found",
                       hint=f"Mẫu có: {', '.join(slugs())}.")
    d = _definition(slug)
    return {**d, "genre": dict(d["genre"]), "prompts": dict(d["prompts"])}


def _create(s: Any, d: dict[str, Any]) -> Project:
    from sagareel.services.pipeline import workflow as W

    p = Project(key=d["key"], name=d["name"], description=d["description"], genre_key=d["genre_key"],
                aspect=d["aspect"], language=d["language"], languages=[d["language"]], genre=d["genre"],
                workflows=W.defaults(), style_block=d["style_block"], negative_block=d["negative_block"],
                narrator_voice=d["narrator_voice"], is_sample=True)
    s.add(p)
    s.flush()
    for key, body in d["prompts"].items():
        s.add(ProjectPrompt(project_id=p.id, key=key, body=body))
    s.flush()
    return p


def seed(db: Database) -> list[str]:
    """Tạo dự án mẫu còn thiếu (theo key, tính cả dòng đã xoá mềm). Trả key đã tạo."""
    created: list[str] = []
    u = UnitOfWork(db, actor=SEED_ACTOR, op_name="sample.seed", project_id=None, summary="Tạo dự án mẫu")
    with u as s:
        for slug in slugs():
            d = definition(slug)
            if s.scalar(select(exists().where(Project.key == d["key"]))):
                continue
            _create(s, d)
            created.append(d["key"])
    if created:
        logger.info("Seed: đã tạo dự án mẫu %s", ", ".join(created))
    return created


def reseed(ctx: OpContext, keys: list[str] | None = None) -> dict[str, Any]:
    """Tạo lại dự án mẫu chưa có (chưa xoá) — gồm mẫu người dùng đã xoá. Mẫu đang có giữ nguyên."""
    from sagareel.services import entities

    created: list[dict[str, Any]] = []
    u = entities.uow(ctx, "Tạo lại dự án mẫu", "sample.reseed")
    with u as s:
        for slug in slugs():
            d = definition(slug)
            if keys and d["key"] not in keys and slug not in keys:
                continue
            alive = s.scalar(select(exists().where(Project.key == d["key"], Project.deleted_at.is_(None))))
            if alive:
                continue
            p = _create(s, d)
            created.append({"id": p.id, "key": p.key, "name": p.name})
    return {"created": created, "available": [definition(x)["key"] for x in slugs()]}


def install_prompts(s: Any, project_id: int, slug: str) -> int:
    """Chép bộ prompt của dữ liệu mẫu `slug` vào project (ghi đè khoá trùng). Trả số khoá."""
    d = definition(slug)
    for key, body in d["prompts"].items():
        prompts.put(s, project_id, key, body)
    return len(d["prompts"])


def backfill_once(db: Database) -> int:
    """Một lần: project cũ (không phải mẫu) chưa có prompt nào → chép bộ prompt mẫu theo thể loại."""
    with db.session() as s0:
        if system_state.get(s0, BACKFILL_FLAG) is not None:
            return 0
    n = 0
    u = UnitOfWork(db, actor=SEED_ACTOR, op_name="sample.backfill", project_id=None,
                   summary="Chép bộ prompt mẫu cho dự án cũ")
    with u as s:
        rows = s.scalars(select(Project).where(Project.deleted_at.is_(None), Project.is_sample.is_(False))).all()
        for p in rows:
            if s.scalar(select(exists().where(ProjectPrompt.project_id == p.id))):
                continue
            install_prompts(s, p.id, slug_for_genre(p.genre_key))
            n += 1
    from sagareel.db.base import utcnow

    with db.session(immediate=True) as s1:
        system_state.set(s1, BACKFILL_FLAG, utcnow().isoformat())
    if n:
        logger.info("Seed: đã chép bộ prompt mẫu cho %d dự án cũ", n)
    return n


def find_sample(s: Any, genre_key: str | None) -> Project | None:
    """Dự án mẫu (chưa xoá) ứng với thể loại — dùng khi tạo dự án từ ý tưởng mà không chỉ định mẫu."""
    key = definition(slug_for_genre(genre_key))["key"]
    return s.scalars(select(Project).where(Project.key == key, Project.is_sample.is_(True),
                                           Project.deleted_at.is_(None)).limit(1)).first()
