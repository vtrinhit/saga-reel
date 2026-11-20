"""Nghiệp vụ clip (spec §6, §10, §23.1, phụ lục film-craft §2): tạo/sửa/đổi thứ tự/tách/nhân bản/xoá hai bước.

Thứ tự theo `rank` (fractional index, `core.rank`): chèn hay di chuyển k clip chỉ ghi đúng k dòng — không bao giờ đánh
số lại cả tập (trừ khi khoá trùng nhau, khi đó `_rebalance` chạy một lần). Danh sách phân trang cursor `"<rank>:<id>"`.

Trường shot dùng enum của `docs/research/film-craft/craft-and-prompts.md` §1 (chuỗi rỗng = chưa đặt). `axis_side`,
`cut_in`, `cut_out` là cột (migration 0005); giá trị cũ gửi trong `custom{...}` vẫn được nhận và chuyển lên cột.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sagareel.core import rank
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ConfirmRequired, NotFound, ValidationFailed
from sagareel.db.base import utcnow
from sagareel.db.models import Clip, DialogueLine, Sequence, Take, TimelineItem
from sagareel.services import confirm, entities, staleness

# --- Enum ---------------------------------------------------------------------------------------------------------

SHOT_ENUMS: dict[str, tuple[str, ...]] = {
    "shot_size": ("ews", "ws", "fs", "mws", "ms", "mcu", "cu", "ecu", "insert"),
    "angle": ("eye", "low", "high", "dutch", "ots", "pov", "overhead"),
    "camera_move": (
        "static", "push_in", "pull_out", "pan_l", "pan_r", "tilt_u", "tilt_d", "truck_l", "truck_r", "track", "arc",
        "crane_up", "crane_down", "handheld", "whip_pan", "rack_focus",
    ),
    "lens": ("24mm", "35mm", "50mm", "85mm", "anamorphic"),
    "composition": (
        "center", "thirds_l", "thirds_r", "symmetry", "leading_lines", "frame_in_frame", "negative_space",
        "headroom_tight",
    ),
    "eyeline": ("screen_left", "screen_right", "to_camera", "down", "up"),
    "screen_dir": ("L→R", "R→L", "toward_cam", "away_cam", "none"),
}
# Bí danh ASCII cho hướng màn hình (gõ tay/CLI) → giá trị chuẩn.
SCREEN_DIR_ALIASES = {"L>R": "L→R", "L->R": "L→R", "LR": "L→R", "R>L": "R→L", "R->L": "R→L", "RL": "R→L"}
CUT_KINDS = ("cut", "match_action", "match_graphic", "j_cut", "l_cut", "smash", "cutaway", "dissolve")
CUT_ENUMS: dict[str, tuple[str, ...]] = {
    "axis_side": ("A", "B"),
    "cut_in": CUT_KINDS,
    "cut_out": CUT_KINDS,
}
CUSTOM_ENUMS = CUT_ENUMS  # tên cũ (W1)
FIELD_ENUMS: dict[str, tuple[str, ...]] = {
    "prompt_mode": ("verbatim", "synced"),
    "engine": ("ai_video", "still_motion", "code_anim"),
    "link": ("cut", "fade", "continue"),
    "grok_audio": ("keep", "mute", "ambient"),
    "review": ("pending", "approved", "rejected", "skipped"),
}
# Không sửa qua clip.update: thứ tự/tập (clip.move), take đang chọn (take.select).
UPDATE_FORBIDDEN = {
    "rank": "Dùng clip.move để đổi thứ tự.",
    "sequence_id": "Dùng clip.move / sequence.duplicate; clip không chuyển giữa các tập bằng update.",
    "image_take_id": "Dùng take.select để chọn take.",
    "video_take_id": "Dùng take.select để chọn take.",
}
MAX_BULK = 1000
LIST_MAX = 500


def _enum_error(field: str, value: Any, allowed: frozenset[str], loc: str = "values") -> dict:
    return {"loc": [loc, field], "msg": f"Giá trị '{value}' của {field} không hợp lệ; chọn một trong: "
            f"{', '.join(sorted(allowed))} (hoặc để trống).", "type": "enum"}


def normalize_fields(values: dict[str, Any]) -> dict[str, Any]:
    """Kiểm enum (shot + engine/link/… + axis_side/cut_in/cut_out); chuẩn hoá bí danh screen_dir. `custom` kiểu cũ
    chứa axis_side/cut_in/cut_out → chuyển lên cột."""
    out = dict(values)
    errors = []
    custom = out.get("custom")
    if isinstance(custom, dict) and any(k in custom for k in CUT_ENUMS):
        custom = dict(custom)
        for field in CUT_ENUMS:
            if field in custom:
                v = custom.pop(field)
                out.setdefault(field, "" if v is None else v)
        out["custom"] = custom
    if isinstance(out.get("screen_dir"), str):
        out["screen_dir"] = SCREEN_DIR_ALIASES.get(out["screen_dir"].strip(), out["screen_dir"].strip())
    for field, allowed in SHOT_ENUMS.items():
        v = out.get(field)
        if isinstance(v, str) and v != "" and v not in allowed:
            errors.append(_enum_error(field, v, allowed))
    for field, allowed in FIELD_ENUMS.items():
        v = out.get(field)
        if v is not None and isinstance(v, str) and v not in allowed:
            errors.append({"loc": ["values", field], "msg": f"Giá trị '{v}' của {field} không hợp lệ; chọn một trong:"
                           f" {', '.join(sorted(allowed))}.", "type": "enum"})
    for field, allowed in CUT_ENUMS.items():
        v = out.get(field)
        if v not in (None, "") and v not in allowed:
            errors.append(_enum_error(field, v, allowed))
    if isinstance(out.get("duration_ms"), int) and not isinstance(out["duration_ms"], bool) and out["duration_ms"] <= 0:
        errors.append({"loc": ["values", "duration_ms"], "msg": "Thời lượng phải lớn hơn 0.", "type": "range"})
    if errors:
        raise ValidationFailed("Giá trị không hợp lệ.", hint="Xem `extra.errors`.", extra={"errors": errors})
    return out


# --- Đọc ----------------------------------------------------------------------------------------------------------


def get_sequence(s: Session, sequence_id: int) -> Sequence:
    return entities.get(s, "sequence", sequence_id)


def get_clip(s: Session, clip_id: int) -> Clip:
    return entities.get(s, "clip", clip_id)


def clip_out(c: Clip) -> dict[str, Any]:
    return entities.to_dict(c)


def get(ctx: OpContext, clip_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        c = get_clip(s, clip_id)
        out = clip_out(c)
        out["dialogue"] = [entities.to_dict(d) for d in s.scalars(
            select(DialogueLine).where(DialogueLine.clip_id == c.id, DialogueLine.deleted_at.is_(None))
            .order_by(DialogueLine.rank, DialogueLine.id))]
        return out


def _bad_cursor() -> AppError:
    return AppError("invalid_cursor", "Cursor phân trang không hợp lệ.",
                    hint="Dùng đúng `next_cursor` của trang trước, hoặc bỏ trống để lấy trang đầu.", status=422)


def filtered_query(sequence_id: int, flt: dict[str, Any] | None):
    """SELECT clip của tập (chưa xoá) theo bộ lọc `{flags?, review?, scene_id?, asset_id?, variant_id?, ids?}`.

    `flags`: danh sách cờ — clip có ít nhất một cờ (true/khác rỗng). `review`: chuỗi hoặc danh sách. `scene_id`: id
    hoặc null (clip không thuộc cảnh nào). `asset_id` (+ `variant_id`): clip dùng tài sản (look) đó.
    """
    flt = dict(flt or {})
    q = select(Clip).where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None))
    flags = flt.get("flags")
    if flags:
        if isinstance(flags, str):
            flags = [flags]
        conds = []
        for i, f in enumerate(flags):
            path = f"$.{f}" if str(f).replace("_", "").isalnum() else None
            if path is None:
                raise ValidationFailed(f"Tên cờ không hợp lệ: {f!r}.")
            conds.append(text(f"coalesce(json_extract(clip.flags, :fp{i}), 0) NOT IN (0, '', 'false')")
                         .bindparams(**{f"fp{i}": path}))
        q = q.where(or_(*conds))
    review = flt.get("review")
    if review:
        q = q.where(Clip.review.in_([review] if isinstance(review, str) else list(review)))
    if "scene_id" in flt:
        sid = flt["scene_id"]
        q = q.where(Clip.scene_id.is_(None) if sid is None else Clip.scene_id == sid)
    if flt.get("asset_id") is not None:
        cond = "json_extract(value, '$.asset_id') = :aid"
        params: dict[str, Any] = {"aid": int(flt["asset_id"])}
        if flt.get("variant_id") is not None:
            cond += " AND json_extract(value, '$.variant_id') = :vid"
            params["vid"] = int(flt["variant_id"])
        q = q.where(text(f"EXISTS (SELECT 1 FROM json_each(clip.assets) WHERE {cond})").bindparams(**params))
    if flt.get("ids"):
        q = q.where(Clip.id.in_([int(i) for i in flt["ids"]]))
    if flt.get("has_take"):
        q = q.where(or_(Clip.image_take_id.is_not(None), Clip.video_take_id.is_not(None)))
    return q


# Hàng duyệt "take chờ duyệt": clip chưa duyệt đã có take đang chọn. Dùng chung cho đếm `takes_pending`
# (project.get, sequence.list) và `review.queue(filter={review: pending, has_take: true})` → số khớp nhau.
PENDING_FILTER: dict[str, Any] = {"review": ["pending"], "has_take": True}


def count_pending(s: Session, sequence_ids: list[int]) -> dict[int, int]:
    """Số clip chờ duyệt (có take) theo tập — cùng điều kiện với `PENDING_FILTER`."""
    out = dict.fromkeys(sequence_ids, 0)
    if not sequence_ids:
        return out
    for sid, n in s.execute(
        select(Clip.sequence_id, func.count()).where(
            Clip.sequence_id.in_(sequence_ids), Clip.deleted_at.is_(None), Clip.review == "pending",
            or_(Clip.image_take_id.is_not(None), Clip.video_take_id.is_not(None)))
        .group_by(Clip.sequence_id)
    ):
        out[sid] = int(n)
    return out


def count_flags(s: Session, sequence_ids: list[int]) -> dict[int, dict[str, int]]:
    """Số clip có cờ `stale` (stale_image/stale_video/stale_*), `drift`, `failed` theo tập.

    `stale` = clip có bất kỳ cờ cũ nào; `stale_image` / `stale_video` tách riêng ảnh cũ và video cũ."""
    out = {i: {"stale": 0, "stale_image": 0, "stale_video": 0, "drift": 0, "failed": 0} for i in sequence_ids}
    if not sequence_ids:
        return out
    rows = s.execute(select(Clip.sequence_id, Clip.flags).where(
        Clip.sequence_id.in_(sequence_ids), Clip.deleted_at.is_(None), Clip.flags.is_not(None),
        text("clip.flags NOT IN ('{}', '', 'null')")))
    for sid, flags in rows:
        flags = flags or {}
        if any(k.startswith("stale") and v for k, v in flags.items()):
            out[sid]["stale"] += 1
        if flags.get("stale_image"):
            out[sid]["stale_image"] += 1
        if flags.get("stale_video"):
            out[sid]["stale_video"] += 1
        if flags.get("drift"):
            out[sid]["drift"] += 1
        if flags.get("failed"):
            out[sid]["failed"] += 1
    return out


def selected_media(s: Session, rows: list[Clip]) -> dict[int, dict[str, Any]]:
    """Media của take đang chọn cho từng clip (một truy vấn): `image_media_id`, `video_media_id`,
    `thumb_media_id` (ảnh, không thì video — có thumbnail ngay: `/media/<id>/thumb`; null nếu file đã vào thùng rác),
    `source_duration_ms` (độ dài video provider đang chọn)."""
    from sagareel.db.models import Media

    take_ids = {t for c in rows for t in (c.image_take_id, c.video_take_id) if t}
    info: dict[int, tuple[int, int | None, bool]] = {}
    if take_ids:
        for tid, mid, dur, trashed in s.execute(
            select(Take.id, Media.id, Media.duration_ms, Media.trashed_at).join(Media, Media.id == Take.media_id)
            .where(Take.id.in_(take_ids))
        ):
            info[tid] = (mid, dur, trashed is not None)
    out: dict[int, dict[str, Any]] = {}
    for c in rows:
        img = info.get(c.image_take_id) if c.image_take_id else None
        vid = info.get(c.video_take_id) if c.video_take_id else None
        thumb = next((x[0] for x in (img, vid) if x is not None and not x[2]), None)
        out[c.id] = {"image_media_id": img[0] if img else None, "video_media_id": vid[0] if vid else None,
                     "thumb_media_id": thumb, "source_duration_ms": vid[1] if vid else None}
    return out


def list_page(s: Session, sequence_id: int, cursor: str | None = None, limit: int = 100,
              flt: dict[str, Any] | None = None) -> dict[str, Any]:
    get_sequence(s, sequence_id)
    limit = max(1, min(int(limit), LIST_MAX))
    q = filtered_query(sequence_id, flt)
    if cursor:
        r, sep, i = cursor.rpartition(":")
        if not sep or not i.isdigit():
            raise _bad_cursor()
        q = q.where(or_(Clip.rank > r, and_(Clip.rank == r, Clip.id > int(i))))
    rows = s.scalars(q.order_by(Clip.rank, Clip.id).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    media_of = selected_media(s, rows)
    return {"items": [{**clip_out(c), **media_of.get(c.id, {})} for c in rows],
            "next_cursor": f"{rows[-1].rank}:{rows[-1].id}" if more else None}


def list_clips(ctx: OpContext, sequence_id: int, cursor: str | None = None, limit: int = 100,
               flt: dict[str, Any] | None = None) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        return list_page(s, sequence_id, cursor, limit, flt)


def ordered(s: Session, sequence_id: int) -> list[Clip]:
    return list(s.scalars(select(Clip).where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None))
                          .order_by(Clip.rank, Clip.id)))


# --- Thứ tự (rank) ------------------------------------------------------------------------------------------------


def _rank_of(s: Session, clip_id: int, sequence_id: int) -> str:
    c = get_clip(s, clip_id)
    if c.sequence_id != sequence_id:
        raise ValidationFailed(f"Clip #{clip_id} không thuộc tập #{sequence_id}.")
    return c.rank


def _neighbors(s: Session, sequence_id: int, after_clip_id: int | None, exclude: set[int]) -> tuple[str | None,
                                                                                                    str | None]:
    """(rank trước, rank sau) của vị trí chèn. `after_clip_id`: None = cuối tập, 0 = đầu tập."""
    alive = [Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None)]
    if exclude:
        alive.append(Clip.id.not_in(exclude))
    if after_clip_id is None:
        return s.scalar(select(func.max(Clip.rank)).where(*alive)), None
    if after_clip_id == 0:
        return None, s.scalar(select(func.min(Clip.rank)).where(*alive))
    if after_clip_id in exclude:
        raise ValidationFailed("after_clip_id không được là một trong các clip đang di chuyển.")
    prev = _rank_of(s, after_clip_id, sequence_id)
    # Clip liền sau theo (rank, id): rank trùng với `prev` → `new_ranks` thấy prev >= nxt và đánh lại cả tập.
    nxt = s.scalar(select(Clip.rank).where(
        *alive, or_(Clip.rank > prev, and_(Clip.rank == prev, Clip.id > after_clip_id)))
        .order_by(Clip.rank, Clip.id).limit(1))
    return prev, nxt


def _chain(prev: str | None, nxt: str | None, n: int) -> list[str]:
    if nxt is None:
        return rank.n_after(prev, n)
    out = []
    for _ in range(n):
        prev = rank.between(prev, nxt)
        out.append(prev)
    return out


def _rebalance(s: Session, sequence_id: int) -> None:
    """Gán lại rank ngắn cho cả tập (chỉ khi rank trùng nhau làm không chèn được — đường hiếm)."""
    rows = ordered(s, sequence_id)
    for c, k in zip(rows, rank.rebalance([c.rank for c in rows]), strict=True):
        if c.rank != k:
            c.rank = k
    s.flush()


def new_ranks(s: Session, sequence_id: int, after_clip_id: int | None, n: int,
              exclude: set[int] | None = None) -> list[str]:
    exclude = exclude or set()
    prev, nxt = _neighbors(s, sequence_id, after_clip_id, exclude)
    try:
        if prev is not None and nxt is not None and prev >= nxt:
            raise ValueError("rank trùng")
        return _chain(prev, nxt, n)
    except ValueError:
        _rebalance(s, sequence_id)
        prev, nxt = _neighbors(s, sequence_id, after_clip_id, exclude)
        return _chain(prev, nxt, n)


# --- Mã clip ------------------------------------------------------------------------------------------------------


class KeyAllocator:
    """Cấp mã `c0001`, `c0002`… chưa dùng trong tập (tránh cả clip đã xoá mềm để hoàn tác xoá không vướng). Mã người
    dùng đặt chỉ cần khác các clip chưa xoá (unique một phần, migration 0005)."""

    def __init__(self, s: Session, sequence_id: int) -> None:
        rows = s.execute(select(Clip.key, Clip.deleted_at).where(Clip.sequence_id == sequence_id)).all()
        self.used = {k for k, _ in rows}
        self.alive = {k for k, d in rows if d is None}
        self.n = len(self.used)

    def take(self, wanted: str | None = None) -> str:
        if wanted:
            if wanted in self.alive:
                raise AppError("key_taken", f"Mã clip '{wanted}' đã có trong tập.", hint="Chọn mã khác hoặc bỏ trống.",
                               status=409)
            self.used.add(wanted)
            self.alive.add(wanted)
            return wanted
        while True:
            self.n += 1
            key = f"c{self.n:04d}"
            if key not in self.used:
                self.used.add(key)
                self.alive.add(key)
                return key


# --- Ghi ----------------------------------------------------------------------------------------------------------

_CREATE_SKIP = {"id", "created_at", "updated_at", "deleted_at", "sequence_id", "rank", "image_take_id",
                "video_take_id"}


def _clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    fields = {k: v for k, v in (fields or {}).items()}
    bad = sorted(k for k in fields if k in _CREATE_SKIP)
    if bad:
        raise AppError("readonly_field", f"Không đặt được trường khi tạo clip: {', '.join(bad)}.", status=422,
                       extra={"fields": bad})
    if not fields:
        return {}
    fields = normalize_fields(fields)
    return entities.check_values("clip", fields)


def _check_scene(s: Session, scene_id: int | None, sequence_id: int) -> None:
    if scene_id is None:
        return
    sc = entities.get(s, "scene", scene_id)
    if sc.sequence_id != sequence_id:
        raise ValidationFailed(f"Cảnh #{scene_id} không thuộc tập #{sequence_id}.")


def bulk_create(ctx: OpContext, sequence_id: int, clips: list[dict[str, Any]], after_clip_id: int | None = None,
                scene_id: int | None = None) -> list[dict[str, Any]]:
    """Tạo nhiều clip liền nhau sau `after_clip_id` (None = cuối tập, 0 = đầu tập) — một change set."""
    if not clips:
        raise ValidationFailed("Cần ít nhất một clip.")
    if len(clips) > MAX_BULK:
        raise ValidationFailed(f"Mỗi lần tạo tối đa {MAX_BULK} clip.")
    cleaned = [_clean_fields(c) for c in clips]
    u = entities.uow(ctx, f"Tạo {len(clips)} clip", "clip.bulk_create")
    try:
        with u as s:
            seq = get_sequence(s, sequence_id)
            u.project_id = seq.project_id
            keys = KeyAllocator(s, sequence_id)
            ranks = new_ranks(s, sequence_id, after_clip_id, len(cleaned))
            rows = []
            for fields, r in zip(cleaned, ranks, strict=True):
                fields = dict(fields)
                sid = fields.pop("scene_id", scene_id)
                _check_scene(s, sid, sequence_id)
                key = keys.take(fields.pop("key", None))
                c = Clip(sequence_id=sequence_id, rank=r, key=key, scene_id=sid, **fields)
                s.add(c)
                rows.append(c)
            s.flush()
            out = [clip_out(c) for c in rows]
    except IntegrityError as e:
        raise entities.integrity_error("clip", e) from None
    return out


def create(ctx: OpContext, sequence_id: int, fields: dict[str, Any] | None = None, after_clip_id: int | None = None,
           scene_id: int | None = None) -> dict[str, Any]:
    return bulk_create(ctx, sequence_id, [fields or {}], after_clip_id, scene_id)[0]


def _changed(obj: Any, values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if entities._plain(getattr(obj, k)) != v}


def _check_update(values: dict[str, Any]) -> dict[str, Any]:
    bad = {k: UPDATE_FORBIDDEN[k] for k in values if k in UPDATE_FORBIDDEN}
    if bad:
        raise AppError("readonly_field", f"Không sửa được trường bằng clip.update: {', '.join(sorted(bad))}.",
                       hint=" ".join(sorted(set(bad.values()))), status=422, extra={"fields": sorted(bad)})
    return entities.check_values("clip", normalize_fields(values))


def _apply_update(s: Session, c: Clip, clean: dict[str, Any]) -> list[str]:
    changed = _changed(c, clean)
    if "scene_id" in changed:
        _check_scene(s, changed["scene_id"], c.sequence_id)
    for k, v in changed.items():
        setattr(c, k, v)
    # Người dùng tự sửa `flags` trong cùng lần sửa → tôn trọng giá trị đó, không tự gắn cờ.
    applied = set() if "flags" in changed else staleness.apply(c, staleness.flags_for("clip", changed))
    return sorted(applied)


def update(ctx: OpContext, clip_id: int, values: dict[str, Any],
           expected_updated_at: datetime | None = None) -> dict[str, Any]:
    """Sửa clip (một change set) + gắn cờ làm-cũ theo `defaults/staleness.yaml` cho các trường thật sự đổi."""
    clean = _check_update(values)
    u = entities.uow(ctx, f"Sửa clip #{clip_id}", "clip.update")
    try:
        with u as s:
            c = get_clip(s, clip_id)
            entities.check_fresh("clip", c, expected_updated_at)
            u.project_id = entities.project_id_of(s, c)
            stale = _apply_update(s, c, clean)
            s.flush()
            out = clip_out(c)
    except IntegrityError as e:
        raise entities.integrity_error("clip", e) from None
    out["stale_applied"] = stale
    return out


def bulk_update(ctx: OpContext, ids: list[int], values: dict[str, Any]) -> dict[str, Any]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ValidationFailed("Cần ít nhất một id clip.")
    if len(ids) > MAX_BULK:
        raise ValidationFailed(f"Mỗi lần sửa tối đa {MAX_BULK} clip.")
    clean = _check_update(values)
    u = entities.uow(ctx, f"Sửa {len(ids)} clip", "clip.bulk_update")
    try:
        with u as s:
            rows = [get_clip(s, i) for i in ids]
            projects = {entities.project_id_of(s, c) for c in rows}
            u.project_id = projects.pop() if len(projects) == 1 else None
            stale = {c.id: _apply_update(s, c, clean) for c in rows}
            s.flush()
    except IntegrityError as e:
        raise entities.integrity_error("clip", e) from None
    return {"updated": ids, "stale_applied": {str(k): v for k, v in stale.items() if v}}


def move(ctx: OpContext, ids: list[int], after_clip_id: int | None = None, scene_id: int | None = None) -> dict:
    """Đưa các clip `ids` (giữ thứ tự đã cho) tới sau `after_clip_id` (None = cuối, 0 = đầu). Chỉ ghi rank của các
    clip được di chuyển. `scene_id`: None = giữ nguyên, 0 = bỏ khỏi cảnh, id = chuyển vào cảnh đó."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ValidationFailed("Cần ít nhất một id clip.")
    if len(ids) > MAX_BULK:
        raise ValidationFailed(f"Mỗi lần di chuyển tối đa {MAX_BULK} clip.")
    u = entities.uow(ctx, f"Di chuyển {len(ids)} clip", "clip.move")
    with u as s:
        rows = [get_clip(s, i) for i in ids]
        seqs = {c.sequence_id for c in rows}
        if len(seqs) != 1:
            raise ValidationFailed("Các clip di chuyển phải cùng một tập.")
        sequence_id = seqs.pop()
        u.project_id = entities.project_id_of(s, rows[0])
        target_scene = None
        if scene_id is not None and scene_id != 0:
            _check_scene(s, scene_id, sequence_id)
            target_scene = scene_id
        ranks = new_ranks(s, sequence_id, after_clip_id, len(rows), exclude=set(ids))
        for c, r in zip(rows, ranks, strict=True):
            c.rank = r
            if scene_id is not None and c.scene_id != target_scene:
                c.scene_id = target_scene
        s.flush()
        out = [{"id": c.id, "rank": c.rank, "scene_id": c.scene_id} for c in rows]
    return {"items": out}


