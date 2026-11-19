"""Preset dữ liệu của dựng phim: `motion` (still_motion + lớp hậu kỳ) và `transition`.

Nguồn: YAML cài sẵn ở `defaults/presets/<kind>/*.yaml` (file `_*.yaml` là tham số chung, không phải preset), ghi đè
bởi dòng bảng `preset` cùng `kind`/`key` (toàn cục rồi tới project) khi có session.
"""

from __future__ import annotations

import copy
import functools
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError

PRESETS_DIR = Path(__file__).resolve().parent.parent / "defaults" / "presets"
KINDS = ("motion", "transition")


@functools.lru_cache(maxsize=8)
def _builtin(kind: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    folder = PRESETS_DIR / kind
    for f in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        key = "_" + f.stem.lstrip("_") if f.stem.startswith("_") else str(data.get("key") or f.stem)
        out[key] = data
    return out


def builtin(kind: str) -> dict[str, dict[str, Any]]:
    """Bản sao preset cài sẵn (`key` → data)."""
    return copy.deepcopy(_builtin(kind))


def common(kind: str) -> dict[str, Any]:
    """Tham số chung (`_common.yaml`) của một kind."""
    return copy.deepcopy(_builtin(kind).get("_common", {}))


def load(kind: str, session: Session | None = None, project_id: int | None = None) -> dict[str, dict[str, Any]]:
    """Preset hiệu lực: cài sẵn ← dòng `preset` toàn cục ← dòng của project (đã xoá mềm thì bỏ qua)."""
    presets = {k: v for k, v in builtin(kind).items() if not k.startswith("_")}
    if session is not None:
        from sagareel.db.models import Preset

        rows = session.scalars(
            select(Preset).where(Preset.kind == kind, Preset.deleted_at.is_(None))
        ).all()
        # Toàn cục trước, project sau (thắng).
        for row in sorted(rows, key=lambda r: (r.project_id is not None, r.id)):
            if row.project_id is None or row.project_id == project_id:
                base = presets.get(row.key, {})
                presets[row.key] = {**base, **dict(row.data or {}), "key": row.key}
    return presets


def get(kind: str, key: str, presets: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    table = presets if presets is not None else load(kind)
    if key not in table:
        raise AppError(
            "preset_not_found", f"Không có preset {kind} “{key}”.",
            hint=f"Các preset có sẵn: {', '.join(sorted(table))}.", status=404,
        )
    return copy.deepcopy(table[key])
