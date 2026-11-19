"""Truy cập preset nền tảng + các phép tính thuần dựa trên chúng (tham số ffmpeg, vùng an toàn, chia phần).

Preset là dữ liệu trong bảng `preset` (kind `platform`, nạp từ `defaults/presets/platform/`); schema ở
`services.presets.PlatformData`. Không gọi ffmpeg ở đây.
"""

from __future__ import annotations

import math
from typing import Any

from sagareel.core.errors import NotFound
from sagareel.services import presets

PLATFORM_KINDS = ("fb-reels", "fb-video", "yt-shorts", "yt-long", "tiktok")
DEFAULT_TITLE_MAX = 100  # nền tảng không công bố giới hạn tiêu đề → mặc định chung an toàn


def get(services: Any, key: str, project_id: int | None = None) -> dict[str, Any]:
    """Dữ liệu preset nền tảng (project thắng toàn cục). Không có → `unknown_platform` (404). Đồng bộ."""
    presets.ensure_builtins(services)
    with services.db.session() as s:
        row = presets.find_row(s, "platform", key, project_id)
        if row is None:
            raise NotFound(f"Không có preset nền tảng '{key}'.", code="unknown_platform",
                           hint="Gọi preset.list với kind=platform để xem các nền tảng.")
        return dict(row.data)


def target_size(spec: dict[str, Any]) -> tuple[int, int]:
    return int(spec["width"]), int(spec["height"])


def gop_frames(spec: dict[str, Any], fps: float | None = None) -> int:
    fps = fps or spec["fps"]
    return max(1, round(float(spec["encode"]["gop_s"]) * fps))


def ffmpeg_args(spec: dict[str, Any], fps: float | None = None) -> list[str]:
    """Tham số mã hoá đầy đủ (video + audio + container) theo preset, đặt giữa input và output."""
    e = spec["encode"]
    fps = fps or spec["fps"]
    g = gop_frames(spec, fps)
    args = ["-c:v", e["video_codec"], "-profile:v", e["profile"], "-pix_fmt", e["pix_fmt"], "-preset", e["preset"],
            "-crf", f"{e['crf']:g}", "-bf", str(e["bframes"]), "-sc_threshold", "0", "-g", str(g),
            "-keyint_min", str(g), "-r", f"{fps:g}"]
    if e.get("maxrate_kbps"):
        args += ["-maxrate", f"{e['maxrate_kbps']}k", "-bufsize", f"{e.get('bufsize_kbps') or e['maxrate_kbps'] * 2}k"]
    if e.get("closed_gop"):
        args += ["-x264-params", "open-gop=0"]
    if e.get("colorspace"):
        args += ["-colorspace", e["colorspace"], "-color_primaries", e["colorspace"], "-color_trc", e["colorspace"],
                 "-color_range", "tv"]
    args += ["-c:a", e["audio_codec"], "-ar", str(e["sample_rate"]), "-ac", str(e["channels"]),
             "-b:a", f"{e['audio_bitrate_kbps']}k"]
    if e.get("faststart"):
        args += ["-movflags", "+faststart"]
    return args + list(e.get("extra_args") or [])


def estimate_size_bytes(spec: dict[str, Any], duration_s: float) -> int:
    """Ước lượng cận trên dung lượng: trần bitrate video (maxrate, hoặc 8 Mbps nếu không đặt) + audio."""
    e = spec["encode"]
    video_kbps = e.get("maxrate_kbps") or 8000
    return int(duration_s * (video_kbps + e["audio_bitrate_kbps"]) * 1000 / 8)


def safe_zone(spec: dict[str, Any], width: int | None = None, height: int | None = None) -> dict[str, int] | None:
    """Vùng an toàn theo px, quy đổi sang khung `width x height` (mặc định khung của preset); None = không có."""
    z = spec.get("safe_zone_px")
    if not z:
        return None
    ref = spec["safe_zone_ref"]
    w, h = width or ref["width"], height or ref["height"]
    kx, ky = w / ref["width"], h / ref["height"]
    return {"top": round(z["top"] * ky), "bottom": round(z["bottom"] * ky),
            "left": round(z["left"] * kx), "right": round(z["right"] * kx)}


def title_limit(spec: dict[str, Any]) -> dict[str, int | None]:
    """Giới hạn tiêu đề; nền tảng không công bố (cả hai None) → mặc định chung `DEFAULT_TITLE_MAX` ký tự."""
    t = spec["title"]
    if t.get("max_chars") is None and t.get("max_bytes") is None:
        return {"max_chars": DEFAULT_TITLE_MAX, "max_bytes": None}
    return {"max_chars": t.get("max_chars"), "max_bytes": t.get("max_bytes")}


def plan_parts(total_ms: int, max_s: float, *, min_s: float = 1.0,
               boundaries_ms: list[int] | None = None) -> list[tuple[int, int]]:
    """Chia `[0, total_ms]` thành các phần dài <= `max_s` (≥ `min_s`), càng đều càng tốt.

    Có `boundaries_ms` (ranh giới cảnh/chương) thì mỗi điểm cắt được kéo về ranh giới gần nhất mà vẫn giữ mọi phần
    trong [min_s, max_s]. Một phần duy nhất → `[(0, total_ms)]`.
    """
    max_ms, min_ms = int(max_s * 1000), int(min_s * 1000)
    if total_ms <= max_ms or max_ms <= 0:
        return [(0, total_ms)]
    n = math.ceil(total_ms / max_ms)
    cuts = [0]
    for k in range(1, n):
        ideal = round(total_ms * k / n)
        lo = max(cuts[-1] + min_ms, total_ms - (n - k) * max_ms)  # phần còn lại phải đủ chỗ
        hi = min(cuts[-1] + max_ms, total_ms - (n - k) * min_ms)
        if lo > hi:  # không thể thoả cả min lẫn max → cắt đều
            lo = hi = max(cuts[-1] + 1, min(ideal, total_ms - 1))
        cand = [b for b in (boundaries_ms or []) if lo <= b <= hi]
        cut = min(cand, key=lambda b: abs(b - ideal)) if cand else min(max(ideal, lo), hi)
        cuts.append(cut)
    cuts.append(total_ms)
    return [(a, b) for a, b in zip(cuts, cuts[1:], strict=False) if b > a]


def part_label(index: int, total: int) -> str:
    """"Phần 1/3" (1-based)."""
    return f"Phần {index}/{total}"
