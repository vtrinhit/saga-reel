"""Hợp đồng prompt compiler (Task W0); thân cài đặt ở `compile/compiler.py` (Task A).

- `verbatim`: prompt gửi đúng từng byte người dùng nhập.
- `synced`: ghép từ khối khoá (mô tả tài sản, `lock_word` của look, khối phong cách project) — không bao giờ tự viết
  lại phần khoá.
- Compile chỉ đọc DB. Clip `link=continue` cần khung cuối video của clip trước: gọi
  `await compiler.prepare_clip(services, clip_id)` (trích bằng ffmpeg, có cache) **trước** `compile_clip_image`;
  không gọi thì prompt vẫn ra nhưng thiếu ảnh nối và có cảnh báo.
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
    label: str  # "Image N: <role> of <name>"
    source: str  # anchor/sheet/ref/extra/input/continue


@dataclass
class CompiledPrompt:
    text: str
    refs: list[RefItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider_caps: dict[str, Any] = field(default_factory=dict)


def compile_clip_image(session: Session, clip_id: int, *, connection: int | str | None = None) -> CompiledPrompt:
    """Prompt ảnh của clip (+ ref theo khả năng của kết nối)."""
    from sagareel.compile import compiler

    return compiler.compile_clip_image(session, clip_id, connection=connection)


def compile_clip_motion(session: Session, clip_id: int) -> CompiledPrompt:
    """Prompt chuyển động (video) của clip."""
    from sagareel.compile import compiler

    return compiler.compile_clip_motion(session, clip_id)


def compile_sheet(session: Session, variant_id: int, view: str) -> CompiledPrompt:
    """Prompt một góc nhìn trong sheet của look."""
    from sagareel.compile import compiler

    return compiler.compile_sheet(session, variant_id, view)
