"""Tiện ích chuỗi filtergraph dùng chung cho dựng phim."""

from __future__ import annotations

import math as _math
import re as _re
import string as _string
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


# --- Kiểm giá trị người dùng trước khi đặt vào filtergraph -------------------------------------------------------
# Mọi giá trị đến từ preset/clip/timeline (DB, file khai báo, entity.patch) đi qua các hàm dưới đây ở CẢ chỗ nhập lẫn
# chỗ dựng (dòng cũ trong DB hoặc hoàn tác có thể mang giá trị chưa kiểm). Sai → `ValueError` (thông điệp tiếng Việt).

COLOR_RE = _re.compile(r"^(#[0-9a-fA-F]{6,8}|[a-z]+)(@[0-9.]+)?$")
# `ffmpeg -h filter=xfade` (bỏ `custom`: biểu thức tuỳ ý).
XFADE_TRANSITIONS = frozenset({
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright",
    "smoothup", "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
    "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice",
    "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin", "fadefast",
    "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown"
})
MOTION_PLACEHOLDERS = frozenset({"amt", "ease", "focus_x", "focus_y", "span", "start", "zoom"})
_EXPR_RE = _re.compile(r"^[A-Za-z0-9_+\-*/().,{} <>=!?]*$")
# Lớp hậu kỳ (motion type=post): chỉ bộ lọc hình thuần tính toán (không đọc/ghi file, không nạp plugin).
POST_FILTERS = frozenset({"noise", "eq", "vignette", "hue", "unsharp", "gblur", "boxblur", "colorbalance",
                          "colorchannelmixer", "colorlevels", "chromashift", "rgbashift", "vibrance", "colortemperature",
                          "fade", "format", "null"})
_FILTER_NAME_RE = _re.compile(r"^[a-z0-9_]+$")


def check_color(value: object) -> str:
    s = str(value)
    if not COLOR_RE.match(s) or "\n" in s:
        raise ValueError(f"Màu '{s}' không hợp lệ (vd. white, #FFCC00, yellow@0.8).")
    return s


def check_xfade(value: object) -> str:
    s = str(value)
    if s not in XFADE_TRANSITIONS:
        raise ValueError(f"Chuyển cảnh xfade '{s}' không hợp lệ.")
    return s


def check_number(value: object, name: str, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{name}' phải là số.")  # noqa: TRY004 — người gọi bắt ValueError
    v = float(value)
    if not _math.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"'{name}' nằm ngoài khoảng cho phép.")
    return v


def check_motion_expr(expr: object, name: str) -> str:
    """Biểu thức zoompan (z/x/y): chỉ ký tự biểu thức + chỗ trống trong `MOTION_PLACEHOLDERS` (không truy cập thuộc
    tính kiểu `{amt.__class__}`); không có `'`, `:`, `;`, `[`, `]`, gạch chéo ngược (không thoát khỏi chuỗi trích dẫn)."""
    s = str(expr)
    if not _EXPR_RE.match(s) or len(s) > 500:
        raise ValueError(f"Biểu thức '{name}' có ký tự không được phép.")
    try:
        parts = list(_string.Formatter().parse(s))
    except ValueError:
        raise ValueError(f"Biểu thức '{name}' sai cú pháp chỗ trống {{…}}.") from None
    for _lit, field, spec, conv in parts:
        if field is None:
            continue
        if field not in MOTION_PLACEHOLDERS or spec or conv:
            raise ValueError(f"Chỗ trống '{{{field}}}' trong '{name}' không được phép.")
    return s


def _split_outside_quotes(s: str, sep: str) -> list[str]:
    out, cur, quoted = [], [], False
    for ch in s:
        if ch == "'":
            quoted = not quoted
        if ch == sep and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if quoted:
        raise ValueError("Dấu nháy ' chưa đóng.")
    out.append("".join(cur))
    return out


def check_post_filter(value: object) -> str:
    """Chuỗi lớp hậu kỳ: các bộ lọc nối bằng `,`, tên trong `POST_FILTERS`; không nhãn `[…]`, không `;`, không gạch
    chéo ngược, không tham số đọc/ghi file."""
    s = str(value)
    if not s or len(s) > 500 or any(c in s for c in "[];\\\n\r") or "file" in s.lower():
        raise ValueError("Bộ lọc hậu kỳ có ký tự/tham số không được phép.")
    for part in _split_outside_quotes(s, ","):
        name = part.split("=", 1)[0].strip()
        if not _FILTER_NAME_RE.match(name) or name not in POST_FILTERS:
            raise ValueError(f"Bộ lọc hậu kỳ '{name}' không được phép.")
    return s


def check_title_params(params: object) -> list[str]:
    """Lỗi của tham số title (mục timeline track=title / title_style) — rỗng nếu hợp lệ."""
    errs: list[str] = []
    if not isinstance(params, dict):
        return ["params phải là object."]
    for key, fn in (("color", lambda v: check_color(v)),
                    ("fontsize_ratio", lambda v: check_number(v, "fontsize_ratio", 0.005, 0.5)),
                    ("y_ratio", lambda v: check_number(v, "y_ratio", 0.0, 1.0))):
        if params.get(key) is not None:
            try:
                fn(params[key])
            except ValueError as e:
                errs.append(str(e))
    return errs
