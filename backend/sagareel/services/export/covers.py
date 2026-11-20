"""Ảnh bìa 1..3 cho gói xuất: chọn khung đẹp nhất + chữ tiêu đề trong vùng an toàn (post-production.md §6).

Nguồn khung (sạch, không phụ đề burn-in): ưu tiên ảnh take đã chọn của clip (ảnh gốc độ phân giải đầy đủ), rồi khung
giữa video take của clip; chỉ khi không có cả hai mới lấy mẫu từ video thành phẩm. Chấm điểm bằng Pillow (độ nét,
tương phản, không quá tối/sáng, có nhân vật thì cộng điểm); với video thành phẩm thì ưu tiên khung ngay sau điểm đổi
cảnh nhưng loại khung đang giữa chuyển cảnh/mờ; khung do người dùng đánh dấu (`marks_ms`) đứng đầu.

Chữ: tối đa 5 từ, đặt trong dải ít chi tiết nhất (trên 10–24% hoặc dưới-giữa 52–64%, kẹp trong vùng an toàn) để không
đè mặt nhân vật, có nền tối chuyển màu mờ dần cho dễ đọc. Phông Be Vietnam Pro nếu có ở `defaults/fonts/`, không thì
phông mặc định của Pillow. Mọi ffmpeg đi qua `media.ffmpeg.run`.
"""

from __future__ import annotations

import asyncio
import io
import itertools
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
TITLE_BANDS = ((0.10, 0.24), (0.52, 0.64))  # dải đặt chữ (tỉ lệ chiều cao): trên, dưới-giữa — chọn dải ít chi tiết
BAND_PAD = 0.025  # nền tối tràn ra ngoài dải chữ (tỉ lệ chiều cao)
SHADE_ALPHA = 150  # độ đậm tối đa của nền tối sau chữ (0-255)
CHARACTER_BONUS = 1.3
ENERGY_W = 270  # đo độ chi tiết trên ảnh thu nhỏ


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
                     for a, b in itertools.pairwise(frames)]
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


def _zone(img: Image.Image, zone: Mapping[str, int] | None) -> dict[str, int]:
    w, h = img.size
    return dict(zone) if zone else {"top": round(h * 0.08), "bottom": round(h * 0.08), "left": round(w * 0.06),
                                    "right": round(w * 0.06)}