_COPY_SKIP = {"id", "created_at", "updated_at", "deleted_at"}


def copy_values(obj: Any, **overrides: Any) -> dict[str, Any]:
    from sqlalchemy import inspect

    vals = {a.key: entities._plain(getattr(obj, a.key)) for a in inspect(obj).mapper.column_attrs
            if a.key not in _COPY_SKIP}
    vals.update(overrides)
    return vals


def split(ctx: OpContext, clip_id: int, at_ms: int) -> dict[str, Any]:
    """Tách clip tại `at_ms` (tính từ đầu clip): clip gốc giữ phần đầu, clip mới phần sau. Cả hai trỏ cùng take
    (không tạo/xoá take) và cắt bằng `trim_in_ms/trim_out_ms`; lời thoại có `offset_ms ≥ at_ms` sang clip mới."""
    u = entities.uow(ctx, f"Tách clip #{clip_id}", "clip.split")
    with u as s:
        c = get_clip(s, clip_id)
        if not (0 < at_ms < c.duration_ms):
            raise ValidationFailed(f"Điểm tách phải nằm trong (0, {c.duration_ms}) ms.",
                                   extra={"errors": [{"loc": ["at_ms"], "msg": "ngoài khoảng", "type": "range"}]})
        u.project_id = entities.project_id_of(s, c)
        orig = c.duration_ms
        r = new_ranks(s, c.sequence_id, c.id, 1)[0]
        b = Clip(**copy_values(c, key=KeyAllocator(s, c.sequence_id).take(), rank=r, duration_ms=orig - at_ms,
                               trim_in_ms=c.trim_in_ms + at_ms, link="cut", review="pending", flags={}))
        c.trim_out_ms = c.trim_out_ms + (orig - at_ms)
        c.duration_ms = at_ms
        s.add(b)
        s.flush()
        for d in s.scalars(select(DialogueLine).where(DialogueLine.clip_id == c.id, DialogueLine.deleted_at.is_(None),
                                                      DialogueLine.offset_ms >= at_ms)):
            d.clip_id = b.id
            d.offset_ms = d.offset_ms - at_ms
        s.flush()
        out = {"first": clip_out(c), "second": clip_out(b)}
    return out


