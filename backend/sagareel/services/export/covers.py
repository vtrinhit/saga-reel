"""Ảnh bìa 1..3 cho gói xuất: chọn khung đẹp nhất + chữ tiêu đề trong vùng an toàn (post-production.md §6).

Chọn khung: lấy mẫu video thưa (ffmpeg, ảnh nhỏ), chấm điểm bằng Pillow (độ nét, tương phản, không quá tối/sáng),
ưu tiên khung ngay sau điểm đổi cảnh nhưng loại khung đang giữa chuyển cảnh/mờ; khung do người dùng đánh dấu
(`marks_ms`) đứng đầu. Chữ: tối đa 5 từ, phông Be Vietnam Pro nếu có ở `defaults/fonts/` (Task D giao), không thì phông
mặc định của Pillow. Mọi ffmpeg đi qua `media.ffmpeg.run`.
"""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import anyio
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from sagareel.media import ffmpeg
from sagareel.services import llm
from sagareel.services.export import platforms

logger = logging.getLogger("sagareel.export")

FONTS_DIR = llm.DEFAULTS_DIR / "fonts"
MAX_SAMPLES = 120
SAMPLE_W = 96
CUT_DIFF = 18.0  # chênh lệch trung bình 0-255 giữa hai mẫu liên tiếp coi là đổi cảnh
MAX_TITLE_WORDS = 5


def find_font(bold: bool = True) -> Path | None:
    """Be Vietnam Pro trong `defaults/fonts/` (ưu tiên Bold); không có → None."""
    if not FONTS_DIR.is_dir():
        return None
    files = sorted(FONTS_DIR.glob("BeVietnamPro*.ttf")) or sorted(FONTS_DIR.glob("*.ttf"))
    pick = [f for f in files if ("Bold" in f.stem) == bold and "Italic" not in f.stem]
    return (pick or files or [None])[0]


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_font()
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            logger.warning("Không mở được phông %s; dùng phông mặc định", path.name)
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow cũ không nhận size
        return ImageFont.load_default()


# --- Chọn khung -----------------------------------------------------------------------------------------------------


def _features(img: Image.Image) -> dict[str, float]:
    g = img.convert("L")
    st = ImageStat.Stat(g)
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    return {"bright": st.mean[0], "contrast": st.stddev[0], "sharp": edges}


def _score(f: Mapping[str, float], t_s: float, dur_s: float, scene_start: bool) -> float:
    s = f["sharp"] * 1.0 + f["contrast"] * 0.5
    if f["bright"] < 25 or f["bright"] > 235:
        s *= 0.3
    if scene_start:
        s *= 1.25
    if t_s < 0.3 or t_s > dur_s - 0.3:  # đầu/cuối thường là fade
        s *= 0.8
    return s


def rank_samples(frames: Sequence[Image.Image], times_s: Sequence[float], dur_s: float) -> list[tuple[float, float]]:
    """`[(điểm, giây)]` giảm dần. Mẫu nằm ngay tại điểm đổi cảnh (có thể đang giữa chuyển cảnh) bị hạ điểm."""
    diffs = [0.0] + [ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0]
                     for a, b in zip(frames, frames[1:], strict=False)]
    out: list[tuple[float, float]] = []
    for i, (img, t) in enumerate(zip(frames, times_s, strict=True)):
        scene_start = i > 0 and diffs[i - 1] > CUT_DIFF
        score = _score(_features(img), t, dur_s, scene_start or i == 0)
        # Khung đầu tiên sau cú cắt/mờ có thể đang giữa chuyển cảnh: hạ điểm (ưu tiên khung kế tiếp), không loại hẳn
        # để video cắt liên tục vẫn có bìa.
        out.append((score * 0.3 if diffs[i] > CUT_DIFF and i > 0 else score, t))
    out.sort(reverse=True)
    return out


def pick_times(ranked: Sequence[tuple[float, float]], n: int, dur_s: float,
               marks_ms: Sequence[int] = ()) -> list[int]:
    """Chọn <= n mốc (ms) cách nhau đủ xa; khung người dùng đánh dấu đứng đầu."""
    gap = max(0.2, min(1.0, dur_s / (2 * n)))
    chosen: list[float] = []
    for m in marks_ms:
        if 0 <= m <= dur_s * 1000 and len(chosen) < n:
            chosen.append(m / 1000)
    for _, t in ranked:
        if len(chosen) >= n:
            break
        if all(abs(t - c) >= gap for c in chosen):
            chosen.append(t)
    return [round(t * 1000) for t in chosen]


async def _extract(video: Path, t_s: float, out: Path, scale: str | None = None) -> None:
    vf = ["-vf", scale] if scale else []
    with ffmpeg.atomic_output(out) as tmp:
        await ffmpeg.run(["-ss", f"{t_s:.3f}", "-i", str(video), "-frames:v", "1", *vf, "-q:v", "2", str(tmp)],
                         timeout=120)


