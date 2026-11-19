"""Lưu trữ (Task BF): dọn file gốc của take không được chọn sang thùng rác `data/trash/`, khôi phục, xoá hẳn.

- `cleanup_preview(filters)` → ứng viên + `confirm_token`; `cleanup(token)` chuyển file sang `trash_dir/<media.path>`
  và đặt `media.trashed_at` (bản ghi media + take giữ nguyên — take không bao giờ bị xoá). Token gắn với đúng tập ứng
  viên: dữ liệu đổi giữa hai bước → `confirm_required`.
- Ứng viên **hẹp**: media chỉ được tham chiếu bởi take KHÔNG được chọn (không là ảnh/video/âm thanh đang chọn của clip
  hay câu thoại, kể cả clip đã xoá mềm; không là take sheet có `selected_by`), take đủ cũ, và không xuất hiện ở bất kỳ
  chỗ nào khác: mỏ neo/sheet/ref của look, ảnh bìa, mẫu giọng, input/ref clip, timeline, âm thanh, bản dựng, khối dựng,
  upload, nghiên cứu kênh, đầu vào (`inputs`) của take khác. File dùng chung đường dẫn với media khác thì bỏ qua.
- `trash.list` / `trash.restore(ids)` / `trash.purge_preview(older_than_days)` → `trash.purge(token)` (xoá file trong
  thùng rác, đặt `media.purged_at`; không hoàn tác được).
- `/media/{id}` của media trong thùng rác → 410 `media_trashed`.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import ConfirmRequired, ValidationFailed
from sagareel.db.base import utcnow
from sagareel.db.models import (
    Asset,
    AssetVariant,
    ChannelStudy,
    Clip,
    DialogueLine,
    Media,
    Project,
    Render,
    RenderChunk,
    Sound,
    Take,
    TimelineItem,
    UploadSession,
    VoiceProfile,
)
from sagareel.services import confirm

CLEANUP_OP = "media.cleanup"
PURGE_OP = "trash.purge"
KINDS = ("image", "video", "audio")
PREVIEW_ITEMS = 200
_INPUT_MEDIA_KEYS = ("ref_media_ids", "image_media_id", "last_frame_media_id", "media_id", "source_media_id")


# --- Ứng viên ---------------------------------------------------------------------------------------------------------


def _ints(values: Any) -> set[int]:
    out: set[int] = set()
    if isinstance(values, int) and not isinstance(values, bool):
        out.add(values)
    elif isinstance(values, (list, tuple, set)):
        for v in values:
            out |= _ints(v)
    elif isinstance(values, dict):
        for v in values.values():
            out |= _ints(v)
    return out


def media_in_use(s: Session) -> set[int]:
    """Mọi media id đang được tham chiếu ngoài vai trò "take không được chọn"."""
    used: set[int] = set()
    selected_takes: set[int] = set()
    for a, b in s.execute(select(Clip.image_take_id, Clip.video_take_id)):
        selected_takes |= {x for x in (a, b) if x}
    selected_takes |= {x for x in s.scalars(select(DialogueLine.audio_take_id)) if x}
    for tid, mid, sel_by, inputs in s.execute(select(Take.id, Take.media_id, Take.selected_by, Take.inputs)):
        if mid is not None and (tid in selected_takes or sel_by):
            used.add(mid)
        for k in _INPUT_MEDIA_KEYS:
            used |= _ints((inputs or {}).get(k))
    for anchor, sheet, refs in s.execute(select(AssetVariant.anchor_media_id, AssetVariant.sheet,
                                                AssetVariant.ref_media_ids)):
        used |= _ints(anchor) | _ints(sheet) | _ints(refs)
    for col in (Asset.cover_media_id, Project.cover_media_id, VoiceProfile.sample_media_id, Clip.input_media_id,
                TimelineItem.media_id, Sound.media_id, Render.media_id, RenderChunk.media_id, UploadSession.media_id):
        used |= {x for x in s.scalars(select(col)) if x}
    for refs in s.scalars(select(Clip.extra_ref_media_ids)):
        used |= _ints(refs)
    for ids in s.scalars(select(ChannelStudy.media_ids)):
        used |= _ints(ids)
    return used


def _filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    f = dict(filters or {})
    days = f.get("unselected_takes_older_than_days", 30)
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise ValidationFailed("unselected_takes_older_than_days phải là số nguyên ≥ 0.")
    kinds = f.get("kinds") or list(KINDS)
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        raise ValidationFailed(f"kinds chỉ gồm: {', '.join(KINDS)}.")
    out: dict[str, Any] = {"unselected_takes_older_than_days": days, "kinds": sorted(set(kinds))}
    if f.get("project_id") is not None:
        out["project_id"] = int(f["project_id"])
    return out


def candidates(s: Session, settings: Any, filters: dict[str, Any]) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(days=filters["unselected_takes_older_than_days"])
    used = media_in_use(s)
    takes_of: dict[int, list[Take]] = {}
    for t in s.scalars(select(Take).where(Take.media_id.is_not(None))):
        takes_of.setdefault(t.media_id, []).append(t)
    q = select(Media).where(Media.trashed_at.is_(None), Media.purged_at.is_(None),
                            Media.kind.in_(filters["kinds"]), Media.id.in_(list(takes_of)))
    if "project_id" in filters:
        q = q.where(Media.project_id == filters["project_id"])
    rows = list(s.scalars(q.order_by(Media.id)))
    path_users: dict[str, int] = {}
    for p in s.scalars(select(Media.path).where(Media.purged_at.is_(None))):
        path_users[p] = path_users.get(p, 0) + 1
    out = []
    for m in rows:
        if m.id in used or path_users.get(m.path, 0) > 1:
            continue
        ts = takes_of[m.id]
        if any(t.created_at and t.created_at > cutoff for t in ts):
            continue
        out.append({"media_id": m.id, "kind": m.kind, "size": int(m.size or 0), "path": m.path,
                    "project_id": m.project_id, "take_ids": sorted(t.id for t in ts)})
    return out


# --- Token (bộ lọc + tập ứng viên) ------------------------------------------------------------------------------------


def _pack(filters: dict[str, Any], inner: str) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(filters, sort_keys=True).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{raw}|{inner}"


def _unpack(token: str | None, preview_op: str) -> tuple[dict[str, Any], str]:
    hint = f"Gọi {preview_op} để xem lại và lấy confirm_token mới."
    if not token or "|" not in token:
        raise ConfirmRequired("Thao tác này cần xác nhận bằng confirm_token.", hint=hint)
    raw, _, inner = token.partition("|")
    try:
        filters = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        raise ConfirmRequired("confirm_token không hợp lệ.", hint=hint) from None
    if not isinstance(filters, dict):
        raise ConfirmRequired("confirm_token không hợp lệ.", hint=hint)
    return filters, inner


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    for it in items:
        k = by_kind.setdefault(it["kind"], {"files": 0, "bytes": 0})
        k["files"] += 1
        k["bytes"] += it["size"]
    return {"files": len(items), "bytes": sum(it["size"] for it in items), "by_kind": by_kind}


# --- Dọn sang thùng rác ------------------------------------------------------------------------------------------------


def cleanup_preview(ctx: OpContext, filters: dict[str, Any] | None) -> dict[str, Any]:
    f = _filters(filters)
    with ctx.services.db.session() as s:
        items = candidates(s, ctx.services.settings, f)
        ids = [it["media_id"] for it in items]
        inner = confirm.make_token(s, CLEANUP_OP, ids, {"filters": f})
    return {"filters": f, **_summary(items), "items": items[:PREVIEW_ITEMS], "truncated": len(items) > PREVIEW_ITEMS,
            "confirm_token": _pack(f, inner), "expires_in_s": confirm.TTL_S}


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))


def trash_path(settings: Any, m: Media) -> Path:
    return Path(settings.trash_dir) / m.path


def cleanup(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    f, inner = _unpack(confirm_token, "media.cleanup_preview")
    f = _filters(f)
    settings = ctx.services.settings
    data_dir = Path(settings.data_dir)
    moved, missing, freed = [], [], 0
    with ctx.services.db.session(immediate=True) as s:
        items = candidates(s, settings, f)
        confirm.verify(s, CLEANUP_OP, [it["media_id"] for it in items], {"filters": f}, inner)
        now = utcnow()
        for it in items:
            m = s.get(Media, it["media_id"])
            src = data_dir / m.path
            if src.is_file():
                _move(src, trash_path(settings, m))
                freed += it["size"]
            else:
                missing.append(m.id)
            m.trashed_at = now
            moved.append(m.id)
    _prune_cache(ctx.services)
    return {"trashed": moved, "files": len(moved), "bytes": freed, "missing_files": missing}


def _prune_cache(services: Any) -> dict[str, Any] | None:
    """Dọn cache dựng (LRU) theo `render.cache_max_gb` / `render.min_free_gb` nếu `render.cache_auto_prune`."""
    from sagareel.render import pipeline as render_pipeline

    try:
        return render_pipeline._prune(services, set())
    except Exception:  # noqa: BLE001 — dọn cache là phụ, không chặn thao tác chính
        return None


# --- Thùng rác ----------------------------------------------------------------------------------------------------------


def _trash_item(settings: Any, m: Media) -> dict[str, Any]:
    return {"media_id": m.id, "kind": m.kind, "size": int(m.size or 0), "path": m.path, "project_id": m.project_id,
            "trashed_at": m.trashed_at, "file_present": trash_path(settings, m).is_file()}


def trash_list(ctx: OpContext, project_id: int | None = None, cursor: int | None = None,
               limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    with ctx.services.db.session() as s:
        q = select(Media).where(Media.trashed_at.is_not(None), Media.purged_at.is_(None))
        if project_id is not None:
            q = q.where(Media.project_id == project_id)
        if cursor is not None:
            q = q.where(Media.id > cursor)
        rows = list(s.scalars(q.order_by(Media.id).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        total = list(s.execute(select(Media.size).where(Media.trashed_at.is_not(None), Media.purged_at.is_(None))))
        return {"items": [_trash_item(ctx.services.settings, m) for m in rows],
                "next_cursor": rows[-1].id if more else None,
                "total": {"files": len(total), "bytes": sum(int(x[0] or 0) for x in total)}}


def restore(ctx: OpContext, media_ids: list[int]) -> dict[str, Any]:
    """Đưa file từ thùng rác về chỗ cũ (media chưa xoá hẳn)."""
    settings = ctx.services.settings
    restored, skipped = [], []
    with ctx.services.db.session(immediate=True) as s:
        for mid in dict.fromkeys(media_ids):
            m = s.get(Media, mid)
            if m is None or m.trashed_at is None or m.purged_at is not None:
                skipped.append({"media_id": mid, "reason": "not_in_trash"})
                continue
            src = trash_path(settings, m)
            if not src.is_file():
                skipped.append({"media_id": mid, "reason": "file_missing"})
                continue
            _move(src, Path(settings.data_dir) / m.path)
            m.trashed_at = None
            restored.append(mid)
    return {"restored": restored, "skipped": skipped}


def _purge_filters(older_than_days: int | None, project_id: int | None) -> dict[str, Any]:
    days = 0 if older_than_days is None else int(older_than_days)
    if days < 0:
        raise ValidationFailed("older_than_days phải ≥ 0.")
    f: dict[str, Any] = {"older_than_days": days}
    if project_id is not None:
        f["project_id"] = int(project_id)
    return f


def _purge_rows(s: Session, f: dict[str, Any]) -> list[Media]:
    cutoff = utcnow() - timedelta(days=f["older_than_days"])
    q = select(Media).where(Media.trashed_at.is_not(None), Media.purged_at.is_(None), Media.trashed_at <= cutoff)
    if "project_id" in f:
        q = q.where(Media.project_id == f["project_id"])
    return list(s.scalars(q.order_by(Media.id)))


def purge_preview(ctx: OpContext, older_than_days: int | None = None, project_id: int | None = None) -> dict:
    f = _purge_filters(older_than_days, project_id)
    with ctx.services.db.session() as s:
        rows = _purge_rows(s, f)
        items = [_trash_item(ctx.services.settings, m) for m in rows]
        inner = confirm.make_token(s, PURGE_OP, [m.id for m in rows], {"filters": f})
    return {"filters": f, **_summary(items), "items": items[:PREVIEW_ITEMS], "truncated": len(items) > PREVIEW_ITEMS,
            "confirm_token": _pack(f, inner), "expires_in_s": confirm.TTL_S,
            "warning": "Xoá hẳn khỏi đĩa — không hoàn tác được."}


def purge(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    f, inner = _unpack(confirm_token, "trash.purge_preview")
    f = _purge_filters(f.get("older_than_days"), f.get("project_id"))
    settings = ctx.services.settings
    purged, freed = [], 0
    with ctx.services.db.session(immediate=True) as s:
        rows = _purge_rows(s, f)
        confirm.verify(s, PURGE_OP, [m.id for m in rows], {"filters": f}, inner)
        now = utcnow()
        for m in rows:
            p = trash_path(settings, m)
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    continue  # file đang bị giữ (Windows) → để lần sau
                freed += int(m.size or 0)
            m.purged_at = now
            purged.append(m.id)
    return {"purged": purged, "files": len(purged), "bytes": freed}