def copy_dialogue(s: Session, src_clip_id: int, dst_clip_id: int) -> None:
    for d in s.scalars(select(DialogueLine).where(DialogueLine.clip_id == src_clip_id,
                                                  DialogueLine.deleted_at.is_(None)).order_by(DialogueLine.id)):
        s.add(DialogueLine(**copy_values(d, clip_id=dst_clip_id, audio_take_id=None)))


def duplicate(ctx: OpContext, clip_id: int, after_clip_id: int | None = None) -> dict[str, Any]:
    """Bản sao ngay sau clip gốc (hoặc sau `after_clip_id`): giữ prompt/tài sản/shot/lời thoại; không mang take."""
    u = entities.uow(ctx, f"Nhân bản clip #{clip_id}", "clip.duplicate")
    with u as s:
        c = get_clip(s, clip_id)
        u.project_id = entities.project_id_of(s, c)
        r = new_ranks(s, c.sequence_id, c.id if after_clip_id is None else after_clip_id, 1)[0]
        b = Clip(**copy_values(c, key=KeyAllocator(s, c.sequence_id).take(), rank=r, image_take_id=None,
                               video_take_id=None, flags={}, review="pending"))
        s.add(b)
        s.flush()
        copy_dialogue(s, c.id, b.id)
        s.flush()
        out = clip_out(b)
    return out


