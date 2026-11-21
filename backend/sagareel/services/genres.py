"""Số liệu thể loại (độ dài tập/clip, số clip, chuyển cảnh, hook, kiểu kết…) — **dữ liệu của project** (`project.genre`).

- `defaults/genres/<key>.yaml` là số liệu tham khảo nghề làm phim (craft-and-prompts §7.4), chỉ dùng khi tạo project
  trống hoặc khi người dùng/AI chọn nạp số liệu một thể loại (`reference(key)`); project mẫu mang sẵn số liệu riêng
  và được nhân bản cùng project.
- `validate(data)` kiểm bằng `GenreData` (khoá lạ được giữ: agent/kênh mẫu thêm được).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from sagareel.core.errors import ValidationFailed, clean_validation_errors

DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "defaults"
GENRES_DIR = DEFAULTS_DIR / "genres"
TRANSITIONS = Literal["cut", "crossfade", "fade", "fadeblack", "fadewhite", "dissolve"]


def _span(v: Any) -> tuple[float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError("cần dạng [thấp, cao]")
    lo, hi = v
    if isinstance(lo, bool) or isinstance(hi, bool) or not all(isinstance(x, (int, float)) for x in (lo, hi)):
        raise ValueError("cần hai số")
    if not (0 < lo <= hi):
        raise ValueError("cần 0 < thấp <= cao")
    return float(lo), float(hi)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Hook(_Strict):
    window_s: float = Field(gt=0)
    visual_in_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> _Hook:
        if self.visual_in_s > self.window_s:
            raise ValueError("visual_in_s phải <= window_s")
        return self


class _Structure(_Strict):
    groups: bool
    scenes: bool


class GenreData(BaseModel):
    """Số liệu thể loại. Khoá lạ được giữ."""

    model_config = ConfigDict(extra="allow")

    episode_s: tuple[float, float]
    clip_s: tuple[float, float]
    clips: tuple[int, int]
    transition: TRANSITIONS
    scene_transition: TRANSITIONS | None = None
    scene_transition_s: float | None = Field(default=None, gt=0)
    end: Literal["cliffhanger", "loop", "loop_or_cta", "cta", "resolve", "none"] | None = None
    hook: _Hook | None = None
    structure: _Structure | None = None
    dialogue_style: Literal["narration", "dialogue", "mixed", "none"] | None = None
    default_platforms: list[str] = Field(default_factory=list)

    @field_validator("episode_s", "clip_s", "clips", mode="before")
    @classmethod
    def _spans(cls, v: Any) -> Any:
        _span(v)
        return v


def validate(data: Any) -> dict[str, Any]:
    """`{}` hợp lệ (project chưa có số liệu); còn lại phải khớp `GenreData`. Sai → `genre_invalid` (422)."""
    if data in (None, {}):
        return {}
    if not isinstance(data, dict):
        raise ValidationFailed("Số liệu thể loại phải là object.", code="genre_invalid")
    try:
        GenreData.model_validate(data)
    except ValidationError as e:
        raise ValidationFailed(f"Số liệu thể loại không hợp lệ: {len(e.errors())} lỗi.", code="genre_invalid",
                               hint="Xem `extra.errors` để biết trường nào sai.",
                               extra={"errors": clean_validation_errors(e.errors())}) from None
    return dict(data)


@functools.lru_cache(maxsize=1)
def _references() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(GENRES_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        key = str(raw.get("key") or path.stem)
        out[key] = {"key": key, "name": str(raw.get("name") or key), "data": validate(raw.get("data") or {})}
    return out


def references() -> list[dict[str, Any]]:
    """Số liệu tham khảo: [{key, name, data}]."""
    return [{**v, "data": dict(v["data"])} for v in _references().values()]


def reference(key: str | None) -> dict[str, Any] | None:
    v = _references().get(key or "")
    return dict(v["data"]) if v else None


def of_project(project: Any) -> dict[str, Any]:
    return dict(project.genre or {})
