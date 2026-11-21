"""Tự cắt theo nội dung ở tầng dịch vụ: phân tích take video đang chọn (cache theo sha, chép vào `media.meta`),
`auto_trim` cho `clip.get/list`, job `clip.analyze_trim` (không tốn tiền provider).

Phân tích + quyết định nằm ở `render/trim.py`; ở đây chỉ đọc DB, gọi ffmpeg NGOÀI session và lưu kết quả.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError
from sagareel.db.models import Clip, DialogueLine, Media, Take
from sagareel.render import trim

logger = logging.getLogger("sagareel.trim")

JOB_TYPE = "clip.analyze_trim"
LANE = "ffmpeg"


# --- Phân tích media ------------------------------------------------------------------------------------------------


def _media_rows(s: Session, media_ids: list[int]) -> list[Media]:
    return list(s.scalars(select(Media).where(Media.id.in_(list(media_ids) or [-1]))))


def analyses(services: Any, media_ids: list[int], *, compute: bool = True) -> dict[int, dict]:
    """Phân tích các media video (id → kết quả). `compute=False` chỉ đọc cache (op chỉ đọc: không chạy ffmpeg).
    Kết quả mới được chép vào `media.meta["content"]` để `clip.get/list` đọc không cần ffmpeg."""
    from sagareel.services import media as media_svc

    cache_dir = services.settings.cache_dir
    todo: list[dict[str, Any]] = []
    out: dict[int, dict] = {}
    with services.db.session() as s:
        for m in _media_rows(s, media_ids):
            if m.kind != "video":
                continue
            got = trim.cached(cache_dir, m.sha256, m.meta)
            if got is not None:
                out[m.id] = got
                if not isinstance((m.meta or {}).get("content"), dict):
                    todo.append({"id": m.id, "result": got})
                continue
            if not compute:
                continue
            try:
                path = media_svc.file_path(services.settings, m)
            except AppError:
                continue
            probe_meta = (m.meta or {}).get("probe") or {}
            todo.append({"id": m.id, "path": path, "sha": m.sha256, "has_audio": probe_meta.get("has_audio"),
                         "duration_ms": m.duration_ms, "meta": dict(m.meta or {})})
    for t in todo:
        if "result" in t:
            continue
        try:
            has_audio, dur = t["has_audio"], t["duration_ms"]
            if has_audio is None or not dur:
                from sagareel.media.probe import probe

                info = probe(t["path"])
                has_audio, dur = bool(info.has_audio), info.duration_ms
            t["result"] = trim.ensure(t["path"], t["sha"], cache_dir, has_audio=bool(has_audio), duration_ms=dur)
            out[t["id"]] = t["result"]
        except Exception as e:  # noqa: BLE001 — phân tích hỏng không chặn dựng: clip giữ cách cũ
            logger.warning("trim analysis failed for media %s: %s", t["id"], e)
    store = {t["id"]: t["result"] for t in todo if t.get("result") is not None}
    if store and compute:  # chỉ đọc (ước tính) → không ghi DB
        with services.db.session() as s:
            for m in _media_rows(s, list(store)):
                m.meta = {**(m.meta or {}), "content": store[m.id]}
    return out


# --- auto_trim cho clip ---------------------------------------------------------------------------------------------


def _next_links(s: Session, rows: list[Clip]) -> dict[int, str]:
    """`link` của clip ngay sau mỗi clip (theo rank trong tập)."""
    out: dict[int, str] = {}
    by_seq: dict[int, list[Clip]] = {}
    for c in rows:
        by_seq.setdefault(c.sequence_id, []).append(c)
    for seq_id, cs in by_seq.items():
        ids = {c.id for c in cs}
        ordered = list(s.execute(select(Clip.id, Clip.link).where(Clip.sequence_id == seq_id,
                                                                   Clip.deleted_at.is_(None))
                                 .order_by(Clip.rank, Clip.id)))
        for (cid, _), (_, nxt) in itertools.pairwise(ordered):
            if cid in ids:
                out[cid] = nxt or "cut"
    return out


def info_for(s: Session, rows: list[Clip]) -> dict[int, dict[str, Any]]:
    """`{clip_id: {"trim_mode", "auto_trim"}}` từ phân tích đã lưu (không chạy ffmpeg). `auto_trim` = null khi clip
    chưa có video; `{in_ms, out_ms, dur_ms, reason, applied, take_ms}` (in/out cùng nghĩa trim_in_ms/trim_out_ms)."""
    if not rows:
        return {}
    take_ids = [c.video_take_id for c in rows if c.video_take_id]
    media_of: dict[int, Media] = {}
    if take_ids:
        for tid, m in s.execute(select(Take.id, Media).join(Media, Media.id == Take.media_id)
                                .where(Take.id.in_(take_ids))):
            media_of[tid] = m
    sources: dict[int, set[str]] = {}
    for cid, src in s.execute(select(DialogueLine.clip_id, DialogueLine.source).where(
            DialogueLine.clip_id.in_([c.id for c in rows]), DialogueLine.deleted_at.is_(None))):
        sources.setdefault(cid, set()).add(src)
    nexts = _next_links(s, rows)
    cache_dir = None
    out: dict[int, dict[str, Any]] = {}
    for c in rows:
        mode = trim.mode_of(c.settings, c.trim_in_ms, c.trim_out_ms)
        m = media_of.get(c.video_take_id) if c.video_take_id else None
        auto: dict[str, Any] | None = None
        if m is not None and c.engine == "ai_video":
            a = trim.cached(cache_dir, m.sha256, m.meta)
            srcs = sources.get(c.id, set())
            auto = trim.decide(a, desired_ms=c.duration_ms, mode="auto", has_grok="grok" in srcs,
                               has_voice=bool(srcs & {"tts", "upload"}), next_continue=nexts.get(c.id) == "continue")
            auto["take_ms"] = int(a["duration_ms"]) if a else m.duration_ms
            if a and "grok" in srcs:
                auto["gain_db"] = trim.loudness_gain(a)
        out[c.id] = {"trim_mode": mode, "auto_trim": auto}
    return out


# --- Job clip.analyze_trim ------------------------------------------------------------------------------------------


def media_of_clips(services: Any, clip_ids: list[int]) -> dict[int, int]:
    """clip_id → media_id của take video đang chọn."""
    with services.db.session() as s:
        rows = s.execute(select(Clip.id, Take.media_id).join(Take, Take.id == Clip.video_take_id)
                         .where(Clip.id.in_(clip_ids), Clip.deleted_at.is_(None), Take.media_id.is_not(None)))
        return {cid: mid for cid, mid in rows}


def analyze_clips(services: Any, clip_ids: list[int]) -> dict[str, Any]:
    """Đồng bộ (thread): phân tích take video đang chọn của các clip rồi trả `auto_trim` từng clip."""
    mapping = media_of_clips(services, clip_ids)
    done = analyses(services, list(set(mapping.values())), compute=True)
    with services.db.session() as s:
        rows = list(s.scalars(select(Clip).where(Clip.id.in_(clip_ids))))
        info = info_for(s, rows)
    return {"analyzed": len(done), "clips": {str(cid): info.get(cid) for cid in clip_ids},
            "without_video": [cid for cid in clip_ids if cid not in mapping]}
