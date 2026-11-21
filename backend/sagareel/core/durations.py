"""Thời lượng clip ↔ thời lượng provider ("quay dài, cắt gọn").

`clip.duration_ms` là độ dài **mong muốn trên màn hình**, do người viết (LLM của tool, agent, người dùng) đặt tự do.
Provider video chỉ tạo được vài mức giây — danh sách lấy từ `capabilities()["durations"]` của **kết nối được chọn**
(mặc định của adapter, ghi đè bằng `options.durations` của kết nối); không có danh sách cứng nào trong code.

Khi tạo video: xin mức **nhỏ nhất ≥ mong muốn + 0,3 s** (chừa đệm để bản dựng tự cắt sát nội dung); không có mức nào đủ
→ mức dài nhất (kèm cảnh báo). Không có caps (kết nối không khai báo) → làm tròn lên giây.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

PAD_MS = 300  # đệm xin thêm để còn chỗ cắt đầu/cuối


def sanitize(raw: Any) -> list[int]:
    """Danh sách giây hợp lệ (số nguyên > 0, tăng dần, không trùng). Nhận list hoặc chuỗi "3, 5, 6" (form)."""
    if raw is None:
        return []
    items: Iterable[Any]
    if isinstance(raw, str):
        items = [x for x in raw.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
    elif isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = [raw]
    out: set[int] = set()
    for x in items:
        if isinstance(x, bool):
            continue
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0 and float(v).is_integer():
            out.add(int(v))
    return sorted(out)


def ceil_s(ms: float) -> int:
    return max(1, math.ceil(float(ms) / 1000 - 1e-9))


def pick(desired_ms: float, caps: Any, *, pad_ms: int = PAD_MS) -> tuple[int, str | None]:
    """(giây xin provider, cảnh báo | None) cho clip muốn dài `desired_ms`.

    Nhỏ nhất trong caps ≥ `desired_ms + pad_ms`; không có → dài nhất kèm cảnh báo; caps rỗng → làm tròn lên giây."""
    need = max(0.0, float(desired_ms)) + max(0, int(pad_ms))
    opts = sanitize(caps)
    if not opts:
        return ceil_s(need), None
    for d in opts:
        if d * 1000 >= need - 1e-6:
            return d, None
    top = opts[-1]
    return top, (f"Clip muốn {float(desired_ms) / 1000:g} s vượt mức tối đa {top} s của kết nối; tạo {top} s "
                 "(bản dựng làm chậm/giữ khung cuối hoặc tách clip).")


def exact(seconds: float, caps: Any) -> tuple[int, str | None]:
    """Agent/người yêu cầu đúng `seconds` giây (overrides.duration): giữ nguyên nếu kết nối hỗ trợ, không thì mức gần
    nhất ≥ (hoặc dài nhất) kèm cảnh báo. Không cộng đệm."""
    want = math.ceil(float(seconds))
    opts = sanitize(caps)
    if not opts or want in opts:
        return max(1, want), None
    sec, _ = pick(want * 1000, opts, pad_ms=0)
    return sec, f"Kết nối không hỗ trợ {want} s; dùng {sec} s (hỗ trợ: {', '.join(map(str, opts))} s)."


def supported(caps: Any, seconds: int) -> bool:
    opts = sanitize(caps)
    return not opts or int(seconds) in opts
