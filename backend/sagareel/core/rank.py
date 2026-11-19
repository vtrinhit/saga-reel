"""Fractional index base-62 cho cột `rank` (thứ tự clip/scene/lời thoại/nhóm) — spec §6.

Khoá = phần nguyên (ký tự đầu mã hoá độ dài: `a`..`z` → 2..27 ký tự, `A`..`Z` → số âm) + phần phân số không kết thúc
bằng `0`. Bảng chữ số `0-9A-Za-z` tăng dần theo ASCII → so sánh chuỗi Python == collation BINARY của SQLite.
Chèn/đổi chỗ một phần tử chỉ sửa một dòng. Theo thuật toán "fractional-indexing" (rocicorp, CC0).

- `between(None, None) == "a0"`; `between(a, None)` sau `a`; `between(None, b)` trước `b`.
- `n_after(a, n)`: n khoá tăng dần sau `a` (append hàng loạt, khoá ngắn).
- `rebalance(keys)`: bộ khoá mới ngắn, cùng số lượng, giữ thứ tự (khi khoá đã dài do chèn dồn một chỗ).
"""

from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ZERO = DIGITS[0]
_SMALLEST_INT = "A" + _ZERO * 26


def _midpoint(a: str, b: str | None) -> str:
    """Phân số nằm giữa `a` và `b` (chuỗi chữ số, không có phần nguyên; `b=None` = 1)."""
    if b is not None and a >= b:
        raise ValueError(f"rank: {a!r} phải nhỏ hơn {b!r}")
    if a[-1:] == _ZERO or (b is not None and b[-1:] == _ZERO):
        raise ValueError("rank: phần phân số không được kết thúc bằng 0")
    if b is not None:
        n = 0
        while (a[n] if n < len(a) else _ZERO) == b[n]:
            n += 1
        if n > 0:
            return b[:n] + _midpoint(a[n:], b[n:])
    da = DIGITS.index(a[0]) if a else 0
    db = DIGITS.index(b[0]) if b is not None else len(DIGITS)
    if db - da > 1:
        return DIGITS[(da + db + 1) // 2]
    if b is not None and len(b) > 1:
        return b[:1]
    return DIGITS[da] + _midpoint(a[1:], None)


def _int_len(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise ValueError(f"rank: ký tự đầu không hợp lệ {head!r}")


def _int_part(key: str) -> str:
    n = _int_len(key[0])
    if n > len(key):
        raise ValueError(f"rank: khoá không hợp lệ {key!r}")
    return key[:n]


def _validate(key: str) -> None:
    if not key or key == _SMALLEST_INT:
        raise ValueError(f"rank: khoá không hợp lệ {key!r}")
    if any(c not in DIGITS for c in key):
        raise ValueError(f"rank: khoá chứa ký tự lạ {key!r}")
    frac = key[len(_int_part(key)):]
    if frac[-1:] == _ZERO:
        raise ValueError(f"rank: khoá không hợp lệ {key!r}")


def validate(key: str) -> None:
    """Khoá rank hợp lệ? Không → `ValueError` (dùng trước khi ghi rank do client gửi)."""
    _validate(key)


def _increment(x: str) -> str | None:
    head, digs = x[0], list(x[1:])
    i = len(digs) - 1
    while i >= 0:
        d = DIGITS.index(digs[i]) + 1
        if d < len(DIGITS):
            digs[i] = DIGITS[d]
            return head + "".join(digs)
        digs[i] = _ZERO
        i -= 1
    if head == "Z":
        return "a" + _ZERO
    if head == "z":
        return None
    h = chr(ord(head) + 1)
    if h > "a":
        digs.append(_ZERO)
    else:
        digs.pop()
    return h + "".join(digs)


def _decrement(x: str) -> str | None:
    head, digs = x[0], list(x[1:])
    i = len(digs) - 1
    while i >= 0:
        d = DIGITS.index(digs[i]) - 1
        if d >= 0:
            digs[i] = DIGITS[d]
            return head + "".join(digs)
        digs[i] = DIGITS[-1]
        i -= 1
    if head == "a":
        return "Z" + DIGITS[-1]
    if head == "A":
        return None
    h = chr(ord(head) - 1)
    if h < "Z":
        digs.append(DIGITS[-1])
    else:
        digs.pop()
    return h + "".join(digs)


def between(a: str | None, b: str | None) -> str:
    """Khoá nằm giữa `a` và `b` (None = đầu/cuối). `a >= b` → `ValueError`."""
    if a is not None:
        _validate(a)
    if b is not None:
        _validate(b)
    if a is not None and b is not None and a >= b:
        raise ValueError(f"rank: {a!r} phải nhỏ hơn {b!r}")
    if a is None:
        if b is None:
            return "a" + _ZERO
        ib = _int_part(b)
        fb = b[len(ib):]
        if ib == _SMALLEST_INT:
            return ib + _midpoint("", fb)
        if ib < b:
            return ib
        res = _decrement(ib)
        if res is None:
            raise ValueError("rank: không thể tạo khoá nhỏ hơn nữa")
        return res
    ia = _int_part(a)
    fa = a[len(ia):]
    if b is None:
        i = _increment(ia)
        return ia + _midpoint(fa, None) if i is None else i
    ib = _int_part(b)
    fb = b[len(ib):]
    if ia == ib:
        return ia + _midpoint(fa, fb)
    i = _increment(ia)
    if i is None:
        raise ValueError("rank: không thể tạo khoá lớn hơn nữa")
    if i < b:
        return i
    return ia + _midpoint(fa, None)


def n_after(a: str | None, n: int) -> list[str]:
    """`n` khoá tăng dần nằm sau `a` (None = bắt đầu từ "a0")."""
    out: list[str] = []
    prev = a
    for _ in range(n):
        prev = between(prev, None)
        out.append(prev)
    return out


def rebalance(keys: list[str]) -> list[str]:
    """Bộ khoá mới ngắn gọn cùng số lượng với `keys` (gán lại theo thứ tự hiện tại)."""
    return n_after(None, len(keys))
