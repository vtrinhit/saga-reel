"""Chuẩn hoá clip video → segment chuẩn (spec §12.1, post-production.md §5.1 `normalize_clip` + `grade`).

Segment: W×H của hồ sơ, 30 fps CFR, yuv420p BT.709 tv, PCM 48 kHz stereo (`.mov`), độ dài đúng N khung (âm thanh
đúng N × 1600 mẫu). Chọn `0:v:0` / `0:a:0` (bỏ stream ảnh bìa mjpeg của GrokTele); không có âm → `anullsrc`.
Áp trim/tốc độ; âm gốc Grok `keep` / `mute` / `ambient` (−18 dB). `keep` có thoại: `gain_db` (đo ở
`render/trim.py`) đưa đoạn nói của từng clip về −16 LUFS như câu TTS, để các clip không câu to câu nhỏ.
Cache theo fingerprint ở `cache/segments/`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import anyio

from sagareel.core.errors import AppError
from sagareel.media import ffmpeg
from sagareel.media.probe import probe
from sagareel.render import cache
from sagareel.render.filters import atempo_chain, samples, secs
from sagareel.render.profile import GRADE_BASE, Profile, coerce

NORMALIZE_VERSION = 1
GROK_MODES = ("keep", "mute", "ambient")
AMBIENT_DB = -18


def normalize_vf(w: int, h: int, fps: int) -> str:
    """Đúng chuỗi `normalize_clip.vf` của post-production.md (trừ `format=yuv420p`, thêm ở cuối chuỗi)."""
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos:in_range=auto:out_range=tv:"
        f"out_color_matrix=bt709,crop={w}:{h},setsar=1,fps={fps}:round=near"
    )


@dataclass(frozen=True)
class SourceMedia:
    path: Path
    sha256: str
    has_audio: bool | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class ClipParams:
    trim_in_ms: int = 0
    trim_out_ms: int = 0
    speed: float = 1.0
    grok_audio: str = "ambient"
    dur_ms: int | None = None  # None = độ dài có sẵn sau trim/tốc độ
    gain_db: float = 0.0  # âm gốc `keep` có thoại: gain đưa đoạn nói về −16 LUFS (render/trim.loudness_gain)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def coerce_media(media: SourceMedia | Mapping[str, Any] | Any) -> SourceMedia:
    if isinstance(media, SourceMedia):
        return media
    path = _get(media, "path")
    if path is None or not Path(path).is_absolute():
        raise AppError("render_bad_media", "Media đầu vào cần đường dẫn tuyệt đối.", status=422)
    sha = _get(media, "sha256") or cache.fingerprint({"path": str(path), "size": Path(path).stat().st_size})
    meta = _get(media, "meta") or {}
    has_audio = _get(media, "has_audio", (meta.get("probe") or {}).get("has_audio") if meta else None)
    return SourceMedia(Path(path), sha, has_audio, _get(media, "duration_ms"))


def coerce_clip(clip: ClipParams | Mapping[str, Any] | Any) -> ClipParams:
    if isinstance(clip, ClipParams):
        return clip
    if clip is None:
        return ClipParams()
    mode = str(_get(clip, "grok_audio", "ambient") or "ambient")
    dur = _get(clip, "dur_ms")
    return ClipParams(
        trim_in_ms=int(_get(clip, "trim_in_ms", 0) or 0),
        trim_out_ms=int(_get(clip, "trim_out_ms", 0) or 0),
        speed=float(_get(clip, "speed", 1.0) or 1.0),
        grok_audio=mode if mode in GROK_MODES else "ambient",
        dur_ms=int(dur) if dur is not None else None,
        gain_db=float(_get(clip, "gain_db", 0.0) or 0.0),
    )


def available_ms(src_ms: int, clip: ClipParams) -> int:
    """Độ dài dùng được sau trim và tốc độ."""
    return max(0, round((src_ms - clip.trim_in_ms - clip.trim_out_ms) / max(clip.speed, 0.01)))


MIN_SLOWDOWN = 0.8  # spec §11: video ngắn hơn clip → làm chậm tối đa còn 0,8× rồi giữ khung cuối


def fit_to_duration(src_ms: int, clip: ClipParams, target_ms: int | None) -> tuple[ClipParams, dict[str, Any]]:
    """Khớp video provider với `clip.duration_ms` (spec §11): dài hơn → cắt bớt cuối; ngắn hơn → làm chậm (chỉ khi
    tốc độ clip đang là 1×, tối đa ≥ 0,8×) rồi giữ khung cuối phần còn thiếu. Trả (ClipParams mới, mô tả cách khớp)."""
    if not target_ms or target_ms <= 0:
        return clip, {"mode": "source"}
    avail = available_ms(src_ms, clip)
    if avail <= 0:
        return clip, {"mode": "source"}
    if avail >= target_ms:
        return replace(clip, dur_ms=int(target_ms)), {"mode": "trim" if avail > target_ms else "exact",
                                                      "cut_ms": avail - int(target_ms)}
    speed = clip.speed
    if abs(clip.speed - 1.0) < 1e-9:
        speed = max(MIN_SLOWDOWN, avail / target_ms)
    slowed = available_ms(src_ms, replace(clip, speed=speed))
    hold = max(0, int(target_ms) - slowed)
    mode = "slow" if hold == 0 else ("slow_hold" if speed < clip.speed else "hold")
    return replace(clip, speed=round(speed, 6), dur_ms=int(target_ms)), {
        "mode": mode, "speed": round(speed, 4), "hold_last_frame_ms": hold}


def plan(src: SourceMedia, clip: ClipParams, prof: Profile) -> dict[str, Any]:
    """Tham số đã giải (độ dài, số khung, có âm) + fingerprint — không chạy ffmpeg."""
    if clip.speed <= 0:
        raise AppError("render_bad_speed", "Tốc độ clip phải > 0.", status=422)
    if src.duration_ms is None or src.has_audio is None:
        raise AppError("render_bad_media", "Thiếu thời lượng/thông tin âm thanh của media.", status=422)
    avail = available_ms(src.duration_ms, clip)
    if avail <= 0:
        raise AppError("render_trim_empty", "Trim cắt hết clip (trim_in + trim_out ≥ độ dài).", status=422)
    dur = clip.dur_ms if clip.dur_ms else avail
    frames = prof.frames(dur)
    audio = "silent" if (not src.has_audio or clip.grok_audio == "mute") else clip.grok_audio
    gain = round(clip.gain_db, 2) if audio == "keep" else 0.0
    fp_in = {
        "v": NORMALIZE_VERSION, "kind": "video", "sha": src.sha256, "trim_in": clip.trim_in_ms,
        "trim_out": clip.trim_out_ms, "speed": round(clip.speed, 6), "audio": audio, "frames": frames,
        "w": prof.width, "h": prof.height, "fps": prof.fps, "tier": prof.tier, "grade": prof.grade, "lut": prof.lut,
    }
    if gain:
        fp_in["gain_db"] = gain  # chỉ khi khác 0: segment cũ (không gain) giữ nguyên fingerprint
    fp = cache.fingerprint(fp_in)
    return {"frames": frames, "dur_ms": round(frames * 1000 / prof.fps), "avail_ms": avail, "audio": audio,
            "pad_ms": max(0, dur - avail), "gain_db": gain, "fingerprint": fp}


def build_args(src: SourceMedia, clip: ClipParams, prof: Profile, p: dict[str, Any], out: Path) -> list[str]:
    n = p["frames"]
    in_len_ms = max(1, src.duration_ms - clip.trim_in_ms - clip.trim_out_ms)
    args: list[str] = []
    if clip.trim_in_ms:
        args += ["-ss", secs(clip.trim_in_ms)]
    args += ["-t", secs(in_len_ms), "-i", str(src.path)]

    v = [f"setpts=(PTS-STARTPTS)/{clip.speed:.6g}" if clip.speed != 1 else "setpts=PTS-STARTPTS",
         normalize_vf(prof.width, prof.height, prof.fps)]
    if prof.grade:
        v.append(GRADE_BASE)
    if prof.lut:
        from sagareel.render.filters import escape_path

        v.append(f"lut3d=file='{escape_path(prof.lut)}':interp=tetrahedral")
    v.append("format=yuv420p")
    v.append(f"tpad=stop_mode=clone:stop_duration={secs(p['pad_ms'] + 1000)}")
    v.append(f"trim=end_frame={n},setpts=PTS-STARTPTS")
    graph = [f"[0:v:0]{','.join(v)}[v]"]

    s = samples(n, prof.fps)
    if p["audio"] == "silent":
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=end_sample={s}[a]")
    else:
        a = ["asetpts=PTS-STARTPTS", *atempo_chain(clip.speed), "aresample=48000",
             "aformat=sample_fmts=s16:channel_layouts=stereo"]
        if p["audio"] == "ambient":
            a.append(f"volume={AMBIENT_DB}dB")
        elif p.get("gain_db"):
            a.append(f"volume={p['gain_db']:.2f}dB")  # thoại gốc: chuẩn hoá từng clip (tuyến tính) trước khi trộn
        a += ["apad", f"atrim=end_sample={s}", "asetpts=PTS-STARTPTS"]
        graph.append(f"[0:a:0]{','.join(a)}[a]")
    return [*args, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
            "-frames:v", str(n), *prof.intermediate_args(), str(out)]


async def _probe_fill(src: SourceMedia) -> SourceMedia:
    if src.duration_ms is not None and src.has_audio is not None:
        return src
    info = await anyio.to_thread.run_sync(probe, src.path)
    return SourceMedia(src.path, src.sha256, info.has_audio if src.has_audio is None else src.has_audio,
                       info.duration_ms if src.duration_ms is None else src.duration_ms)


async def segment(
    media: SourceMedia | Mapping[str, Any] | Any,
    clip: ClipParams | Mapping[str, Any] | Any,
    profile: Profile | Mapping[str, Any],
    *,
    cache_dir: Path | None = None,
    cancel: asyncio.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Segment chuẩn hoá (cache theo fingerprint). Async: chạy ffmpeg qua `media.ffmpeg.run`."""
    src = await _probe_fill(coerce_media(media))
    cp = coerce_clip(clip)
    prof = coerce(profile)
    p = plan(src, cp, prof)
    out = cache.segment_path(cache_dir or cache.default_cache_dir(), p["fingerprint"])
    if cache.hit(out):
        return out
    with ffmpeg.atomic_output(out) as part:
        await ffmpeg.run(build_args(src, cp, prof, p, part), total_ms=p["dur_ms"], on_progress=on_progress,
                         cancel=cancel, timeout=1800)
    return out
