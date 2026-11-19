"""Phát hiện đổi cảnh, trích khung hình, lưới keyframe (contact sheet) — chỉ dùng cho phân tích, không đưa khung gốc ra
đầu ra (spec §14.5–14.6).

Đổi cảnh: `select='gt(scene,T)',showinfo` qua `media.ffmpeg.run` (đọc `pts_time` trong stderr). Cảnh ngắn hơn
`min_scene_s` bị gộp vào cảnh trước (bỏ điểm cắt tạo ra cảnh quá ngắn).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from sagareel.media import ffmpeg

_PTS_RE = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")
MAX_SHEET_FRAMES = 16
HOOK_S = 3.0


@dataclass(frozen=True)
class Scene:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def mid_s(self) -> float:
        return (self.start_s + self.end_s) / 2


async def detect_cuts(path: Path, threshold: float = 0.3, *, cancel=None) -> list[float]:
    """Thời điểm (giây, tăng dần) các điểm đổi cảnh. Không tính khung đầu tiên."""
    if not 0 < threshold < 1:
        raise ValueError("threshold phải nằm trong (0, 1)")
    tail = await ffmpeg.run(
        ["-i", str(path), "-an", "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        timeout=1800, cancel=cancel, tail_lines=1_000_000,
    )
    cuts = sorted({round(float(m.group(1)), 3) for m in _PTS_RE.finditer(tail)})
    return [c for c in cuts if c > 0.05]


def build_scenes(cuts: list[float], duration_s: float, min_scene_s: float = 1.0) -> list[Scene]:
    """Chia [0, duration] theo điểm cắt; cảnh ngắn hơn `min_scene_s` gộp vào cảnh liền trước (cảnh cuối ngắn → gộp)."""
    if duration_s <= 0:
        return []
    bounds = [0.0]
    for c in cuts:
        if c >= duration_s:
            break
        if c - bounds[-1] >= min_scene_s:
            bounds.append(c)
    if len(bounds) > 1 and duration_s - bounds[-1] < min_scene_s:
        bounds.pop()
    bounds.append(duration_s)
    return [Scene(a, b) for a, b in itertools.pairwise(bounds)]


async def extract_frame(src: Path, at_s: float, out: Path, *, width: int = 480, cancel=None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run(["-ss", f"{max(0.0, at_s):.3f}", "-i", str(src), "-an", "-frames:v", "1",
                      "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", str(out)], timeout=120, cancel=cancel)
    if not out.is_file():  # -ss vượt quá cuối file → không có khung
        raise ffmpeg.FFmpegError(f"Không trích được khung hình tại {at_s:.2f}s.", code="ffmpeg_no_output")
    return out


def pick_times(duration_s: float, scenes: list[Scene], max_frames: int = MAX_SHEET_FRAMES) -> list[float]:
    """Mốc lấy keyframe: 3 khung trong 3 giây đầu (hook) + giữa các cảnh/đều theo thời gian, tối đa `max_frames`."""
    max_frames = max(1, min(max_frames, MAX_SHEET_FRAMES))
    hook_end = min(HOOK_S, duration_s)
    n_hook = min(3, max_frames)
    hook = [round(hook_end * (i + 0.5) / n_hook, 3) for i in range(n_hook)]
    rest = max_frames - n_hook
    body: list[float] = []
    if rest > 0 and duration_s > hook_end:
        mids = [sc.mid_s for sc in scenes if sc.mid_s > hook_end]
        if len(mids) > rest:  # lấy đều trong danh sách giữa-cảnh
            mids = [mids[int(i * len(mids) / rest)] for i in range(rest)]
        if not mids:
            mids = [hook_end + (duration_s - hook_end) * (i + 0.5) / rest for i in range(rest)]
        body = [round(m, 3) for m in mids]
    times = sorted({*hook, *body})
    return [min(t, max(0.0, duration_s - 0.05)) for t in times]


def contact_sheet(frames: list[Path], out: Path, *, cols: int = 4, cell_w: int = 320) -> Path:
    """Lưới JPEG các khung theo thứ tự thời gian (trái→phải, trên→dưới)."""
    if not frames:
        raise ValueError("Cần ít nhất một khung hình.")
    imgs = []
    for f in frames:
        with Image.open(f) as im:
            imgs.append(im.convert("RGB").copy())
    cell_h = max(round(im.height * cell_w / im.width) for im in imgs)
    cols = max(1, min(cols, len(imgs)))
    rows = -(-len(imgs) // cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (16, 16, 16))
    for i, im in enumerate(imgs):
        cell = im.resize((cell_w, round(im.height * cell_w / im.width)))
        sheet.paste(cell, ((i % cols) * cell_w, (i // cols) * cell_h))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=85)
    return out
