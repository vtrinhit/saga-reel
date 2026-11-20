"""Độ dài lời nói: một nguồn duy nhất cho mọi kiểm tra "thoại vừa clip".

Tiếng Việt đơn âm tiết: mỗi từ cách nhau bởi khoảng trắng ≈ một âm tiết. Nhịp kể/thoại tự nhiên ≈ 4,5 âm tiết/giây,
chừa ~2 s đầu/cuối clip (craft-and-prompts §7.4) → giới hạn = (giây − 2) × 4,5 **âm tiết** (không phải ký tự).
"""

from __future__ import annotations

import re

SYLLABLES_PER_S = 4.5
PAD_S = 2.0

_WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)


def syllables(text: str) -> int:
    """Số âm tiết (từ) trong câu; dấu câu và ký hiệu không tính."""
    return len(_WORD.findall(text or ""))


def max_syllables(duration_s: float) -> int:
    """Số âm tiết tối đa đọc vừa một clip dài `duration_s` giây."""
    return max(0, int((duration_s - PAD_S) * SYLLABLES_PER_S))


def fits(text: str, duration_s: float) -> bool:
    return syllables(text) <= max_syllables(duration_s)


def seconds_needed(text: str) -> float:
    """Thời lượng clip tối thiểu để đọc hết câu (kể cả phần đệm)."""
    return syllables(text) / SYLLABLES_PER_S + PAD_S