def detail_energy(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Độ chi tiết của vùng `box` (cạnh + độ lệch sáng) — mặt người, chữ, lá cây cho số lớn; trời, tường cho số nhỏ."""
    w, h = img.size
    k = min(1.0, ENERGY_W / w)
    small = img.convert("L").resize((max(1, round(w * k)), max(1, round(h * k))))
    x0, y0, x1, y1 = (round(v * k) for v in box)
    crop = small.crop((x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)))
    return ImageStat.Stat(crop.filter(ImageFilter.FIND_EDGES)).mean[0] + 0.25 * ImageStat.Stat(crop).stddev[0]


def pick_band(img: Image.Image, zone: Mapping[str, int] | None) -> tuple[int, int]:
    """Dải (y0, y1) đặt tiêu đề: dải ít chi tiết nhất trong `TITLE_BANDS` (kẹp vào vùng an toàn); bằng nhau → dải trên."""
    w, h = img.size
    z = _zone(img, zone)
    bands = []
    for a, b in TITLE_BANDS:
        y0, y1 = max(round(h * a), z["top"]), min(round(h * b), h - z["bottom"])
        if y1 - y0 >= h * 0.05:
            bands.append((y0, y1))
    if not bands:
        return z["top"], h - z["bottom"]
    scored = [(round(detail_energy(img, (z["left"], y0, w - z["right"], y1)), 1), i, (y0, y1))
              for i, (y0, y1) in enumerate(bands)]
    return min(scored)[2]


def _ramp(n: int, feather: int) -> list[int]:
    """Dải 0..255..0 dài `n`, mờ dần `feather` px ở hai đầu."""
    f = max(1, min(feather, n // 2))
    return [round(255 * min(1.0, (i + 0.5) / f, (n - i - 0.5) / f)) for i in range(n)]


def _shade(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Nền tối mờ dần ở bốn cạnh sau chữ (không có cạnh cứng)."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 2 or bh < 2:
        return
    col = Image.new("L", (1, bh))
    col.putdata(_ramp(bh, bh // 2))
    row = Image.new("L", (bw, 1))
    row.putdata(_ramp(bw, bw // 4))
    mask = ImageChops.multiply(col.resize((bw, bh)), row.resize((bw, bh))).point(lambda v: v * SHADE_ALPHA // 255)
    region = img.crop(box)
    img.paste(Image.composite(Image.new("RGB", (bw, bh), (0, 0, 0)), region.convert("RGB"), mask), (x0, y0))


def draw_title(img: Image.Image, title: str, zone: Mapping[str, int] | None) -> Image.Image:
    """Chữ tiêu đề (<= 5 từ) trong dải ít chi tiết của vùng an toàn: trắng viền đen (Fontsize 96 / Outline 6 theo cạnh
    ngắn 1080) trên nền tối mờ dần."""
    words = title.split()[:MAX_TITLE_WORDS]
    if not words:
        return img
    w, h = img.size
    z = _zone(img, zone)
    y_top, y_bot = pick_band(img, z)
    scale = min(w, h) / 1080
    size = max(12, round(96 * scale))
    outline = max(1, round(6 * scale))
    area_w = w - z["left"] - z["right"]
    area_h = y_bot - y_top
    font = load_font(size)
    lines = _wrap(words, font, area_w - 2 * outline)
    while len(lines) * size * 1.2 > area_h and size > 14:  # quá cao: thu nhỏ cho vừa dải
        size = round(size * 0.9)
        font = load_font(size)
        lines = _wrap(words, font, area_w - 2 * outline)
    line_h = round(size * 1.2)
    y = y_top + max(0, (area_h - len(lines) * line_h) // 2)
    pad = round(h * BAND_PAD)
    _shade(img, (z["left"], max(z["top"], y_top - pad), w - z["right"], min(h - z["bottom"], y_bot + pad)))
    draw = ImageDraw.Draw(img)
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


def rank_stills(stills: Sequence[Mapping[str, Any]]) -> list[tuple[float, int]]:
    """`[(điểm, chỉ số)]` giảm dần cho ảnh tĩnh sạch: độ nét + tương phản, không quá tối/sáng, có nhân vật thì cộng."""
    out: list[tuple[float, int]] = []
    for i, st in enumerate(stills):
        try:
            with Image.open(st["frame"]) as im:
                im.thumbnail((ENERGY_W, ENERGY_W * 2))
                f = _features(im)
        except OSError as e:
            logger.warning("Không đọc được ảnh làm bìa %s: %s", st.get("frame"), e)
            continue
        score = f["sharp"] + f["contrast"] * 0.5
        if f["bright"] < 25 or f["bright"] > 235:
            score *= 0.3
        if st.get("has_character"):
            score *= CHARACTER_BONUS
        out.append((score, i))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def _still_ts(st: Mapping[str, Any], scale: float, duration_ms: int) -> int:
    mid = (int(st.get("start_ms") or 0) + int(st.get("end_ms") or st.get("start_ms") or 0)) / 2
    return max(0, min(round(mid * scale), max(duration_ms - 1, 0)))


async def _clean_frames(stills: Sequence[Mapping[str, Any]], tmp: Path) -> list[dict[str, Any]]:
    """Khung sạch từ ảnh take (dùng thẳng file) hoặc video take (trích khung giữa clip, không phải khung đầu)."""
    out: list[dict[str, Any]] = []
    for i, st in enumerate(stills):
        path = Path(st["path"])
        if not path.is_file():
            continue
        if st.get("kind") == "video":
            frame = tmp / f"take_{i}.png"
            mid_s = max(0.0, (int(st.get("end_ms") or 0) - int(st.get("start_ms") or 0)) / 2000)
            try:
                await _extract(path, mid_s, frame)
            except Exception as e:  # noqa: BLE001 — một take hỏng không chặn bìa
                logger.warning("Không trích được khung từ video take của clip #%s: %s", st.get("clip_id"), e)
                continue
            out.append({**st, "frame": frame, "source": "video_take"})
        else:
            out.append({**st, "frame": path, "source": "image_take"})
    return out


async def make_covers(video: Path, out_dir: Path, spec: Mapping[str, Any], *, duration_ms: int, title: str | None,
                      n: int = 3, marks_ms: Sequence[int] = (), stills: Sequence[Mapping[str, Any]] = (),
                      clip_total_ms: int | None = None) -> list[dict[str, Any]]:
    """Tạo `cover-1..n.jpg` trong `out_dir`; trả `[{file, timestamp_ms, source, clip_id}]` (n thực tế có thể < `n`).

    `stills` = `[{clip_id, path, kind: image|video, has_character, start_ms, end_ms}]` theo thời gian clip (cộng dồn
    thời lượng clip, tổng `clip_total_ms`); có ảnh/khung sạch → không lấy khung từ `video` (đã burn phụ đề).
    `timestamp_ms` = điểm giữa clip quy về thời gian của `video` (TikTok chọn bìa theo mốc này)."""
    n = max(1, min(3, n))
    out_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="srcover_") as tmp:
        frames = await _clean_frames(stills, Path(tmp)) if stills else []
        if frames:
            total = clip_total_ms or max(int(f.get("end_ms") or 0) for f in frames) or duration_ms
            scale = duration_ms / total if total else 1.0
            ranked = await anyio.to_thread.run_sync(rank_stills, frames)
            order = [i for _, i in ranked]
            marked = []
            for m in marks_ms:  # khung người dùng đánh dấu → clip chứa mốc đó đứng đầu
                hit = next((i for i, f in enumerate(frames)
                            if int(f.get("start_ms") or 0) * scale <= m < int(f.get("end_ms") or 0) * scale), None)
                if hit is not None and hit in order and hit not in marked:
                    marked.append(hit)
            order = marked + [i for i in order if i not in marked]
            for k, i in enumerate(order[:n], 1):
                f = frames[i]
                dest = out_dir / f"cover-{k}.jpg"
                await asyncio.to_thread(render_cover, Path(f["frame"]), dest, spec, title)
                result.append({"file": dest.name, "timestamp_ms": _still_ts(f, scale, duration_ms),
                               "source": f["source"], "clip_id": f.get("clip_id")})
            return result
        if stills:
            logger.warning("Không có ảnh take/video take dùng được; lấy bìa từ video thành phẩm (có thể dính phụ đề)")
        times = await candidate_times(video, duration_ms, n, marks_ms)
        for i, t in enumerate(times[:n], 1):
            raw = Path(tmp) / f"f{i}.png"
            await _extract(video, min(t / 1000, max(duration_ms / 1000 - 0.05, 0)), raw)
            dest = out_dir / f"cover-{i}.jpg"
            await asyncio.to_thread(render_cover, raw, dest, spec, title)
            result.append({"file": dest.name, "timestamp_ms": t, "source": "final_video", "clip_id": None})
    return result