# --- Xoá hai bước ---------------------------------------------------------------------------------------------------


def _impact_extra(s: Session, rows: list[Any]) -> dict[str, Any]:
    ids = [r.id for r in rows]
    takes = s.scalar(select(func.count()).select_from(Take).where(Take.owner_type == "clip", Take.owner_id.in_(ids)))
    lines = s.scalar(select(func.count()).select_from(DialogueLine).where(DialogueLine.clip_id.in_(ids),
                                                                         DialogueLine.deleted_at.is_(None)))
    anchored = s.scalar(select(func.count()).select_from(TimelineItem).where(TimelineItem.anchor_clip_id.in_(ids),
                                                                            TimelineItem.deleted_at.is_(None)))
    return {"takes_kept": takes, "dialogue_lines": lines, "timeline_items_reanchored": anchored}


entities.IMPACT_EXTRA["clip"] = _impact_extra


def delete_preview(ctx: OpContext, ids: list[int]) -> dict[str, Any]:
    return entities.soft_delete_preview(ctx, "clip", ids)


def delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    """Xoá mềm clip (+ lời thoại của chúng); mục timeline neo vào clip bị xoá chuyển neo sang clip kế tiếp còn lại
    (spec §23.1). Take không bị xoá. Một change set, hoàn tác được."""
    type_, ids, inner = entities.parse_token(confirm_token)
    if type_ != "clip":
        raise ConfirmRequired("confirm_token này không dành cho clip.",
                              hint="Gọi clip.delete_preview để lấy token mới.")
    u = entities.uow(ctx, f"Xoá {len(ids)} clip", "clip.delete")
    with u as s:
        rows = [get_clip(s, i) for i in ids]
        confirm.verify(s, entities.delete_op("clip"), ids, entities.impact(s, "clip", rows), inner)
        projects = {entities.project_id_of(s, r) for r in rows}
        u.project_id = projects.pop() if len(projects) == 1 else None
        now = utcnow()
        # Neo timeline (nhạc/SFX/title) do Task D quản lý: clip sau còn lại → clip trước (+ độ dài) → bỏ neo;
        # `end_anchor` ưu tiên clip trước.
        from sagareel.services.audio import timeline

        timeline.reanchor_for_deleted_clips(s, ids)
        for r in rows:
            r.deleted_at = now
            for d in s.scalars(select(DialogueLine).where(DialogueLine.clip_id == r.id,
                                                          DialogueLine.deleted_at.is_(None))):
                d.deleted_at = now
    return {"type": "clip", "deleted": ids}


def require_clip_ids(s: Session, ids: list[int]) -> list[Clip]:
    rows = []
    for i in ids:
        c = s.get(Clip, i)
        if c is None or c.deleted_at is not None:
            raise NotFound(f"Không tìm thấy clip #{i}.")
        rows.append(c)
    return rows


__all__ = [
    "CUSTOM_ENUMS", "CUT_ENUMS", "FIELD_ENUMS", "SHOT_ENUMS", "KeyAllocator", "bulk_create", "bulk_update", "create", "delete",
    "delete_preview", "duplicate", "filtered_query", "get", "list_clips", "list_page", "move", "new_ranks",
    "normalize_fields", "ordered", "split", "update",
]
