"""Nghiệp vụ project: tạo (mã tự sinh từ tên), đọc kèm số đếm, liệt kê, sửa, xoá mềm hai bước, nhân bản Library."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Conflict, ValidationFailed
from sagareel.db.models import (
    Asset,
    AssetKind,
    AssetVariant,
    Clip,
    Preset,
    Project,
    Sequence,
    Sound,
    VoiceProfile,
)
from sagareel.services import entities

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SKIP_COPY = frozenset({"id", "created_at", "updated_at", "deleted_at"})


def slugify(text: str, fallback: str = "project") -> str:
    """"Truyện Đêm Khuya" → "truyen-dem-khuya" (bỏ dấu, đ→d, chữ thường, gạch nối); rỗng → `fallback`."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:48].strip("-") or fallback


def _key_taken(s: Session, key: str, *, alive_only: bool = False) -> bool:
    """Mã tự sinh tránh cả project đã xoá mềm (hoàn tác xoá không đụng mã); mã người dùng đặt chỉ cần khác các project
    chưa xoá (unique một phần, migration 0005)."""
    q = select(func.count()).select_from(Project).where(Project.key == key)
    if alive_only:
        q = q.where(Project.deleted_at.is_(None))
    return s.scalar(q) > 0


def _free_key(s: Session, base: str) -> str:
    key, n = base, 1
    while _key_taken(s, key):
        n += 1
        key = f"{base}-{n}"
    return key


def _taken(key: str) -> Conflict:
    return Conflict(f"Mã dự án '{key}' đã được dùng.", code="key_taken",
                    hint="Chọn mã khác hoặc bỏ trống để tự sinh từ tên.")


def _check_name(name: str) -> str:
    if not name or not name.strip():
        raise ValidationFailed("Tên dự án không được để trống.",
                               extra={"errors": [{"loc": ["name"], "msg": "Tên dự án không được để trống.",
                                                  "type": "empty"}]})
    return name


def _check_key(key: str) -> None:
    if not KEY_RE.match(key):
        raise ValidationFailed(
            "Mã dự án chỉ gồm chữ không dấu, số, '-' hoặc '_' (tối đa 64 ký tự).",
            extra={"errors": [{"loc": ["key"], "msg": "Mã dự án không hợp lệ.", "type": "pattern"}]},
        )


# --- Đọc --------------------------------------------------------------------------------------------------------


def counts(s: Session, project_ids: list[int]) -> dict[int, dict[str, int]]:
    """Số tài sản / tập / clip (chưa xoá) của từng project."""
    out = {pid: {"assets": 0, "sequences": 0, "clips": 0} for pid in project_ids}
    if not project_ids:
        return out
    for pid, n in s.execute(
        select(Asset.project_id, func.count()).where(Asset.project_id.in_(project_ids), Asset.deleted_at.is_(None))
        .group_by(Asset.project_id)
    ):
        out[pid]["assets"] = n
    for pid, n in s.execute(
        select(Sequence.project_id, func.count())
        .where(Sequence.project_id.in_(project_ids), Sequence.deleted_at.is_(None))
        .group_by(Sequence.project_id)
    ):
        out[pid]["sequences"] = n
    for pid, n in s.execute(
        select(Sequence.project_id, func.count(Clip.id))
        .join(Clip, Clip.sequence_id == Sequence.id)
        .where(Sequence.project_id.in_(project_ids), Sequence.deleted_at.is_(None), Clip.deleted_at.is_(None))
        .group_by(Sequence.project_id)
    ):
        out[pid]["clips"] = n
    return out


def review_counts(s: Session, project_ids: list[int]) -> dict[int, dict[str, int]]:
    """Take chờ duyệt (cùng điều kiện `clips.PENDING_FILTER`) + clip có cờ drift, cộng theo project."""
    from sagareel.services import clips as clips_svc

    out: dict[int, dict[str, int]] = {pid: {} for pid in project_ids}
    seq_rows = s.execute(select(Sequence.id, Sequence.project_id).where(
        Sequence.project_id.in_(project_ids), Sequence.deleted_at.is_(None))).all()
    seq_ids = [sid for sid, _ in seq_rows]
    pending = clips_svc.count_pending(s, seq_ids)
    flags = clips_svc.count_flags(s, seq_ids)
    for pid in project_ids:
        out[pid]["takes_pending"] = 0
        out[pid]["drift"] = 0
    for sid, pid in seq_rows:
        out[pid]["takes_pending"] += pending[sid]
        out[pid]["drift"] += flags[sid]["drift"]
    return out


def out(s: Session, p: Project) -> dict[str, Any]:
    return {**entities.to_dict(p), "counts": counts(s, [p.id])[p.id]}


