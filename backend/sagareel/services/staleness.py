"""Bảng làm-cũ (spec §10, §23.9): đọc `defaults/staleness.yaml`, gắn/gỡ cờ `stale_*` trong `clip.flags`.

Cờ chỉ là đánh dấu "đầu ra hiện tại đã cũ so với đầu vào" — không bao giờ xoá take/media. Mọi hàm ở đây chạy trong
session của người gọi (UoW của op hoặc của job) và gán lại `clip.flags` bằng dict mới (không sửa tại chỗ).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.db.models import Clip

RULES_PATH = Path(__file__).resolve().parent.parent / "defaults" / "staleness.yaml"


@functools.lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    with RULES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("fields", {})
    data.setdefault("requires", {})
    data.setdefault("events", {})
    return data


def flags_for(entity: str, fields: Iterable[str]) -> set[str]:
    """Cờ cần gắn khi các `fields` của `entity` (clip/asset_variant/asset/dialogue_line) đổi giá trị."""
    table = rules()["fields"].get(entity) or {}
    out: set[str] = set()
    for f in fields:
        out.update(table.get(f) or [])
    return out


def _allowed(clip: Clip, flag: str) -> bool:
    req = rules()["requires"].get(flag)
    if not req:
        return True
    return any(getattr(clip, col, None) is not None for col in req)


def apply(clip: Clip, flags: Iterable[str]) -> set[str]:
    """Gắn `flags` (= true) lên clip theo điều kiện `requires`; trả các cờ đã gắn."""
    applied = {f for f in flags if _allowed(clip, f)}
    if applied:
        current = dict(clip.flags or {})
        if any(current.get(f) is not True for f in applied):
            clip.flags = {**current, **{f: True for f in applied}}
    return applied


def clear(clip: Clip, flags: Iterable[str]) -> None:
    current = dict(clip.flags or {})
    drop = [f for f in flags if f in current]
    if drop:
        clip.flags = {k: v for k, v in current.items() if k not in drop}


def next_clip(s: Session, clip: Clip) -> Clip | None:
    """Clip kế tiếp (chưa xoá) theo (rank, id) trong cùng sequence."""
    return s.scalars(
        select(Clip)
        .where(Clip.sequence_id == clip.sequence_id, Clip.deleted_at.is_(None), Clip.id != clip.id,
               (Clip.rank > clip.rank) | ((Clip.rank == clip.rank) & (Clip.id > clip.id)))
        .order_by(Clip.rank, Clip.id)
        .limit(1)
    ).first()


def prev_clip(s: Session, clip: Clip) -> Clip | None:
    """Clip liền trước (chưa xoá) theo (rank, id) trong cùng sequence."""
    return s.scalars(
        select(Clip)
        .where(Clip.sequence_id == clip.sequence_id, Clip.deleted_at.is_(None), Clip.id != clip.id,
               (Clip.rank < clip.rank) | ((Clip.rank == clip.rank) & (Clip.id < clip.id)))
        .order_by(Clip.rank.desc(), Clip.id.desc())
        .limit(1)
    ).first()


def on_selected(s: Session, clip: Clip, part: str) -> dict[str, Any]:
    """Áp luật `events.<part>_selected` sau khi clip đổi take đang chọn của `part` (image/video).

    Trả `{"self": [cờ gắn], "next": {"clip_id", "flags"} | None}`.
    """
    ev = rules()["events"].get(f"{part}_selected") or {}
    clear(clip, ev.get("self_clear") or [])
    if clip.link == "continue":
        clear(clip, ev.get("self_clear_if_continue") or [])
    applied = apply(clip, ev.get("self_set") or [])
    nxt_info = None
    nxt_flags = ev.get("next_continue") or []
    if nxt_flags:
        nxt = next_clip(s, clip)
        if nxt is not None and nxt.link == "continue":
            got = apply(nxt, nxt_flags)
            if got:
                nxt_info = {"clip_id": nxt.id, "flags": sorted(got)}
    return {"self": sorted(applied), "next": nxt_info}
