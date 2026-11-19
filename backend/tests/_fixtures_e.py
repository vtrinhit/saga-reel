"""Media tí hon cho test dựng phim (Task E): sinh bằng ffmpeg lavfi, 64×112, ≈1 s."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

FF = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]


def run(args: list[str]) -> None:
    subprocess.run([*FF, *args], check=True, stdin=subprocess.DEVNULL, capture_output=True)


def make_video(path: Path, *, seconds: float = 1.0, fps: int = 30, audio: bool = True, color: str | None = None,
               size: str = "64x112", freq: int = 440) -> Path:
    src = f"color=c={color}:s={size}:d={seconds}:r={fps}" if color else f"testsrc2=s={size}:d={seconds}:r={fps}"
    args = ["-f", "lavfi", "-i", src]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}", "-c:a", "aac"]
    args += ["-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    run(args)
    return path


def make_grok_like(path: Path, tmp: Path) -> Path:
    """Như clip GrokTele: 24 fps, có âm, thêm stream ảnh bìa mjpeg (attached_pic)."""
    base = make_video(tmp / "grok_base.mp4", fps=24)
    cover = make_image(tmp / "grok_cover.jpg")
    run(["-i", str(base), "-i", str(cover), "-map", "0", "-map", "1", "-c", "copy", "-c:v:1", "mjpeg",
         "-disposition:v:1", "attached_pic", str(path)])
    return path


def make_image(path: Path, size: str = "64x112", color: str = "orange") -> Path:
    run(["-f", "lavfi", "-i", f"testsrc2=s={size}:d=1" if color == "test" else f"color=c={color}:s={size}:d=1",
         "-frames:v", "1", str(path)])
    return path


def make_audio(path: Path, seconds: float = 0.5, freq: int = 660) -> Path:
    run(["-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}", "-c:a", "pcm_s16le", "-ar", "48000", str(path)])
    return path


def streams(path: Path) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return json.loads(out.stdout)["streams"]


def stream_durations(path: Path) -> tuple[float, float | None]:
    """(độ dài video, độ dài audio) theo stream (giây)."""
    s = streams(path)
    v = next(x for x in s if x["codec_type"] == "video")
    a = next((x for x in s if x["codec_type"] == "audio"), None)
    return float(v["duration"]), (float(a["duration"]) if a else None)


def frame_count(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return int(out.stdout.decode().strip().split(",")[0])


ASS_HEAD = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,14,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1,0,2,2,2,10,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def fake_ass(cues, w, h) -> str:
    """ASS tối giản (thay `subtitles.to_ass` của Task D trong test)."""

    def ts(ms):
        return f"0:00:{ms / 1000:05.2f}"

    return ASS_HEAD.format(w=w, h=h) + "".join(
        f"Dialogue: 0,{ts(c['start_ms'])},{ts(c['end_ms'])},Default,,0,0,0,,{c['text']}\n" for c in cues)
