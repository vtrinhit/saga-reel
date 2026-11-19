"""Đọc thông tin file media bằng ffprobe (JSON). Đồng bộ — code async gọi qua `anyio.to_thread.run_sync`."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sagareel.media.ffmpeg import FFmpegError, popen_kwargs

PROBE_TIMEOUT_S = 60


@dataclass(frozen=True)
class ProbeInfo:
    duration_ms: int  # 0 nếu không có thời lượng (ảnh tĩnh)
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None
    has_audio: bool
    size: int


def ffprobe_exe() -> str:
    exe = shutil.which("ffprobe")
    if exe is None:
        raise FFmpegError(
            "Không tìm thấy ffprobe trên PATH. Hãy cài ffmpeg (kèm ffprobe) rồi khởi động lại ứng dụng.",
            code="ffmpeg_missing",
        )
    return exe


def _rate(value: str | None) -> float | None:
    if not value or "/" not in value:
        return None
    num, _, den = value.partition("/")
    try:
        n, d = float(num), float(den)
    except ValueError:
        return None
    return round(n / d, 3) if d and n else None


def _seconds(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def probe(path: Path) -> ProbeInfo:
    path = Path(path)
    cmd = [ffprobe_exe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=PROBE_TIMEOUT_S, **popen_kwargs()
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"ffprobe đọc {path.name} quá {PROBE_TIMEOUT_S} giây.", code="probe_failed") from None
    stderr = proc.stderr.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe không đọc được {path.name}.", stderr[-4000:], code="probe_failed")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        raise FFmpegError(f"ffprobe trả kết quả không hợp lệ cho {path.name}.", stderr, code="probe_failed") from None

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _seconds(fmt.get("duration"))
    if duration is None:
        durations = [d for d in (_seconds(s.get("duration")) for s in streams) if d is not None]
        duration = max(durations, default=None)
    is_still = video is not None and video.get("codec_name") in {"png", "mjpeg", "webp", "bmp", "gif", "tiff"} \
        and (fmt.get("format_name") or "").endswith("_pipe")

    fps = None
    if video is not None and not is_still:
        fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))

    return ProbeInfo(
        duration_ms=0 if duration is None or is_still else round(duration * 1000),
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=fps,
        vcodec=video.get("codec_name") if video else None,
        acodec=audio.get("codec_name") if audio else None,
        has_audio=audio is not None,
        size=path.stat().st_size,
    )
