"""Nghiệp vụ tập (sequence) + cảnh (scene): CRUD, nhân bản, đổi thứ tự, kiểm tra ngữ pháp shot (lint), tổng chi phí,
xuất shot list CSV/XLSX (spec §6, §14.7, phụ lục film-craft §2).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sagareel.core import rank
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ConfirmRequired, ValidationFailed
from sagareel.db.base import utcnow
from sagareel.db.models import (
    Asset,
    Clip,
    DialogueLine,
    Scene,
    Sequence,
    Take,
    TimelineItem,
)
from sagareel.services import clips, confirm, entities, media

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SEQUENCE_STATUSES = frozenset({"draft", "writing", "producing", "review", "rendered", "published", "archived"})


def _check_key(key: str, label: str) -> None:
    if not KEY_RE.match(key):
        raise ValidationFailed(f"Mã {label} chỉ gồm chữ không dấu, số, '-' hoặc '_' (tối đa 64 ký tự).",
                               extra={"errors": [{"loc": ["key"], "msg": "Mã không hợp lệ.", "type": "pattern"}]})


# --- Sequence -------------------------------------------------------------------------------------------------------


def _counts(s: Session, sequence_ids: list[int]) -> dict[int, dict[str, int]]:
    out = {i: {"clips": 0, "scenes": 0, "duration_ms": 0} for i in sequence_ids}
    if not sequence_ids:
        return out
    for sid, n, dur in s.execute(
        select(Clip.sequence_id, func.count(), func.coalesce(func.sum(Clip.duration_ms), 0))
        .where(Clip.sequence_id.in_(sequence_ids), Clip.deleted_at.is_(None)).group_by(Clip.sequence_id)
    ):
        out[sid]["clips"] = n
        out[sid]["duration_ms"] = int(dur)
    for sid, n in s.execute(
        select(Scene.sequence_id, func.count()).where(Scene.sequence_id.in_(sequence_ids), Scene.deleted_at.is_(None))
        .group_by(Scene.sequence_id)
    ):
        out[sid]["scenes"] = n
    return out


def seq_out(s: Session, seq: Sequence) -> dict[str, Any]:
    return {**entities.to_dict(seq), "counts": _counts(s, [seq.id])[seq.id]}


def get(ctx: OpContext, sequence_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        return seq_out(s, entities.get(s, "sequence", sequence_id))


def list_sequences(ctx: OpContext, project_id: int, group_id: int | None = None, cursor: str | None = None,
                   limit: int = 100) -> dict[str, Any]:
    """Tập của project theo (number, id); cursor `"<number>:<id>"`."""
    limit = max(1, min(limit, 500))
    with ctx.services.db.session() as s:
        entities.get(s, "project", project_id)
        q = select(Sequence).where(Sequence.project_id == project_id, Sequence.deleted_at.is_(None))
        if group_id is not None:
            q = q.where(Sequence.group_id == group_id)
        if cursor:
            n, sep, i = cursor.partition(":")
            try:
                num, sid = int(n), int(i)
            except ValueError:
                raise AppError("invalid_cursor", "Cursor phân trang không hợp lệ.", status=422) from None
            if not sep:
                raise AppError("invalid_cursor", "Cursor phân trang không hợp lệ.", status=422)
            q = q.where(or_(Sequence.number > num, and_(Sequence.number == num, Sequence.id > sid)))
        rows = s.scalars(q.order_by(Sequence.number, Sequence.id).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        c = _counts(s, [r.id for r in rows])
        return {"items": [{**entities.to_dict(r), "counts": c[r.id]} for r in rows],
                "next_cursor": f"{rows[-1].number}:{rows[-1].id}" if more else None}


def _free_key(s: Session, project_id: int, base: str) -> str:
    used = set(s.scalars(select(Sequence.key).where(Sequence.project_id == project_id)))
    key, n = base, 1
    while key in used:
        n += 1
        key = f"{base}-{n}"
    return key


def create(ctx: OpContext, project_id: int, title: str, *, key: str | None = None, number: int | None = None,
           group_id: int | None = None, synopsis: str = "", script: str = "",
           target_platforms: list[str] | None = None) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValidationFailed("Tên tập không được để trống.")
    if key is not None:
        _check_key(key, "tập")
    u = entities.uow(ctx, f"Tạo tập {title}", "sequence.create")
    try:
        with u as s:
            entities.get(s, "project", project_id)
            u.project_id = project_id
            if group_id is not None:
                g = entities.get(s, "group_node", group_id)
                if g.project_id != project_id:
                    raise ValidationFailed(f"Nhóm #{group_id} không thuộc dự án #{project_id}.")
            if number is None:
                number = int(s.scalar(select(func.coalesce(func.max(Sequence.number), 0)).where(
                    Sequence.project_id == project_id, Sequence.deleted_at.is_(None))) or 0) + 1
            if key is None:
                key = _free_key(s, project_id, f"ep{number:03d}")
            elif s.scalar(select(func.count()).select_from(Sequence).where(Sequence.project_id == project_id,
                                                                          Sequence.key == key,
                                                                          Sequence.deleted_at.is_(None))):
                raise AppError("key_taken", f"Mã tập '{key}' đã được dùng.", hint="Chọn mã khác hoặc bỏ trống.",
                               status=409)
            seq = Sequence(project_id=project_id, group_id=group_id, key=key, number=number, title=title,
                           synopsis=synopsis, script=script, target_platforms=list(target_platforms or []))
            s.add(seq)
            s.flush()
            out = seq_out(s, seq)
    except IntegrityError as e:
        raise entities.integrity_error("sequence", e) from None
    return out


def update(ctx: OpContext, sequence_id: int, values: dict[str, Any],
           expected_updated_at: datetime | None = None) -> dict[str, Any]:
    if "key" in values and isinstance(values["key"], str):
        _check_key(values["key"], "tập")
    if "title" in values and isinstance(values["title"], str) and not values["title"].strip():
        raise ValidationFailed("Tên tập không được để trống.")
    if "status" in values and values["status"] not in SEQUENCE_STATUSES:
        raise ValidationFailed(f"Trạng thái tập không hợp lệ; chọn một trong: {', '.join(sorted(SEQUENCE_STATUSES))}.")
    entities.patch(ctx, "sequence", sequence_id, values, expected_updated_at)
    return get(ctx, sequence_id)


def _seq_impact(s: Session, rows: list[Any]) -> dict[str, Any]:
    total = {"clips": 0, "scenes": 0, "duration_ms": 0}
    for c in _counts(s, [r.id for r in rows]).values():
        for k in total:
            total[k] += c[k]
    return {"counts": total}


entities.IMPACT_EXTRA["sequence"] = _seq_impact


def delete_preview(ctx: OpContext, sequence_id: int) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, "sequence", [sequence_id])


def delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    return entities.soft_delete(ctx, confirm_token, expect_type="sequence")


def reorder(ctx: OpContext, project_id: int, ids: list[int]) -> dict[str, Any]:
    """Đánh lại `number` = 1..n theo thứ tự `ids` (chỉ ghi dòng đổi số). Tập không có trong `ids` xếp sau, giữ thứ
    tự cũ."""
    ids = list(dict.fromkeys(ids))
    u = entities.uow(ctx, "Đổi thứ tự tập", "sequence.reorder")
    with u as s:
        entities.get(s, "project", project_id)
        u.project_id = project_id
        rows = s.scalars(select(Sequence).where(Sequence.project_id == project_id, Sequence.deleted_at.is_(None))
                         .order_by(Sequence.number, Sequence.id)).all()
        by_id = {r.id: r for r in rows}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise ValidationFailed(f"Tập không thuộc dự án: {', '.join(map(str, unknown))}.")
        order = [by_id[i] for i in ids] + [r for r in rows if r.id not in set(ids)]
        changed = 0
        for n, r in enumerate(order, start=1):
            if r.number != n:
                r.number = n
                changed += 1
    return {"items": [{"id": r.id, "number": r.number} for r in order], "changed": changed}


def duplicate(ctx: OpContext, sequence_id: int, title: str | None = None, key: str | None = None) -> dict[str, Any]:
    """Tập mới chép cảnh, clip (không take, không cờ), lời thoại, mục timeline (neo lại sang clip mới)."""
    if key is not None:
        _check_key(key, "tập")
    u = entities.uow(ctx, f"Nhân bản tập #{sequence_id}", "sequence.duplicate")
    try:
        with u as s:
            src = entities.get(s, "sequence", sequence_id)
            u.project_id = src.project_id
            number = int(s.scalar(select(func.max(Sequence.number)).where(
                Sequence.project_id == src.project_id, Sequence.deleted_at.is_(None))) or 0) + 1
            new_key = key or _free_key(s, src.project_id, f"ep{number:03d}")
            new = Sequence(**clips.copy_values(src, key=new_key, number=number, title=title or f"{src.title} (bản sao)",
                                               status="draft", gates={}, pipeline={}))
            s.add(new)
            s.flush()
            scene_map: dict[int, int] = {}
            for sc in s.scalars(select(Scene).where(Scene.sequence_id == src.id, Scene.deleted_at.is_(None))
                                .order_by(Scene.rank, Scene.id)):
                c = Scene(**clips.copy_values(sc, sequence_id=new.id))
                s.add(c)
                s.flush()
                scene_map[sc.id] = c.id
            clip_map: dict[int, int] = {}
            for cl in clips.ordered(s, src.id):
                c = Clip(**clips.copy_values(cl, sequence_id=new.id, scene_id=scene_map.get(cl.scene_id),
                                             image_take_id=None, video_take_id=None, flags={}, review="pending"))
                s.add(c)
                s.flush()
                clip_map[cl.id] = c.id
                clips.copy_dialogue(s, cl.id, c.id)
            for it in s.scalars(select(TimelineItem).where(TimelineItem.sequence_id == src.id,
                                                           TimelineItem.deleted_at.is_(None))):
                end = dict(it.end_anchor or {})
                if end.get("clip_id") in clip_map:
                    end["clip_id"] = clip_map[end["clip_id"]]
                s.add(TimelineItem(**clips.copy_values(it, sequence_id=new.id,
                                                       anchor_clip_id=clip_map.get(it.anchor_clip_id),
                                                       end_anchor=end or it.end_anchor)))
            s.flush()
            out = seq_out(s, new)
    except IntegrityError as e:
        raise entities.integrity_error("sequence", e) from None
    return out


# --- Scene ----------------------------------------------------------------------------------------------------------


def _scene_neighbors(s: Session, sequence_id: int, after_scene_id: int | None, exclude: int | None):
    alive = [Scene.sequence_id == sequence_id, Scene.deleted_at.is_(None)]
    if exclude is not None:
        alive.append(Scene.id != exclude)
    if after_scene_id is None:
        return s.scalar(select(func.max(Scene.rank)).where(*alive)), None
    if after_scene_id == 0:
        return None, s.scalar(select(func.min(Scene.rank)).where(*alive))
    a = entities.get(s, "scene", after_scene_id)
    if a.sequence_id != sequence_id:
        raise ValidationFailed(f"Cảnh #{after_scene_id} không thuộc tập #{sequence_id}.")
    return a.rank, s.scalar(select(func.min(Scene.rank)).where(*alive, Scene.rank > a.rank))


def _scene_rank(s: Session, sequence_id: int, after_scene_id: int | None, exclude: int | None = None) -> str:
    prev, nxt = _scene_neighbors(s, sequence_id, after_scene_id, exclude)
    try:
        return rank.between(prev, nxt)
    except ValueError:  # rank trùng: đánh lại cả danh sách cảnh (ít dòng)
        rows = s.scalars(select(Scene).where(Scene.sequence_id == sequence_id, Scene.deleted_at.is_(None))
                         .order_by(Scene.rank, Scene.id)).all()
        for sc, k in zip(rows, rank.rebalance([r.rank for r in rows]), strict=True):
            sc.rank = k
        s.flush()
        prev, nxt = _scene_neighbors(s, sequence_id, after_scene_id, exclude)
        return rank.between(prev, nxt)


def scene_create(ctx: OpContext, sequence_id: int, title: str = "", *, key: str | None = None,
                 after_scene_id: int | None = None, location_asset_id: int | None = None, summary: str = "",
                 state: dict | None = None) -> dict[str, Any]:
    if key is not None:
        _check_key(key, "cảnh")
    u = entities.uow(ctx, f"Tạo cảnh {title}".strip(), "scene.create")
    try:
        with u as s:
            seq = entities.get(s, "sequence", sequence_id)
            u.project_id = seq.project_id
            if location_asset_id is not None:
                a = entities.get(s, "asset", location_asset_id)
                if a.project_id != seq.project_id:
                    raise ValidationFailed("Bối cảnh không thuộc dự án của tập.")
            used = set(s.scalars(select(Scene.key).where(Scene.sequence_id == sequence_id)))
            alive = set(s.scalars(select(Scene.key).where(Scene.sequence_id == sequence_id,
                                                          Scene.deleted_at.is_(None))))
            if key is None:
                n = len(used)
                while True:
                    n += 1
                    key = f"s{n:02d}"
                    if key not in used:
                        break
            elif key in alive:
                raise AppError("key_taken", f"Mã cảnh '{key}' đã có trong tập.", status=409)
            sc = Scene(sequence_id=sequence_id, key=key, rank=_scene_rank(s, sequence_id, after_scene_id),
                       title=title, location_asset_id=location_asset_id, summary=summary, state=dict(state or {}))
            s.add(sc)
            s.flush()
            out = entities.to_dict(sc)
    except IntegrityError as e:
        raise entities.integrity_error("scene", e) from None
    return out


def scene_update(ctx: OpContext, scene_id: int, values: dict[str, Any],
                 expected_updated_at: datetime | None = None) -> dict[str, Any]:
    bad = sorted(k for k in values if k in ("rank", "sequence_id"))
    if bad:
        raise AppError("readonly_field", f"Không sửa được trường: {', '.join(bad)}.", hint="Dùng scene.move.",
                       status=422, extra={"fields": bad})
    return entities.patch(ctx, "scene", scene_id, values, expected_updated_at)


def scene_move(ctx: OpContext, scene_id: int, after_scene_id: int | None = None) -> dict[str, Any]:
    """Đổi chỗ cảnh (chỉ ghi rank của cảnh đó). Clip giữ nguyên rank — muốn kéo cả clip theo dùng clip.move."""
    u = entities.uow(ctx, f"Di chuyển cảnh #{scene_id}", "scene.move")
    with u as s:
        sc = entities.get(s, "scene", scene_id)
        u.project_id = entities.project_id_of(s, sc)
        if after_scene_id == scene_id:
            raise ValidationFailed("Không đặt cảnh sau chính nó.")
        sc.rank = _scene_rank(s, sc.sequence_id, after_scene_id, exclude=scene_id)
        s.flush()
        out = entities.to_dict(sc)
    return out


def _scene_impact(s: Session, rows: list[Any]) -> dict[str, Any]:
    n = s.scalar(select(func.count()).select_from(Clip).where(Clip.scene_id.in_([r.id for r in rows]),
                                                              Clip.deleted_at.is_(None)))
    return {"clips_unassigned": n}


entities.IMPACT_EXTRA["scene"] = _scene_impact


def scene_delete_preview(ctx: OpContext, ids: list[int]) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, "scene", ids)


def scene_delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    """Xoá mềm cảnh; clip của cảnh giữ nguyên, chỉ bỏ `scene_id` (cùng change set)."""
    type_, ids, inner = entities.parse_token(confirm_token)
    if type_ != "scene":
        raise ConfirmRequired("confirm_token này không dành cho cảnh.", hint="Gọi scene.delete_preview.")
    u = entities.uow(ctx, f"Xoá {len(ids)} cảnh", "scene.delete")
    with u as s:
        rows = [entities.get(s, "scene", i) for i in ids]
        confirm.verify(s, entities.delete_op("scene"), ids, entities.impact(s, "scene", rows), inner)
        projects = {entities.project_id_of(s, r) for r in rows}
        u.project_id = projects.pop() if len(projects) == 1 else None
        now = utcnow()
        for c in s.scalars(select(Clip).where(Clip.scene_id.in_(ids), Clip.deleted_at.is_(None))):
            c.scene_id = None
        for r in rows:
            r.deleted_at = now
    return {"type": "scene", "deleted": ids}


# --- Lint (ngữ pháp shot) -------------------------------------------------------------------------------------------

CHARS_PER_S = 4.5
CONTINUE_CHAIN_MAX = 3


def _dialogue_chars(s: Session, sequence_id: int) -> dict[int, int]:
    rows = s.execute(
        select(DialogueLine.clip_id, func.coalesce(func.sum(func.length(DialogueLine.text)), 0))
        .join(Clip, Clip.id == DialogueLine.clip_id)
        .where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None), DialogueLine.deleted_at.is_(None))
        .group_by(DialogueLine.clip_id)
    )
    return {cid: int(n) for cid, n in rows}


def _primary_asset(c: Clip) -> Any:
    for a in c.assets or []:
        if isinstance(a, dict) and a.get("asset_id") is not None:
            return a["asset_id"]
    return None


def lint(ctx: OpContext, sequence_id: int) -> dict[str, Any]:
    """Cảnh báo ngữ pháp shot (không chặn): axis_flip, same_size_repeat, screen_dir_flip, continue_chain_too_long,
    dialogue_too_long. Hai clip liền nhau chỉ so khi cùng cảnh."""
    with ctx.services.db.session() as s:
        entities.get(s, "sequence", sequence_id)
        rows = clips.ordered(s, sequence_id)
        chars = _dialogue_chars(s, sequence_id)
    warnings: list[dict[str, Any]] = []

    def warn(code: str, c: Clip, msg: str, prev: Clip | None = None, **extra: Any) -> None:
        w = {"code": code, "level": "warning", "clip_id": c.id, "clip_key": c.key, "message": msg}
        if prev is not None:
            w["related_clip_id"] = prev.id
            w["related_clip_key"] = prev.key
        w.update(extra)
        warnings.append(w)

    chain = 0
    prev: Clip | None = None
    for c in rows:
        cut_in = c.cut_in or ""
        neutral = cut_in == "cutaway" or c.eyeline == "to_camera"
        if prev is not None and prev.scene_id == c.scene_id:
            a1, a2 = prev.axis_side or "", c.axis_side or ""
            if a1 and a2 and a1 != a2 and not neutral:
                warn("axis_flip", c, f"Clip {c.key} vượt trục 180° (phía {a1} → {a2}) so với clip {prev.key}; "
                     "chèn cutaway/shot trung tính hoặc giữ cùng phía.", prev)
            d1, d2 = prev.screen_dir, c.screen_dir
            if {d1, d2} == {"L→R", "R→L"} and cut_in != "cutaway":
                warn("screen_dir_flip", c, f"Hướng màn hình đảo ({d1} → {d2}) giữa clip {prev.key} và {c.key}.", prev)
            if (c.shot_size and c.shot_size == prev.shot_size and c.angle == prev.angle
                    and _primary_asset(c) == _primary_asset(prev) and cut_in != "cutaway"):
                warn("same_size_repeat", c, f"Clip {c.key} cùng cỡ ({c.shot_size}) và cùng góc với clip {prev.key}: "
                     "đổi ít nhất một bậc cỡ cảnh hoặc ≥ 30° góc máy.", prev)
        if c.link == "continue":
            chain += 1
            if chain == CONTINUE_CHAIN_MAX + 1:
                warn("continue_chain_too_long", c, f"Hơn {CONTINUE_CHAIN_MAX} clip nối tiếp (continue) liền nhau tới "
                     f"clip {c.key}: nên nạp lại ảnh tham chiếu gốc để tránh trôi nhân vật.", chain=chain)
        else:
            chain = 0
        n = chars.get(c.id, 0)
        limit = max(0.0, (c.duration_ms / 1000 - 2) * CHARS_PER_S)
        if n > limit:
            warn("dialogue_too_long", c, f"Lời thoại clip {c.key} dài {n} ký tự, vượt {int(limit)} ký tự cho "
                 f"{c.duration_ms / 1000:g} s (≈ (thời lượng − 2) × 4,5).", chars=n, max_chars=int(limit))
        prev = c
    counts: dict[str, int] = {}
    for w in warnings:
        counts[w["code"]] = counts.get(w["code"], 0) + 1
    return {"sequence_id": sequence_id, "clips": len(rows), "warnings": warnings, "counts": counts}


# --- Chi phí --------------------------------------------------------------------------------------------------------


def cost_summary(ctx: OpContext, sequence_id: int, per_clip: bool = False) -> dict[str, Any]:
    """Tổng `take.cost` của mọi take (mọi trạng thái, kể cả lần thử lại và take của clip đã xoá) của tập, theo phần
    (image/video/audio) — audio gồm take của lời thoại."""
    with ctx.services.db.session() as s:
        entities.get(s, "sequence", sequence_id)
        clip_ids = select(Clip.id).where(Clip.sequence_id == sequence_id).scalar_subquery()
        line_ids = (select(DialogueLine.id).join(Clip, Clip.id == DialogueLine.clip_id)
                    .where(Clip.sequence_id == sequence_id).scalar_subquery())
        owned = or_(and_(Take.owner_type == "clip", Take.owner_id.in_(clip_ids)),
                    and_(Take.owner_type == "dialogue", Take.owner_id.in_(line_ids)))
        ok = case((Take.status == "succeeded", 1), else_=0)
        by_part: dict[str, dict[str, Any]] = {}
        total = {"cost": 0.0, "takes": 0, "succeeded": 0, "retries": 0}
        for part, cost, n, n_ok in s.execute(
            select(Take.part, func.coalesce(func.sum(Take.cost), 0.0), func.count(), func.sum(ok))
            .where(owned).group_by(Take.part)
        ):
            by_part[part] = {"cost": float(cost), "takes": int(n), "succeeded": int(n_ok or 0)}
        # lần thử lại = số take vượt quá 1 cho mỗi (chủ, phần)
        sub = select(Take.owner_type, Take.owner_id, Take.part, func.count().label("n")).where(owned).group_by(
            Take.owner_type, Take.owner_id, Take.part).subquery()
        for part, extra in s.execute(select(sub.c.part, func.sum(sub.c.n - 1)).group_by(sub.c.part)):
            by_part.setdefault(part, {"cost": 0.0, "takes": 0, "succeeded": 0})["retries"] = int(extra or 0)
        for v in by_part.values():
            v.setdefault("retries", 0)
            for k in total:
                total[k] += v[k]
        out: dict[str, Any] = {"sequence_id": sequence_id, "total": total["cost"], "takes": total["takes"],
                               "succeeded": total["succeeded"], "retries": total["retries"], "by_part": by_part}
        if per_clip:
            rows = s.execute(
                select(Take.owner_id, Take.part, func.sum(Take.cost), func.count())
                .where(Take.owner_type == "clip", Take.owner_id.in_(clip_ids)).group_by(Take.owner_id, Take.part)
            ).all()
            keys = dict(s.execute(select(Clip.id, Clip.key).where(Clip.sequence_id == sequence_id)).all())
            per: dict[int, dict[str, Any]] = {}
            for cid, part, cost, n in rows:
                e = per.setdefault(cid, {"clip_id": cid, "clip_key": keys.get(cid), "cost": 0.0, "takes": 0,
                                         "by_part": {}})
                e["cost"] += float(cost or 0)
                e["takes"] += int(n)
                e["by_part"][part] = float(cost or 0)
            out["clips"] = sorted(per.values(), key=lambda e: -e["cost"])
    return out


# --- Shot list ------------------------------------------------------------------------------------------------------

SHOTLIST_COLUMNS = [
    ("index", "#"), ("key", "Mã clip"), ("scene", "Cảnh"), ("title", "Tiêu đề"), ("duration_s", "Thời lượng (s)"),
    ("shot_size", "Cỡ cảnh"), ("angle", "Góc máy"), ("camera_move", "Chuyển động máy"), ("lens", "Ống kính"),
    ("composition", "Bố cục"), ("eyeline", "Hướng nhìn"), ("screen_dir", "Hướng màn hình"),
    ("axis_side", "Phía trục"), ("cut_in", "Vào"), ("cut_out", "Ra"), ("link", "Kiểu nối"), ("engine", "Engine"),
    ("characters", "Nhân vật/tài sản"), ("image_prompt", "Prompt ảnh"), ("motion_prompt", "Prompt chuyển động"),
    ("dialogue", "Lời thoại"), ("sfx_desc", "Âm thanh"), ("review", "Duyệt"), ("flags", "Cờ"),
    ("image_take_id", "Take ảnh"), ("video_take_id", "Take video"), ("cost", "Chi phí"), ("notes", "Ghi chú"),
]


def _ascii_slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:48] or "x"


def shotlist_rows(s: Session, sequence_id: int) -> list[dict[str, Any]]:
    rows = clips.ordered(s, sequence_id)
    scenes = dict(s.execute(select(Scene.id, Scene.key).where(Scene.sequence_id == sequence_id)).all())
    lines: dict[int, list[str]] = {}
    dl = s.scalars(select(DialogueLine).join(Clip, Clip.id == DialogueLine.clip_id)
                   .where(Clip.sequence_id == sequence_id, DialogueLine.deleted_at.is_(None))
                   .order_by(DialogueLine.clip_id, DialogueLine.rank, DialogueLine.id)).all()
    asset_ids = {a.get("asset_id") for c in rows for a in (c.assets or []) if isinstance(a, dict)}
    speakers = {d.speaker_asset_id for d in dl if d.speaker_asset_id is not None}
    names = dict(s.execute(select(Asset.id, Asset.key).where(Asset.id.in_({i for i in asset_ids | speakers if i})))
                 .all())
    for d in dl:
        who = names.get(d.speaker_asset_id, "") if d.speaker_asset_id else ""
        lines.setdefault(d.clip_id, []).append(f"{who}: {d.text}" if who else d.text)
    costs = dict(s.execute(select(Take.owner_id, func.sum(Take.cost)).where(
        Take.owner_type == "clip", Take.owner_id.in_(select(Clip.id).where(Clip.sequence_id == sequence_id)
                                                     .scalar_subquery())).group_by(Take.owner_id)).all())
    out = []
    for i, c in enumerate(rows, start=1):
        chars = []
        for a in c.assets or []:
            if isinstance(a, dict) and a.get("asset_id") is not None:
                chars.append(str(names.get(a["asset_id"], a["asset_id"])))
        out.append({
            "index": i, "key": c.key, "scene": scenes.get(c.scene_id, "") if c.scene_id else "", "title": c.title,
            "duration_s": round(c.duration_ms / 1000, 3), "shot_size": c.shot_size, "angle": c.angle,
            "camera_move": c.camera_move, "lens": c.lens, "composition": c.composition, "eyeline": c.eyeline,
            "screen_dir": c.screen_dir, "axis_side": c.axis_side, "cut_in": c.cut_in,
            "cut_out": c.cut_out, "link": c.link, "engine": c.engine, "characters": ", ".join(chars),
            "image_prompt": c.image_prompt, "motion_prompt": c.motion_prompt,
            "dialogue": "\n".join(lines.get(c.id, [])), "sfx_desc": c.sfx_desc, "review": c.review,
            "flags": ", ".join(sorted(k for k, v in (c.flags or {}).items() if v)),
            "image_take_id": c.image_take_id or "", "video_take_id": c.video_take_id or "",
            "cost": float(costs.get(c.id) or 0.0), "notes": c.notes,
        })
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel mở đúng tiếng Việt
        w = csv.writer(f)
        w.writerow([label for _, label in SHOTLIST_COLUMNS])
        for r in rows:
            w.writerow([r[k] for k, _ in SHOTLIST_COLUMNS])


def _write_xlsx(path: Path, rows: list[dict[str, Any]], title: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = (_ascii_slug(title) or "shotlist")[:31]
    ws.append([label for _, label in SHOTLIST_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([r[k] for k, _ in SHOTLIST_COLUMNS])
    ws.freeze_panes = "A2"
    wrap = {k for k in ("image_prompt", "motion_prompt", "dialogue", "notes")}
    for idx, (k, _) in enumerate(SHOTLIST_COLUMNS, start=1):
        letter = ws.cell(row=1, column=idx).column_letter
        ws.column_dimensions[letter].width = 48 if k in wrap else 14
        if k in wrap:
            for row in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
                for cell in row:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def export_shotlist(ctx: OpContext, sequence_id: int, fmt: str = "csv") -> dict[str, Any]:
    """Ghi shot list ra `outputs/shotlists/<project>_<tập>_<thời điểm>.<csv|xlsx>` và đăng ký một media `other`
    (bản sao trong kho) để tải qua `/media/<id>`."""
    if fmt not in ("csv", "xlsx"):
        raise ValidationFailed("Định dạng shot list phải là csv hoặc xlsx.")
    settings = ctx.services.settings
    with ctx.services.db.session() as s:
        seq = entities.get(s, "sequence", sequence_id)
        project = entities.get(s, "project", seq.project_id)
        rows = shotlist_rows(s, sequence_id)
        project_id, pkey, skey, title = project.id, project.key, seq.key, seq.title
    folder = Path(settings.outputs_dir) / "shotlists"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{_ascii_slug(pkey)}_{_ascii_slug(skey)}_shotlist_{stamp}.{fmt}"
    path = folder / filename
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{filename.rsplit('.', 1)[0]}-{n}.{fmt}"
    if fmt == "csv":
        _write_csv(path, rows)
    else:
        _write_xlsx(path, rows, title)
    with ctx.services.db.session() as s:
        m = media.register(s, path, kind="other", origin="derived", settings=settings, project_id=project_id,
                           meta={"shotlist": {"sequence_id": sequence_id, "format": fmt, "filename": path.name}},
                           move=False)
        out_media = media.to_out(m).model_dump(mode="json")
    return {"path": str(path), "filename": path.name, "format": fmt, "rows": len(rows), "media_id": out_media["id"],
            "url": out_media["url"], "mime": out_media["mime"], "size": out_media["size"], "media": out_media}