async def candidate_times(video: Path, duration_ms: int, n: int = 3, marks_ms: Sequence[int] = ()) -> list[int]:
    dur_s = max(duration_ms / 1000, 0.1)
    fps = min(4.0, max(0.2, MAX_SAMPLES / dur_s))
    with tempfile.TemporaryDirectory(prefix="srcover_") as tmp:
        d = Path(tmp)
        await ffmpeg.run(["-i", str(video), "-an", "-vf", f"fps={fps:.4f},scale={SAMPLE_W}:-2", "-fps_mode", "vfr",
                          str(d / "s_%04d.png")], timeout=600)
        files = sorted(d.glob("s_*.png"))

        def work() -> list[tuple[float, float]]:
            frames = []
            for f in files:
                with Image.open(f) as im:
                    frames.append(im.copy())
            times = [min(i / fps, dur_s) for i in range(len(frames))]
            return rank_samples(frames, times, dur_s) if frames else []

        ranked = await anyio.to_thread.run_sync(work)
    times = pick_times(ranked, n, dur_s, marks_ms)
    return times or [0]


# --- Vẽ bìa ---------------------------------------------------------------------------------------------------------


def _wrap(words: Sequence[str], font, max_w: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and font.getlength(trial) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def draw_title(img: Image.Image, title: str, zone: Mapping[str, int] | None) -> Image.Image:
    """Chữ tiêu đề (<= 5 từ) trong vùng an toàn: trắng viền đen dày (Fontsize 96 / Outline 8 theo cạnh ngắn 1080)."""
    words = title.split()[:MAX_TITLE_WORDS]
    if not words:
        return img
    w, h = img.size
    z = zone or {"top": round(h * 0.08), "bottom": round(h * 0.08), "left": round(w * 0.06), "right": round(w * 0.06)}
    scale = min(w, h) / 1080
    size = max(12, round(96 * scale))
    outline = max(1, round(8 * scale))
    area_w = w - z["left"] - z["right"]
    area_h = h - z["top"] - z["bottom"]
    font = load_font(size)
    lines = _wrap(words, font, area_w - 2 * outline)
    while len(lines) * size * 1.2 > area_h and size > 14:  # quá cao: thu nhỏ cho vừa vùng an toàn
        size = round(size * 0.9)
        font = load_font(size)
        lines = _wrap(words, font, area_w - 2 * outline)
    draw = ImageDraw.Draw(img)
    line_h = round(size * 1.2)
    y = z["top"] + max(0, (area_h - len(lines) * line_h) // 3)
    for line in lines:
        x = z["left"] + (area_w - font.getlength(line)) / 2
        draw.text((x, y), line, font=font, fill="white", stroke_width=outline, stroke_fill="black")
        y += line_h
    return img


def fit_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """Cắt giữa theo tỉ lệ đích rồi đổi cỡ (khung video đã đúng tỉ lệ nên hầu như không cắt)."""
    img = img.convert("RGB")
    sw, sh = img.size
    target = width / height
    if abs(sw / sh - target) > 0.01:
        if sw / sh > target:
            nw = round(sh * target)
            img = img.crop(((sw - nw) // 2, 0, (sw - nw) // 2 + nw, sh))
        else:
            nh = round(sw / target)
            img = img.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
    return img.resize((width, height), Image.LANCZOS)


def save_jpeg(img: Image.Image, out: Path, max_mb: float) -> int:
    """Ghi JPEG, hạ chất lượng dần cho tới khi <= `max_mb`. Trả chất lượng đã dùng."""
    limit = int(max_mb * 1024 * 1024)
    for q in (92, 85, 78, 70, 60, 50):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= limit or q == 50:
            out.write_bytes(buf.getvalue())
            return q
    return 50  # pragma: no cover


def render_cover(frame: Path, out: Path, spec: Mapping[str, Any], title: str | None) -> None:
    c = spec["cover"]
    with Image.open(frame) as im:
        img = fit_cover(im, c["width"], c["height"])
    if title:
        draw_title(img, title, platforms.safe_zone(dict(spec), c["width"], c["height"]))
    save_jpeg(img, out, c["max_mb"])


async def make_covers(video: Path, out_dir: Path, spec: Mapping[str, Any], *, duration_ms: int, title: str | None,
                      n: int = 3, marks_ms: Sequence[int] = ()) -> list[dict[str, Any]]:
    """Tạo `cover-1..n.jpg` trong `out_dir`; trả `[{file, timestamp_ms}]` (n thực tế có thể < `n` với video rất ngắn)."""
    n = max(1, min(3, n))
    times = await candidate_times(video, duration_ms, n, marks_ms)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="srcover_") as tmp:
        for i, t in enumerate(times[:n], 1):
            raw = Path(tmp) / f"f{i}.png"
            await _extract(video, min(t / 1000, max(duration_ms / 1000 - 0.05, 0)), raw)
            dest = out_dir / f"cover-{i}.jpg"
            await asyncio.to_thread(render_cover, raw, dest, spec, title)
            result.append({"file": dest.name, "timestamp_ms": t})
    return result

