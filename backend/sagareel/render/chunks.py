"""Khối dựng (spec §12.2, §12.4, §23.1): lập kế hoạch + fingerprint (`plan`, thuần) và dựng một khối (`render`).

**Biên khối** chỉ đặt ở chỗ nối `cut` (chồng 0) mà không câu thoại/title nào vắt qua:
- Có Scene: đổi Scene → khối mới; Scene dài > `scene_max_ms` (3 phút) mới chia tiếp.
- Không Scene: chia thành khối ≈ `target_ms` (60 s).
- Chia tiếp theo kiểu "content-defined": cắt tại chỗ nối khi đã đủ `target/2` **và** băm ổn định của `clip.key`
  rơi trúng (xác suất 1/4), hoặc khi đã quá `1.5 × target`. Chèn/xoá một clip ở đầu chỉ làm đổi một hai khối quanh đó,
  các khối sau trùng biên cũ → giữ nguyên fingerprint.

**Fingerprint** = sha256 JSON chuẩn của mọi đầu vào: vật liệu từng clip (`src.fp`: sha media, trim/tốc độ/âm,
preset motion…), thời gian **cục bộ** (không dùng thời điểm tuyệt đối, chỉ số khối, id media hay đường dẫn), chuyển
cảnh, thoại (sha âm, chữ, phụ đề), style phụ đề, SFX/title rơi vào khối, hồ sơ/biến thể/ngôn ngữ, `CHUNK_VERSION`.

**Dựng khối**: xfade/concat hình + acrossfade/concat âm giữa các segment; thoại đặt theo offset (`adelay`) trộn
`amix normalize=0`, SFX tương tự, ASS burn-in (Task D qua `Builders`), title bằng drawtext. Ra `.mov` (H.264 theo hồ sơ
+ PCM) dài đúng N khung, kèm stem thoại `.wav` cùng độ dài (sidechain ducking ở lượt âm thanh cuối).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sagareel.core.errors import AppError
from sagareel.media import ffmpeg
from sagareel.render import cache
from sagareel.render.builders import DEFAULT, Builders
from sagareel.render.filters import escape_path, samples, secs
from sagareel.render.layout import ClipSlot, Placed, Timeline
from sagareel.render.profile import AUDIO_PCM, Profile

CHUNK_VERSION = 1
DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "defaults"


@dataclass(frozen=True)
class PlanOptions:
    profile: Profile
    variant: str = "draft"
    lang: str = ""
    subtitle_style: dict[str, Any] = field(default_factory=dict)
    subtitles: bool = True
    safe_zone: dict[str, Any] | None = None
    target_ms: int = 60_000
    scene_max_ms: int = 180_000
    title_style: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkSpec:
    index: int
    start_ms: int
    dur_ms: int
    clips: list[ClipSlot]
    dialogue: list[Placed]
    sfx: list[dict[str, Any]]
    titles: list[dict[str, Any]]
    fingerprint: str
    options: PlanOptions

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.dur_ms

    @property
    def subtitle_lines(self) -> list[Placed]:
        if not self.options.subtitles:
            return []
        return [d for d in self.dialogue if d.subtitle and d.text.strip()]


# --- Lập kế hoạch -------------------------------------------------------------------------------------------------


def _stable_hit(key: str, modulo: int = 4) -> bool:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % modulo == 0


def _spanned(t: int, spans: list[tuple[int, int]]) -> bool:
    return any(a < t < b for a, b in spans)


def boundaries(timeline: Timeline, *, target_ms: int = 60_000, scene_max_ms: int = 180_000) -> list[int]:
    """Chỉ số clip bắt đầu mỗi khối (luôn có 0)."""
    clips = timeline.clips
    if not clips:
        return []
    spans = [(p.start_ms, p.end_ms) for p in (*timeline.dialogue, *timeline.titles)]
    use_scenes = any(c.scene_id is not None for c in clips)
    scene_len: dict[Any, int] = {}
    if use_scenes:
        for c in clips:
            scene_len[c.scene_id] = scene_len.get(c.scene_id, 0) + c.dur_ms - c.overlap_ms
    lo, hi = target_ms // 2, target_ms * 3 // 2

    starts = [0]
    run_start = clips[0].start_ms
    for i in range(1, len(clips)):
        c, prev = clips[i], clips[i - 1]
        if c.overlap_ms or _spanned(c.start_ms, spans):
            continue
        acc = c.start_ms - run_start
        if use_scenes and c.scene_id != prev.scene_id:
            cut = True
        elif use_scenes and scene_len.get(c.scene_id, 0) <= scene_max_ms:
            cut = False
        else:
            cut = acc >= hi or (acc >= lo and _stable_hit(c.key or str(c.id)))
        if cut:
            starts.append(i)
            run_start = c.start_ms
    return starts


def _clip_fp(slot: ClipSlot, chunk_start: int, first: bool) -> dict[str, Any]:
    return {"src": slot.src.get("fp", {}), "start": slot.start_ms - chunk_start, "dur": slot.dur_ms,
            "overlap": 0 if first else slot.overlap_ms, "xfade": None if first else slot.xfade}


def _overlapping(items: list[Placed], a: int, b: int) -> list[Placed]:
    return [p for p in items if p.start_ms < b and p.end_ms > a]


def plan(timeline: Timeline, options: PlanOptions) -> list[ChunkSpec]:
    """Danh sách khối + fingerprint (thuần, không đọc DB/không chạy ffmpeg)."""
    clips = timeline.clips
    starts = boundaries(timeline, target_ms=options.target_ms, scene_max_ms=options.scene_max_ms)
    specs: list[ChunkSpec] = []
    by_clip_chunk: dict[int, int] = {}
    groups = [clips[s:e] for s, e in zip(starts, [*starts[1:], len(clips)], strict=True)]
    for idx, group in enumerate(groups):
        for c in group:
            by_clip_chunk[c.id] = idx
    for idx, group in enumerate(groups):
        a, b = group[0].start_ms, group[-1].end_ms
        dialogue = [d for d in timeline.dialogue if by_clip_chunk.get(d.clip_id) == idx]
        sfx = []
        for p in _overlapping(timeline.sfx, a, b):
            local = p.start_ms - a
            seek = max(0, -local)
            sfx.append({"id": p.id, "start": max(0, local), "seek": seek,
                        "dur": min(p.end_ms, b) - max(p.start_ms, a), "params": p.params, "src": p.src})
        titles = [{"id": p.id, "text": p.text, "start": max(p.start_ms, a) - a, "end": min(p.end_ms, b) - a,
                   "params": p.params} for p in _overlapping(timeline.titles, a, b) if p.text.strip()]
        subs = options.subtitles and any(d.subtitle and d.text.strip() for d in dialogue)
        material = {
            "v": CHUNK_VERSION, "variant": options.variant, "lang": options.lang, "profile": options.profile.key(),
            "dur": b - a,
            "clips": [_clip_fp(c, a, k == 0) for k, c in enumerate(group)],
            "dialogue": [{"src": d.src.get("fp", {}), "start": d.start_ms - a, "end": d.end_ms - a, "text": d.text,
                          "subtitle": d.subtitle} for d in dialogue],
            "subtitle": {"style": options.subtitle_style, "safe_zone": options.safe_zone} if subs else None,
            "sfx": [{"src": s["src"].get("fp", {}), "start": s["start"], "seek": s["seek"], "dur": s["dur"],
                     "params": s["params"]} for s in sfx],
            "titles": [{k: t[k] for k in ("text", "start", "end", "params")} for t in titles],
            "title_style": options.title_style if titles else None,
        }
        specs.append(ChunkSpec(index=idx, start_ms=a, dur_ms=b - a, clips=list(group), dialogue=dialogue, sfx=sfx,
                               titles=titles, fingerprint=cache.fingerprint(material), options=options))
    return specs


# --- Dựng ---------------------------------------------------------------------------------------------------------


def _fontsdir() -> Path | None:
    d = DEFAULTS_DIR / "fonts"
    return d if d.is_dir() and any(d.glob("*.ttf")) else None


def title_font() -> Path | None:
    d = _fontsdir()
    if d is not None:
        bold = sorted(d.glob("*Bold*.ttf")) or sorted(d.glob("*.ttf"))
        if bold:
            return bold[0]
    for cand in (Path("C:/Windows/Fonts/arialbd.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if cand.is_file():
            return cand
    return None


def _offset(ms: int, fps: int) -> str:
    return f"{round(ms * fps / 1000) / fps:.6f}"


def _place_audio(label: str, local_ms: int, seek_ms: int, extra: list[str]) -> str:
    chain = ["aresample=48000", "aformat=sample_fmts=s16:channel_layouts=stereo"]
    if seek_ms:
        chain.append(f"atrim=start={secs(seek_ms)}")
    chain.append("asetpts=PTS-STARTPTS")
    chain += extra
    if local_ms > 0:
        chain.append(f"adelay=delays={int(local_ms)}:all=1")
    return f"[{label}]{','.join(chain)}"


def build_graph(
    spec: ChunkSpec,
    *,
    dialogue_inputs: list[int],
    sfx_inputs: list[int],
    ass_path: Path | None,
    title_files: list[Path],
) -> str:
    prof = spec.options.profile
    fps = prof.fps
    n = len(spec.clips)
    frames = prof.frames(spec.dur_ms)
    total_samples = samples(frames, fps)
    g: list[str] = []

    # Hình + âm nền: nối lần lượt (xfade/acrossfade khi có chồng, concat khi cắt)
    v, a = "[0:v]", "[0:a]"
    if n == 1:
        g.append("[0:v]null[v0]")
        g.append("[0:a]anull[a0]")
        v, a = "[v0]", "[a0]"
    for i in range(1, n):
        c = spec.clips[i]
        local = c.start_ms - spec.start_ms
        if c.overlap_ms and c.xfade:
            d = f"{round(c.overlap_ms * fps / 1000) / fps:.6f}"
            g.append(f"{v}[{i}:v]xfade=transition={c.xfade}:duration={d}:offset={_offset(local, fps)}[v{i}]")
            g.append(f"{a}[{i}:a]acrossfade=d={d}:c1=tri:c2=tri[a{i}]")
        else:
            g.append(f"{v}[{i}:v]concat=n=2:v=1:a=0[v{i}]")
            g.append(f"{a}[{i}:a]concat=n=2:v=0:a=1[a{i}]")
        v, a = f"[v{i}]", f"[a{i}]"

    post: list[str] = []
    if ass_path is not None:
        fd = _fontsdir()
        opt = f"ass=filename='{escape_path(ass_path)}'"
        if fd is not None:
            opt += f":fontsdir='{escape_path(fd)}'"
        post.append(opt)
    font = title_font()
    for t, tf in zip(spec.titles, title_files, strict=True):
        st = {**spec.options.title_style, **(t.get("params") or {})}
        size = int(st.get("fontsize_ratio", 0.055) * prof.height) or 1
        dt = [f"textfile='{escape_path(tf)}'", "expansion=none", f"fontsize={size}",
              f"fontcolor={st.get('color', 'white')}", f"borderw={max(1, size // 12)}", "bordercolor=black",
              "x=(w-tw)/2", f"y=h*{float(st.get('y_ratio', 0.3)):.3f}",
              f"enable='between(t,{secs(t['start'])},{secs(t['end'])})'"]
        if font is not None:
            dt.insert(0, f"fontfile='{escape_path(font)}'")
        post.append("drawtext=" + ":".join(dt))
    post += ["format=yuv420p", f"trim=end_frame={frames}", "setpts=PTS-STARTPTS"]
    g.append(f"{v}{','.join(post)}[vout]")

    # Thoại (track riêng, không chồng), stem cho ducking
    mix_inputs = [a]
    if dialogue_inputs:
        labels = []
        for k, (inp, d) in enumerate(zip(dialogue_inputs, spec.dialogue, strict=True)):
            local = d.start_ms - spec.start_ms
            g.append(_place_audio(f"{inp}:a", max(0, local), max(0, -local), []) + f"[d{k}]")
            labels.append(f"[d{k}]")
        mixed = "".join(labels) + (
            f"amix=inputs={len(labels)}:duration=longest:normalize=0" if len(labels) > 1 else "anull")
        g.append(f"{mixed},apad,atrim=end_sample={total_samples},asplit=2[dmix][dstem]")
        mix_inputs.append("[dmix]")
    else:
        g.append(f"anullsrc=r=48000:cl=stereo,atrim=end_sample={total_samples}[dstem]")

    if sfx_inputs:
        labels = []
        for k, (inp, s) in enumerate(zip(sfx_inputs, spec.sfx, strict=True)):
            vol = (s.get("params") or {}).get("volume_db")
            extra = [f"volume={float(vol):g}dB"] if vol is not None else []
            extra.append(f"atrim=end={secs(s['dur'])}")
            g.append(_place_audio(f"{inp}:a", s["start"], s["seek"], extra) + f"[s{k}]")
            labels.append(f"[s{k}]")
        mixed = "".join(labels) + (
            f"amix=inputs={len(labels)}:duration=longest:normalize=0" if len(labels) > 1 else "anull")
        g.append(f"{mixed},apad,atrim=end_sample={total_samples}[sfx]")
        mix_inputs.append("[sfx]")

    if len(mix_inputs) > 1:
        g.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:normalize=0,"
                 f"apad,atrim=end_sample={total_samples}[aout]")
    else:
        g.append(f"{a}apad,atrim=end_sample={total_samples}[aout]")
    return ";".join(g)


async def render(
    spec: ChunkSpec,
    segments: list[Path],
    *,
    out_path: Path,
    stem_out: Path,
    work_dir: Path,
    dialogue_audio: list[Path] | None = None,
    sfx_audio: list[Path] | None = None,
    builders: Builders = DEFAULT,
    cancel: asyncio.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[Path, Path]:
    """Dựng một khối từ segment đã chuẩn hoá (cùng thứ tự `spec.clips`). Trả (file khối `.mov`, stem thoại `.wav`)."""
    if len(segments) != len(spec.clips):
        raise AppError("render_internal", "Số segment không khớp số clip của khối.", status=500)
    dialogue_audio = list(dialogue_audio or [])
    sfx_audio = list(sfx_audio or [])
    if len(dialogue_audio) != len(spec.dialogue) or len(sfx_audio) != len(spec.sfx):
        raise AppError("render_internal", "Số file thoại/SFX không khớp kế hoạch khối.", status=500)
    prof = spec.options.profile
    work_dir.mkdir(parents=True, exist_ok=True)

    ass_path: Path | None = None
    lines = spec.subtitle_lines
    if lines:
        timing = [{"id": d.id, "text": d.text, "start_ms": d.start_ms - spec.start_ms,
                   "end_ms": d.end_ms - spec.start_ms, "words": d.src.get("words"), "lang": spec.options.lang}
                  for d in lines]
        style = dict(spec.options.subtitle_style)
        cues = builders.build_cues(timing, style, prof.width, prof.height, spec.options.safe_zone)
        text = builders.to_ass(cues, style, prof.width, prof.height)
        ass_path = work_dir / f"{spec.fingerprint[:16]}.ass"
        ass_path.write_text(text, encoding="utf-8")  # UTF-8 không BOM
    title_files = []
    for t in spec.titles:
        tf = work_dir / f"{spec.fingerprint[:16]}_title_{t['id']}.txt"
        tf.write_text(t["text"], encoding="utf-8")
        title_files.append(tf)

    inputs: list[str] = []
    for p in segments:
        inputs += ["-i", str(p)]
    first_dlg = len(segments)
    dlg_idx = list(range(first_dlg, first_dlg + len(dialogue_audio)))
    for p in dialogue_audio:
        inputs += ["-i", str(p)]
    sfx_idx = list(range(first_dlg + len(dialogue_audio), first_dlg + len(dialogue_audio) + len(sfx_audio)))
    for p in sfx_audio:
        inputs += ["-i", str(p)]

    graph = build_graph(spec, dialogue_inputs=dlg_idx, sfx_inputs=sfx_idx,
                        ass_path=ass_path, title_files=title_files)
    frames = prof.frames(spec.dur_ms)
    with ffmpeg.atomic_output(stem_out) as stem_part, ffmpeg.atomic_output(out_path) as part:
        await ffmpeg.run(
            [*inputs, "-filter_complex", graph,
             "-map", "[vout]", "-map", "[aout]", "-frames:v", str(frames), *prof.video_args(), *AUDIO_PCM, str(part),
             "-map", "[dstem]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(stem_part)],
            total_ms=spec.dur_ms, on_progress=on_progress, cancel=cancel, timeout=3600,
        )
    for f in (ass_path, *title_files):
        if f is not None:
            f.unlink(missing_ok=True)
    return out_path, stem_out