def get(ctx: OpContext, project_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        d = out(s, entities.get(s, "project", project_id))
        d["counts"].update(review_counts(s, [project_id])[project_id])
        return d


def list_projects(ctx: OpContext, cursor: int | None = None, limit: int = 50) -> dict[str, Any]:
    """Project chưa xoá, mới nhất trước; `cursor` = `next_cursor` (id) của trang trước."""
    limit = max(1, min(limit, 200))
    q = select(Project).where(Project.deleted_at.is_(None))
    if cursor is not None:
        q = q.where(Project.id < cursor)
    with ctx.services.db.session() as s:
        rows = s.scalars(q.order_by(Project.id.desc()).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        c = counts(s, [r.id for r in rows])
        rc = review_counts(s, [r.id for r in rows])
        items = [{**entities.to_dict(r), "counts": {**c[r.id], **rc[r.id]}} for r in rows]
    return {"items": items, "next_cursor": rows[-1].id if more else None}


# --- Ghi --------------------------------------------------------------------------------------------------------


def create(
    ctx: OpContext,
    name: str,
    key: str | None = None,
    genre_key: str | None = None,
    aspect: str | None = None,
    language: str | None = None,
    target_platforms: list[str] | None = None,
) -> dict[str, Any]:
    _check_name(name)
    if key is not None:
        _check_key(key)
    values: dict[str, Any] = {"name": name}
    if genre_key is not None:
        values["genre_key"] = genre_key
    if aspect is not None:
        values["aspect"] = aspect
    if language is not None:
        values["language"] = language
        values["languages"] = [language]
    if target_platforms is not None:
        values["target_platforms"] = check_platforms(ctx, target_platforms)
    u = entities.uow(ctx, f"Tạo dự án {name}", "project.create")
    try:
        with u as s:
            if key is not None and _key_taken(s, key, alive_only=True):
                raise _taken(key)
            p = Project(key=key or _free_key(s, slugify(name)), **values)
            s.add(p)
            s.flush()
            u.project_id = p.id
            result = out(s, p)
    except IntegrityError as e:
        raise entities.integrity_error("project", e) from None
    return result


def check_platforms(ctx: OpContext, keys: Any, project_id: int | None = None) -> list[str]:
    """Khoá nền tảng phải là preset `platform` có sẵn (toàn cục hoặc của dự án) — sai → `unknown_platform` (422)."""
    from sagareel.services import presets

    if not isinstance(keys, (list, tuple)) or not all(isinstance(k, str) for k in keys):
        raise ValidationFailed("target_platforms phải là danh sách khoá nền tảng (chuỗi).")
    out = list(dict.fromkeys(keys))
    presets.ensure_builtins(ctx.services)
    with ctx.services.db.session() as s:
        missing = [k for k in out if presets.find_row(s, "platform", k, project_id) is None]
    if missing:
        raise AppError("unknown_platform", f"Không có nền tảng: {', '.join(missing)}.",
                       hint="Gọi preset.list kind=platform để xem các khoá hợp lệ (vd. tiktok, yt-shorts).", status=422)
    return out


def update(ctx: OpContext, project_id: int, values: dict[str, Any],
           expected_updated_at: datetime | None = None) -> dict[str, Any]:
    if "name" in values and isinstance(values["name"], str):
        _check_name(values["name"])
    if "key" in values and isinstance(values["key"], str):
        _check_key(values["key"])
    if values.get("target_platforms") is not None:
        values = {**values, "target_platforms": check_platforms(ctx, values["target_platforms"], project_id)}
    entities.patch(ctx, "project", project_id, values, expected_updated_at)
    return get(ctx, project_id)


def _impact_extra(s: Session, rows: list[Any]) -> dict[str, Any]:
    p = rows[0]
    return {"project": {"id": p.id, "key": p.key, "name": p.name}, "counts": counts(s, [p.id])[p.id]}


entities.IMPACT_EXTRA["project"] = _impact_extra


def delete_preview(ctx: OpContext, project_id: int) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, "project", [project_id])


def delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    return entities.soft_delete(ctx, confirm_token, expect_type="project")


def _clone(obj: Any, **overrides: Any) -> Any:
    mapper = inspect(obj).mapper
    values = {a.key: getattr(obj, a.key) for a in mapper.column_attrs if a.key not in _SKIP_COPY}
    for k, v in values.items():  # bản sao JSON độc lập (không chia sẻ MutableDict/MutableList)
        if isinstance(v, (dict, list)):
            values[k] = entities._plain(v)
    values.update(overrides)
    return mapper.class_(**values)


def duplicate(ctx: OpContext, project_id: int, name: str, key: str | None = None) -> dict[str, Any]:
    """Project mới chép cài đặt + Library (loại tài sản riêng, hồ sơ giọng, tài sản + look, âm thanh) + preset của
    project. Không chép tập/clip/take. Một change set (hoàn tác được trọn gói)."""
    _check_name(name)
    if key is not None:
        _check_key(key)
    u = entities.uow(ctx, f"Nhân bản dự án #{project_id}", "project.duplicate")
    try:
        with u as s:
            src = entities.get(s, "project", project_id)
            if key is not None and _key_taken(s, key, alive_only=True):
                raise _taken(key)
            new = _clone(src, key=key or _free_key(s, slugify(name)), name=name)
            s.add(new)
            s.flush()
            u.project_id = new.id

            def alive(model):
                return s.scalars(select(model).where(model.project_id == src.id, model.deleted_at.is_(None))
                                 .order_by(model.id)).all()

            for row in alive(AssetKind):
                s.add(_clone(row, project_id=new.id))
            for row in alive(Preset):
                s.add(_clone(row, project_id=new.id))
            for row in alive(Sound):
                s.add(_clone(row, project_id=new.id))
            voice_map: dict[int, int] = {}
            for row in alive(VoiceProfile):
                c = _clone(row, project_id=new.id)
                s.add(c)
                s.flush()
                voice_map[row.id] = c.id
            for a in alive(Asset):
                c = _clone(a, project_id=new.id, voice_profile_id=voice_map.get(a.voice_profile_id, a.voice_profile_id))
                s.add(c)
                s.flush()
                variants = s.scalars(select(AssetVariant).where(AssetVariant.asset_id == a.id,
                                                                AssetVariant.deleted_at.is_(None))
                                     .order_by(AssetVariant.id)).all()
                for v in variants:
                    s.add(_clone(v, asset_id=c.id))
            s.flush()
            result = out(s, new)
    except IntegrityError as e:
        raise entities.integrity_error("project", e) from None
    return result
