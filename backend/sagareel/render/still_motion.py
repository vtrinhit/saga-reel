"""Ảnh tĩnh → chuyển động (spec §9 `still_motion`, post-production.md §3 + "Mặc định đề xuất" › still_motion).

- Phóng ảnh trước 4× (3× khi ảnh nguồn lớn) bằng lanczos rồi `zoompan` — chống rung do làm tròn x/y (#4298).
- Easing smoothstep `3t²−2t³`, `t = on/D`; mức phóng ≤ `max_zoom_per_s`·giây, quãng lia ≤ `max_pan_width_per_s`·giây.
- Preset là dữ liệu (`defaults/presets/motion/*.yaml` + bảng `preset` kind=motion): `z`/`x`/`y` là biểu thức zoompan
  với chỗ trống `{amt} {ease} {focus_x} {focus_y} {span} {start} {zoom}`; `post` = các lớp hậu kỳ (grain, vignette,
  light_flicker).
- Dùng cho engine `still_motion` và biến thể **animatic** (ảnh đã duyệt thay cho video).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import anyio
from PIL import Image, UnidentifiedImageError

from sagareel.core.errors import AppError
from sagareel.media import ffmpeg
from sagareel.render import cache, filters, presets
from sagareel.render.filters import samples
from sagareel.render.profile import Profile, coerce

STILL_VERSION = 1
DEFAULT_PRESET = "slow_zoom_in"


def resolve_preset(preset: str | Mapping[str, Any] | None, table: dict[str, dict[str, Any]] | None = None) -> dict:
    """Preset motion đã gộp (`str` = khoá; rỗng → mặc định; dict = dữ liệu preset, được gộp lên preset `key`)."""
    table = table if table is not None else presets.load("motion")
    if preset is None or preset == "":
        return presets.get("motion", DEFAULT_PRESET, table)
    if isinstance(preset, str):
        data = presets.get("motion", preset, table)
    else:
        base = table.get(str(preset.get("key", "")), {}) if preset.get("key") else {}
        data = {**base, **dict(preset)}
    if data.get("type") == "post" or not data.get("z"):
        raise AppError("render_bad_motion", f"Preset “{data.get('key', '?')}” không phải chuyển động ảnh.",
                       status=422)
    return data


def motion_params(data: dict[str, Any], frames: int, fps: int, common: dict[str, Any]) -> dict[str, Any]:
    """Giá trị điền vào biểu thức: giới hạn tốc độ phóng/lia theo thời lượng."""
    dur_s = frames / fps
    amt = min(float(data.get("amt", 0.08)), float(common.get("max_zoom_per_s", 0.02)) * dur_s)
    zoom = float(data.get("zoom", 1.0 + amt))
    base = zoom if data.get("type") == "pan" else float(data.get("zoom_base", 1.06))
    reach = max(1e-6, 1 - 1 / max(base, 1.0001))  # phần ảnh có thể lia (theo tỉ lệ của iw)
    span = min(1.0, float(common.get("max_pan_width_per_s", 0.04)) * dur_s / reach)
    return {
        "amt": f"{amt:.5f}", "zoom": f"{zoom:.4f}", "span": f"{span:.5f}", "start": f"{(1 - span) / 2:.5f}",
        "focus_x": f"{float(data.get('focus_x', common.get('focus_x', 0.5))):.4f}",
        "focus_y": f"{float(data.get('focus_y', common.get('focus_y', 0.5))):.4f}",
        "ease": str(common.get("ease", "(3*pow(on/{D},2)-2*pow(on/{D},3))")).replace("{D}", str(frames)),
    }


def build_filter(
    data: dict[str, Any], frames: int, prof: Profile, *, factor: int, post: list[str] | None,
    table: dict[str, dict[str, Any]], common: dict[str, Any],
) -> str:
    vals = motion_params(data, frames, prof.fps, common)
    try:  # dòng preset trong DB có thể có trước khi kiểm ở preset.create/entity.patch → kiểm lại tại chỗ dựng
        for k in ("z", "x", "y"):
            filters.check_motion_expr(data[k], k)
    except ValueError as e:
        raise AppError("render_bad_motion", f"Preset chuyển động không hợp lệ: {e}", status=422) from None
    z, x, y = (str(data[k]).format(**vals) for k in ("z", "x", "y"))
    uw, uh = prof.width * factor, prof.height * factor
    zp = str(common.get("zoompan", "zoompan=z='{z}':x='{x}':y='{y}':d={D}:s={W}x{H}:fps={fps}"))
    zp = zp.format(z=z, x=x, y=y, D=frames, W=prof.width, H=prof.height, fps=prof.fps)
    chain = [
        f"scale={uw}:{uh}:force_original_aspect_ratio=increase:flags=lanczos:out_range=tv:out_color_matrix=bt709",
        f"crop={uw}:{uh}", "setsar=1", "format=yuv420p", zp,
    ]
    for key in (post if post is not None else list(data.get("post") or [])):
        layer = table.get(key)
        if not layer or layer.get("type") != "post" or not layer.get("filter"):
            raise AppError("render_bad_motion", f"Lớp hậu kỳ “{key}” không có.", status=422)
        try:
            chain.append(filters.check_post_filter(layer["filter"]))
        except ValueError as e:
            raise AppError("render_bad_motion", f"Lớp hậu kỳ “{key}” không hợp lệ: {e}", status=422) from None
    chain += ["format=yuv420p", f"trim=end_frame={frames}", "setpts=PTS-STARTPTS"]
    return ",".join(chain)


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as im:
            return im.size
    except (UnidentifiedImageError, OSError):
        raise AppError("media_unreadable", f"Không đọc được ảnh {Path(path).name}.", status=422) from None


async def make_segment(
    image_path: Path | str,
    preset: str | Mapping[str, Any] | None,
    duration_ms: int,
    profile: Profile | Mapping[str, Any],
    *,
    cache_dir: Path | None = None,
    post: list[str] | None = None,
    image_sha256: str | None = None,
    presets_table: dict[str, dict[str, Any]] | None = None,
    cancel: asyncio.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Segment `.mov` (H.264 + PCM câm) dài đúng `duration_ms` (làm tròn khung) từ một ảnh. Async, cache fingerprint.

    `preset`: khoá (`slow_zoom_in`…), dict preset, hoặc rỗng (mặc định). `profile`: `Profile` hoặc
    `{width, height, fps?}`. `post` ghi đè danh sách lớp hậu kỳ của preset.
    """
    path = Path(image_path)
    if not path.is_file():
        raise AppError("render_bad_media", f"Không tìm thấy ảnh {path.name}.", status=404)
    prof = coerce(profile)
    table = presets_table if presets_table is not None else presets.load("motion")
    common = presets.common("motion")
    data = resolve_preset(preset, table)
    frames = prof.frames(max(1, int(duration_ms)))
    w, h = await anyio.to_thread.run_sync(_image_size, path)
    factor = int(common.get("upscale_factor", 4))
    if max(w, h) > int(common.get("large_source_px", 4000)):
        factor = int(common.get("upscale_factor_large", 3))
    vf = build_filter(data, frames, prof, factor=factor, post=post, table=table, common=common)
    sha = image_sha256
    if not sha:
        from sagareel.services.media import sha256_file

        sha = await anyio.to_thread.run_sync(sha256_file, path)
    fp = cache.fingerprint({"v": STILL_VERSION, "kind": "still", "sha": sha, "vf": vf, "frames": frames,
                            "w": prof.width, "h": prof.height, "fps": prof.fps, "tier": prof.tier})
    out = cache.segment_path(cache_dir or cache.default_cache_dir(), fp)
    if cache.hit(out):
        return out
    s = samples(frames, prof.fps)
    graph = f"[0:v]{vf}[v];anullsrc=r=48000:cl=stereo,atrim=end_sample={s}[a]"
    with ffmpeg.atomic_output(out) as part:
        await ffmpeg.run(
            ["-i", str(path), "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-frames:v", str(frames),
             *prof.intermediate_args(), str(part)],
            total_ms=round(frames * 1000 / prof.fps), on_progress=on_progress, cancel=cancel, timeout=1800,
        )
    return out
