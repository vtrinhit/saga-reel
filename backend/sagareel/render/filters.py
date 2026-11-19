"""Tiện ích chuỗi filtergraph dùng chung cho dựng phim."""

from __future__ import annotations

from pathlib import Path


def escape_path(path: Path | str) -> str:
    """Đường dẫn trong tham số filter (`ass=filename='…'`, `fontfile='…'`): `\\` → `/`, escape `:` và `'`.

    Đã thử trên Windows (ổ `C:`): `ass=filename='C\\:/…/sub.ass':fontsdir='C\\:/Windows/Fonts'`.
    """
    s = str(path).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")


def secs(ms: float) -> str:
    """Mili giây → giây dạng chuỗi gọn (tối đa 3 chữ số thập phân)."""
    return f"{ms / 1000:.3f}".rstrip("0").rstrip(".") or "0"


def atempo_chain(speed: float) -> list[str]:
    """`atempo` chỉ nhận 0.5–100 mỗi bộ lọc: tách hệ số nhỏ hơn 0.5 thành chuỗi."""
    out: list[str] = []
    s = float(speed)
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    while s > 100:
        out.append("atempo=100")
        s /= 100
    if abs(s - 1.0) > 1e-6:
        out.append(f"atempo={s:.6g}")
    return out


def samples(frames: int, fps: int, rate: int = 48000) -> int:
    """Số mẫu âm thanh đúng bằng `frames` khung hình."""
    return round(frames * rate / fps)
