"""Xem trước N giây đầu có tiếng (spec §12.5): chỉ dựng các khối phủ N giây rồi cắt ở lượt âm thanh cuối."""

from __future__ import annotations

from sagareel.core.errors import AppError
from sagareel.render.chunks import ChunkSpec


def select(specs: list[ChunkSpec], seconds: float, total_ms: int) -> tuple[list[ChunkSpec], int]:
    """(các khối đầu phủ `seconds`, thời lượng đầu ra = min(N giây, tổng))."""
    if seconds <= 0:
        raise AppError("render_bad_preview", "Số giây xem trước phải > 0.", status=422)
    limit = round(seconds * 1000)
    chosen = [s for s in specs if s.start_ms < limit]
    return chosen, min(limit, total_ms, sum(s.dur_ms for s in chosen))
