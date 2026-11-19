"""Hợp đồng prompt compiler (Task W0). Task A thay thân các hàm `compile_*` (gọi sang `compile/compiler.py`).

- `verbatim`: prompt gửi đúng từng byte người dùng nhập.
- `synced`: ghép từ khối khoá (mô tả tài sản, `lock_word` của look, khối phong cách project) — không bao giờ tự viết
  lại phần khoá.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RefItem:
    """Một ảnh tham chiếu gửi kèm prompt."""

    media_id: int
    role: str  # appearance/outfit/spatial_layout/composition/scale/effect/start_frame/end_frame/style/subject
    label: str  # "Ảnh N: <vai trò> của <tên>"
    source: str  # anchor/sheet/ref/extra/input


@dataclass
class CompiledPrompt:
    text: str
    refs: list[RefItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider_caps: dict[str, Any] = field(default_factory=dict)


def compile_clip_image(session: Session, clip_id: int, *, connection: str | None = None) -> CompiledPrompt:
    """Prompt ảnh của clip (+ ref theo khả năng của kết nối)."""
    raise NotImplementedError("compile_clip_image: Task A cài đặt")


def compile_clip_motion(session: Session, clip_id: int) -> CompiledPrompt:
    """Prompt chuyển động (video) của clip."""
    raise NotImplementedError("compile_clip_motion: Task A cài đặt")


def compile_sheet(session: Session, variant_id: int, view: str) -> CompiledPrompt:
    """Prompt một góc nhìn trong sheet của look."""
    raise NotImplementedError("compile_sheet: Task A cài đặt")
