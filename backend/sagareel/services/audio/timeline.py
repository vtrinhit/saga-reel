"""Kho âm thanh (`sound`) + mục trên track nhạc/SFX/title neo vào clip (`timeline_item`, spec §23.1).

- Thời điểm tuyệt đối không lưu: item neo `anchor_clip_id + offset_ms` (null = đầu sequence), kết thúc theo
  `duration_ms` hoặc `end_anchor` (`{"clip_id": …}` = hết clip đó, `{"sequence_end": true}`), Task E tính layout.
- `reanchor_for_deleted_clips(session, clip_ids)`: Task B gọi **trong UoW xoá clip của mình** (cùng session, chỉ flush)
  → item neo vào clip bị xoá chuyển sang clip kế tiếp còn sống (giữ `offset_ms`: trong timeline magnetic, clip kế tiếp
  chiếm đúng chỗ bắt đầu của clip bị xoá); không có clip kế → clip liền trước (`offset_ms += duration_ms` của nó); không
  còn clip nào → neo đầu sequence (`anchor_clip_id = None`). `end_anchor.clip_id` → clip liền trước (hết clip bị xoá =
  hết clip trước nó), không có thì clip kế, không còn clip → `{"sequence_end": true}`. Thay đổi nằm trong change set
  của Task B.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ValidationFailed
from sagareel.db.models import Clip, Media, Sound, TimelineItem
from sagareel.render.filters import check_title_params
from sagareel.services import entities

SOUND_KINDS = ("music", "sfx", "ambience")
TRACKS = ("music", "sfx", "title")
ITEM_FIELDS = ("track", "anchor_clip_id", "offset_ms", "end_anchor", "duration_ms", "media_id", "sound_id", "params")


# --- Sound -----------------------------------------------------------------------------------------------------------


def sound_add(ctx: OpContext, media_id: int, kind: str, title: str, source: str, license: str,
              attribution: str = "", *, project_id: int | None = None, tags: list[str] | None = None) -> dict:
    if kind not in SOUND_KINDS:
        raise ValidationFailed(f"kind phải là một trong {', '.join(SOUND_KINDS)}.")
    if not source.strip() or not license.strip():
        raise ValidationFailed("Âm thanh bắt buộc ghi nguồn (source) và giấy phép (license).",
                               hint="Không rõ giấy phép: ghi \"unknown\" — sẽ bị cảnh báo khi xuất.")
    u = entities.uow(ctx, f"Thêm âm thanh {title}", "sound.add")
    with u as s:
        m = s.get(Media, media_id)
        if m is None:
            raise AppError("not_found", f"Không tìm thấy media #{media_id}.", status=404)
        if m.kind not in ("audio", "video"):
            raise AppError("media_kind_mismatch", f"Media #{media_id} là {m.kind}, cần audio.", status=422)
        if project_id is not None:
            entities.get(s, "project", project_id)
        u.project_id = project_id
        row = Sound(project_id=project_id, media_id=media_id, kind=kind, title=title, tags=list(tags or []),
                    source=source, license=license, attribution=attribution)
        s.add(row)
        s.flush()
        return entities.to_dict(row)


def sound_list(ctx: OpContext, project_id: int | None = None, kind: str | None = None, q: str | None = None,
               include_global: bool = True) -> list[dict]:
    query = select(Sound).where(Sound.deleted_at.is_(None))
    if project_id is None:
        query = query.where(Sound.project_id.is_(None))
    elif include_global:
        query = query.where(or_(Sound.project_id == project_id, Sound.project_id.is_(None)))
    else:
        query = query.where(Sound.project_id == project_id)
    if kind:
        query = query.where(Sound.kind == kind)
    if q:
        query = query.where(Sound.title.ilike(f"%{q}%"))
    with ctx.services.db.session() as s:
        return [entities.to_dict(x) for x in s.scalars(query.order_by(Sound.id))]


# --- Timeline item ---------------------------------------------------------------------------------------------------


def _check_item(s: Session, sequence_id: int, v: dict[str, Any]) -> None:
    errors = []
    if v.get("track") not in TRACKS:
        errors.append({"loc": ["track"], "msg": f"track phải là một trong {', '.join(TRACKS)}.", "type": "enum"})
    if int(v.get("offset_ms") or 0) < 0:
        errors.append({"loc": ["offset_ms"], "msg": "offset_ms phải ≥ 0.", "type": "value_error"})
    if v.get("duration_ms") is not None and int(v["duration_ms"]) <= 0:
        errors.append({"loc": ["duration_ms"], "msg": "duration_ms phải > 0.", "type": "value_error"})
    for field in ("anchor_clip_id",):
        cid = v.get(field)
        if cid is not None:
            c = s.get(Clip, cid)
            if c is None or c.deleted_at is not None or c.sequence_id != sequence_id:
                errors.append({"loc": [field], "msg": f"Clip #{cid} không thuộc tập này.", "type": "reference"})
    end = v.get("end_anchor")
    if end is not None:
        if not isinstance(end, dict) or not (("clip_id" in end) ^ bool(end.get("sequence_end"))):
            errors.append({"loc": ["end_anchor"], "msg": "end_anchor là {\"clip_id\": id} hoặc "
                           "{\"sequence_end\": true}.", "type": "value_error"})
        elif "clip_id" in end:
            c = s.get(Clip, end["clip_id"])
            if c is None or c.deleted_at is not None or c.sequence_id != sequence_id:
                errors.append({"loc": ["end_anchor", "clip_id"], "msg": "Clip kết thúc không thuộc tập này.",
                               "type": "reference"})
    if v.get("track") in ("music", "sfx") and v.get("sound_id") is None and v.get("media_id") is None:
        errors.append({"loc": ["sound_id"], "msg": "Track nhạc/SFX cần sound_id hoặc media_id.", "type": "missing"})
    if v.get("track") == "title" and not str((v.get("params") or {}).get("text", "")).strip():
        errors.append({"loc": ["params", "text"], "msg": "Title cần params.text.", "type": "missing"})
    for msg in check_title_params(v.get("params") or {}):  # màu/cỡ chữ đi vào filtergraph drawtext
        errors.append({"loc": ["params"], "msg": msg, "type": "value_error"})
    if v.get("sound_id") is not None:
        snd = s.get(Sound, v["sound_id"])
        if snd is None or snd.deleted_at is not None:
            errors.append({"loc": ["sound_id"], "msg": f"Không có âm thanh #{v['sound_id']}.", "type": "reference"})
    if errors:
        raise ValidationFailed("Mục timeline không hợp lệ.", hint="Xem `extra.errors`.", extra={"errors": errors})


def _patch_validator(s: Session, obj: TimelineItem, values: dict[str, Any]) -> dict[str, Any]:
    """`entity.patch` trên mục timeline: tham số title (màu, tỉ lệ) phải an toàn cho filtergraph."""
    if "params" in values:
        errs = check_title_params(values["params"] or {})
        if errs:
            raise ValidationFailed("Mục timeline không hợp lệ.", extra={"errors": [
                {"loc": ["values", "params"], "msg": m, "type": "value_error"} for m in errs]})
    return values


entities.PATCH_VALIDATORS["timeline_item"] = _patch_validator


def timeline_add(ctx: OpContext, sequence_id: int, track: str, **values: Any) -> dict:
    v = {"track": track, "offset_ms": 0, "params": {}, **{k: x for k, x in values.items() if x is not None}}
    u = entities.uow(ctx, f"Thêm mục {track} vào timeline", "timeline.add")
    with u as s:
        seq = entities.get(s, "sequence", sequence_id)
        u.project_id = seq.project_id
        _check_item(s, sequence_id, v)
        if v.get("sound_id") is not None and v.get("media_id") is None:
            v["media_id"] = s.get(Sound, v["sound_id"]).media_id
        row = TimelineItem(sequence_id=sequence_id, **v)
        s.add(row)
        s.flush()
        return entities.to_dict(row)


def timeline_update(ctx: OpContext, id_: int, values: dict[str, Any],
                    expected_updated_at: datetime | None = None) -> dict:
    unknown = set(values) - set(ITEM_FIELDS)
    if unknown:
        raise AppError("unknown_field", f"Mục timeline không sửa được trường: {', '.join(sorted(unknown))}.",
                       status=422)
    with ctx.services.db.session() as s:
        row = entities.get(s, "timeline_item", id_)
        merged = {k: getattr(row, k) for k in ITEM_FIELDS}
        merged.update(values)
        _check_item(s, row.sequence_id, merged)
        if "sound_id" in values and values["sound_id"] is not None and "media_id" not in values:
            values = {**values, "media_id": s.get(Sound, values["sound_id"]).media_id}
    return entities.patch(ctx, "timeline_item", id_, values, expected_updated_at)


def timeline_remove_preview(ctx: OpContext, ids: list[int]) -> dict:
    """Tác động + `confirm_token` (10 phút) cho `timeline_remove`."""
    return entities.soft_delete_preview(ctx, "timeline_item", ids)


def timeline_remove(ctx: OpContext, confirm_token: str | None) -> dict:
    """Xoá mềm các mục trong token (một change set, hoàn tác được bằng history.undo)."""
    out = entities.soft_delete(ctx, confirm_token, expect_type="timeline_item")
    return {"removed": out["deleted"]}


def timeline_list(ctx: OpContext, sequence_id: int, track: str | None = None) -> list[dict]:
    with ctx.services.db.session() as s:
        entities.get(s, "sequence", sequence_id)
        q = select(TimelineItem).where(TimelineItem.sequence_id == sequence_id, TimelineItem.deleted_at.is_(None))
        if track:
            q = q.where(TimelineItem.track == track)
        return [entities.to_dict(x) for x in s.scalars(q.order_by(TimelineItem.track, TimelineItem.id))]


def reanchor_for_deleted_clips(session: Session, clip_ids: list[int] | set[int]) -> list[int]:
    """Chuyển neo của mục timeline khỏi các clip `clip_ids` (đang/đã bị xoá). Chỉ flush trên `session` của người gọi
    (không mở UoW, không commit). Trả id các mục đã đổi."""
    gone = {int(i) for i in clip_ids}
    if not gone:
        return []
    clips = [session.get(Clip, i) for i in gone]
    seq_ids = {c.sequence_id for c in clips if c is not None}
    changed: list[int] = []
    for seq_id in seq_ids:
        order = list(session.scalars(select(Clip).where(Clip.sequence_id == seq_id).order_by(Clip.rank, Clip.id)))
        alive = [c for c in order if c.deleted_at is None and c.id not in gone]

        def neighbour(clip_id: int, order: list[Clip] = order,
                      alive: list[Clip] = alive) -> tuple[Clip | None, Clip | None]:
            c = next((x for x in order if x.id == clip_id), None)
            if c is None:
                return None, None
            key = (c.rank, c.id)
            nxt = next((x for x in alive if (x.rank, x.id) > key), None)
            prev = next((x for x in reversed(alive) if (x.rank, x.id) < key), None)
            return nxt, prev

        items = session.scalars(select(TimelineItem).where(TimelineItem.sequence_id == seq_id,
                                                           TimelineItem.deleted_at.is_(None))).all()
        for it in items:
            touched = False
            if it.anchor_clip_id in gone:
                nxt, prev = neighbour(it.anchor_clip_id)
                if nxt is not None:
                    it.anchor_clip_id = nxt.id
                elif prev is not None:
                    it.anchor_clip_id = prev.id
                    it.offset_ms = int(it.offset_ms or 0) + int(prev.duration_ms or 0)
                else:
                    it.anchor_clip_id = None
                touched = True
            end = it.end_anchor or {}
            if isinstance(end, dict) and end.get("clip_id") in gone:
                nxt, prev = neighbour(int(end["clip_id"]))
                # Hết clip bị xoá = hết clip liền trước nó (magnetic) → ưu tiên clip trước, rồi clip sau.
                target = prev or nxt
                it.end_anchor = {"clip_id": target.id} if target is not None else {"sequence_end": True}
                touched = True
            if touched:
                changed.append(it.id)
    session.flush()
    return changed
