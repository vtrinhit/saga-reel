"""Ghép khối + lượt âm thanh cuối (spec §12.3).

1. `join(paths)`: kiểm chữ ký ffprobe của mọi khối (codec, kích thước, pix_fmt, fps, time_base, mẫu âm, kênh) → concat
   demuxer `-c copy` → kiểm tổng thời lượng.
2. `final_audio(...)`: nhạc nền + ducking (bộ lọc của Task D qua `Builders.final_audio_filter`, chỉ khi có nhạc) →
   loudnorm **hai lượt** (đo rồi áp tuyến tính, `aresample=48000`) → MP4 `-c:v copy`, AAC theo nền tảng, faststart.
   Lượt 2 không `linear` / lệch đích > 1 LU / TP > đích → cảnh báo (post-production.md §2.3).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio

from sagareel.core.errors import AppError
from sagareel.media import ffmpeg
from sagareel.media.probe import ffprobe_exe
from sagareel.render.builders import DEFAULT, Builders
from sagareel.render.filters import secs

LOUDNORM_TARGET = {"I": -14.0, "TP": -1.0, "LRA": 11.0}
_SIG_KEYS = ("codec_type", "codec_name", "profile", "width", "height", "pix_fmt", "r_frame_rate", "time_base",
             "sample_rate", "channels", "sample_fmt")


def _ffprobe_json(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [ffprobe_exe(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False, **ffmpeg.popen_kwargs(),
    )
    if proc.returncode != 0:
        raise ffmpeg.FFmpegError(f"ffprobe không đọc được {Path(path).name}.",
                                 proc.stderr.decode("utf-8", "replace")[-2000:], code="probe_failed")
    return json.loads(proc.stdout.decode("utf-8", "replace") or "{}")


def signature(path: Path) -> tuple:
    data = _ffprobe_json(path)
    return tuple(tuple(s.get(k) for k in _SIG_KEYS) for s in data.get("streams") or [])


def duration_ms(path: Path) -> int:
    data = _ffprobe_json(path)
    try:
        return round(float((data.get("format") or {}).get("duration")) * 1000)
    except (TypeError, ValueError):
        return 0


def _list_line(p: Path) -> str:
    return "file '" + str(Path(p).resolve()).replace("\\", "/").replace("'", "'\\''") + "'\n"


def check_signatures(paths: list[Path]) -> None:
    sigs = [signature(p) for p in paths]
    for i, s in enumerate(sigs[1:], start=1):
        if s != sigs[0]:
            raise AppError("render_chunk_mismatch",
                           f"Khối {i + 1} khác thông số mã hoá với khối đầu nên không ghép thẳng được.",
                           hint="Xoá cache khối (render.cache_clear) rồi dựng lại.", status=500)


async def join(
    paths: list[Path],
    out_path: Path,
    *,
    expected_ms: int | None = None,
    tolerance_ms: int = 80,
    cancel: asyncio.Event | None = None,
) -> Path:
    """Concat demuxer `-c copy` (kiểm chữ ký trước, tổng thời lượng sau). Dùng cho khối `.mov` và stem `.wav`."""
    if not paths:
        raise AppError("render_empty", "Không có khối nào để ghép.", status=422)
    await anyio.to_thread.run_sync(check_signatures, list(paths))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lst = out_path.with_name(out_path.stem + ".concat.txt")
    lst.write_text("".join(_list_line(p) for p in paths), encoding="utf-8")
    try:
        with ffmpeg.atomic_output(out_path) as part:
            await ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0", "-c", "copy", str(part)],
                             total_ms=expected_ms, cancel=cancel, timeout=3600)
    finally:
        lst.unlink(missing_ok=True)
    if expected_ms is not None:
        got = await anyio.to_thread.run_sync(duration_ms, out_path)
        if abs(got - expected_ms) > tolerance_ms + 5 * len(paths):
            raise AppError("render_duration_mismatch",
                           f"Tổng thời lượng sau ghép ({got} ms) lệch dự kiến ({expected_ms} ms).", status=500)
    return out_path


def parse_loudnorm(tail: str) -> dict[str, Any] | None:
    """JSON cuối cùng trong stderr của `loudnorm print_format=json`."""
    blocks = re.findall(r"\{[^{}]*\}", tail or "", flags=re.DOTALL)
    for raw in reversed(blocks):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "input_i" in data:
            return data
    return None


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _ln(target: dict[str, float]) -> str:
    return f"loudnorm=I={target['I']:g}:TP={target['TP']:g}:LRA={target['LRA']:g}"


async def final_audio(
    video: Path,
    out_path: Path,
    *,
    stem: Path | None = None,
    music: list[dict[str, Any]] | None = None,
    total_ms: int,
    audio_bitrate: str = "192k",
    target: dict[str, float] | None = None,
    builders: Builders = DEFAULT,
    loudnorm: bool = True,
    cancel: asyncio.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Lượt âm thanh cuối → MP4. Trả `{loudness: {...}, warnings: [...]}`."""
    target = {**LOUDNORM_TARGET, **(target or {})}
    music = list(music or [])
    warnings: list[dict[str, Any]] = []
    inputs = ["-i", str(video)]
    if music:
        if stem is None:
            raise AppError("render_internal", "Thiếu stem thoại cho ducking.", status=500)
        inputs += ["-i", str(stem)]
        items = []
        for k, m in enumerate(music, start=2):
            if (m.get("params") or {}).get("loop"):
                inputs += ["-stream_loop", "-1"]
            inputs += ["-i", str(m["path"])]
            items.append({"input": f"{k}:a", "start_ms": m["start_ms"], "end_ms": m["end_ms"],
                          "params": dict(m.get("params") or {})})
        fc, maps = builders.final_audio_filter(
            "1:a", items, [], {"bed": "0:a", "total_ms": total_ms, "loudnorm": False, "target": target})
        label = maps[0] if maps[0].startswith("[") else f"[{maps[0]}]"
        premix = f"{fc};"
    else:
        premix, label = "", "[0:a]"
    dur = ["-t", secs(total_ms)]

    loudness: dict[str, Any] = {}
    ln_filter = "anull"
    if loudnorm:
        tail = await ffmpeg.run(
            [*inputs, "-filter_complex", f"{premix}{label}{_ln(target)}:print_format=json[m]", "-map", "[m]",
             *dur, "-f", "null", "-"],
            total_ms=total_ms, cancel=cancel, timeout=3600,
        )
        meas = parse_loudnorm(tail)
        keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        if meas is None or not all(_finite(meas.get(k)) for k in keys):
            warnings.append({"code": "loudnorm_skipped",
                             "message": "Không đo được độ to (âm quá nhỏ hoặc im lặng); bỏ qua chuẩn hoá loudness."})
        else:
            loudness["measured"] = {k: float(meas[k]) for k in keys}
            ln_filter = (
                f"{_ln(target)}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
                f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
                f"offset={meas['target_offset']}:linear=true:print_format=json"
            )
    graph = f"{premix}{label}{ln_filter},aresample=48000[aout]"
    with ffmpeg.atomic_output(out_path) as part:
        tail = await ffmpeg.run(
            [*inputs, "-filter_complex", graph, "-map", "0:v:0", "-map", "[aout]", *dur, "-c:v", "copy",
             "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(part)],
            total_ms=total_ms, on_progress=on_progress, cancel=cancel, timeout=3600,
        )
    if loudness.get("measured"):
        res = parse_loudnorm(tail) or {}
        loudness["normalization_type"] = res.get("normalization_type")
        if _finite(res.get("output_i")):
            loudness["output_i"] = float(res["output_i"])
        if _finite(res.get("output_tp")):
            loudness["output_tp"] = float(res["output_tp"])
        if res.get("normalization_type") not in (None, "linear"):
            warnings.append({"code": "loudnorm_dynamic",
                             "message": "loudnorm đã chuyển sang chế độ dynamic (nén); nên kiểm tra lại độ to."})
        if "output_i" in loudness and abs(loudness["output_i"] - target["I"]) > 1:
            warnings.append({"code": "loudness_off_target",
                             "message": f"Độ to {loudness['output_i']:.1f} LUFS lệch đích {target['I']:g} LUFS."})
        if "output_tp" in loudness and loudness["output_tp"] > target["TP"]:
            warnings.append({"code": "true_peak_high", "message": "True peak vượt đích."})
    return {"loudness": loudness, "warnings": warnings}
