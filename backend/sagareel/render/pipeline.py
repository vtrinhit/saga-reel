"""Dựng một sequence (spec §12): đọc DB → layout → khối (bỏ qua khối đã có) → ghép → lượt âm thanh cuối.

`render(services, sequence_id, variant=draft|animatic|final, platform?, lang?, preview_seconds?)`:
- **Tiếp tục khi bị ngắt / dựng lại tăng dần:** mỗi khối có fingerprint; khối mà `render_chunk` đã có dòng cùng
  fingerprint + file khối + stem thoại còn trên đĩa thì không dựng lại. Mỗi khối xong được ghi `render_chunk` ngay
  (transaction ngắn riêng) → tắt giữa chừng, chạy lại chỉ dựng phần còn thiếu.
- **Biến thể:** `draft` (540p, `veryfast crf 26`, video take → ảnh → khung đen thay thế), `animatic` (ảnh đã duyệt
  + still_motion cho mọi clip, nháp), `final` (độ phân giải project, mã hoá theo nền tảng; thiếu media → lỗi).
- **Xem trước N giây:** chỉ dựng các khối phủ N giây đầu rồi cắt (`render/preview.py`).
- File phái sinh (segment, khối, bản nháp) nằm trong `cache/` (dọn LRU); bản `final` đầy đủ đăng ký vào kho media.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError, NotFound
from sagareel.db.models import (
    Clip,
    DialogueLine,
    Media,
    Project,
    Render,
    RenderChunk,
    Sequence,
    Sound,
    Take,
    TimelineItem,
)
from sagareel.media import ffmpeg
from sagareel.render import (
    cache,
    chunks,
    concat,
    layout,
    normalize,
    presets,
    still_motion,
)
from sagareel.render.builders import DEFAULT, Builders
from sagareel.render.layout import ClipIn, ItemIn, LineIn, SequenceIn, Timeline
from sagareel.render.profile import Profile, for_render
from sagareel.services import media as media_svc

RENDER_VERSION = 1
LANE = "ffmpeg"
JOB_TYPE = "render.sequence"

# Ước tính thời gian chạy (giây máy / giây phim) và dung lượng (bit/s) — thô, chỉ để báo trước.
_RUN_FACTOR = {"draft": 0.35, "animatic": 0.6, "final": 1.6}
_STILL_EXTRA = {"draft": 0.4, "animatic": 0.4, "final": 1.2}
_BITRATE = {"draft": 2_000_000, "animatic": 2_000_000, "final": 9_000_000}


# --- Đọc dữ liệu ----------------------------------------------------------------------------------------------------


@dataclass
class Loaded:
    project_id: int
    sequence_id: int
    profile: Profile
    seq: SequenceIn
    options: chunks.PlanOptions
    transitions: dict[str, dict[str, Any]]
    motion: dict[str, dict[str, Any]]
    lang: str
    platform: str
    warnings: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)


def _setting(services, key: str) -> Any:
    from sagareel.services.settings import global_layer

    with services.db.session() as s:
        layer = global_layer(s)
    try:
        return services.resolver.resolve(key, [layer]).value
    except AppError:
        return None


def _media_file(services, m: Media | None) -> Path | None:
    if m is None:
        return None
    try:
        return media_svc.file_path(services.settings, m)
    except AppError:
        return None


def _take_media(s: Session, take_id: int | None) -> Media | None:
    if not take_id:
        return None
    t = s.get(Take, take_id)
    if t is None or not t.media_id:
        return None
    return s.get(Media, t.media_id)


def _stable_pick(key: str, options: list[str]) -> str:
    return options[int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % len(options)]


def _subtitle_style(s: Session, project: Project, sequence: Sequence) -> dict[str, Any]:
    key = (sequence.settings or {}).get("subtitle_style") or (project.settings or {}).get("subtitle_style")
    if not key:
        a, b = (project.aspect or "9:16").split(":") if ":" in (project.aspect or "") else ("9", "16")
        key = "9x16" if int(a) <= int(b) else "16x9"
    table = presets.load("subtitle_style", s, project.id) if (presets.PRESETS_DIR / "subtitle_style").is_dir() \
        else {}
    return {"key": key, **table.get(key, {})}


def load(
    services,
    sequence_id: int,
    variant: str = "draft",
    platform: str | None = None,
    lang: str | None = None,
) -> Loaded:
    """Đồng bộ (gọi qua `to_thread`): đọc sequence, clip, thoại, item; giải media; dựng `SequenceIn`."""
    with services.db.session() as s:
        sequence = s.get(Sequence, sequence_id)
        if sequence is None or sequence.deleted_at is not None:
            raise NotFound(f"Không tìm thấy tập #{sequence_id}.")
        project = s.get(Project, sequence.project_id)
        lang = lang or project.language or "vi"
        platform = platform or ""
        prof = for_render(project.aspect, project.resolution, variant, platform)
        transitions = presets.load("transition", s, project.id)
        motion = presets.load("motion", s, project.id)
        cycle = list(presets.common("motion").get("default_cycle") or [still_motion.DEFAULT_PRESET])
        loaded = Loaded(project.id, sequence.id, prof, SequenceIn([]), None, transitions, motion,  # type: ignore
                        lang, platform)

        clip_rows = s.scalars(
            select(Clip).where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None)).order_by(Clip.rank, Clip.id)
        ).all()
        line_rows = s.scalars(
            select(DialogueLine).where(DialogueLine.clip_id.in_([c.id for c in clip_rows] or [-1]),
                                       DialogueLine.deleted_at.is_(None))
            .order_by(DialogueLine.clip_id, DialogueLine.rank, DialogueLine.id)
        ).all()
        lines_by_clip: dict[int, list[DialogueLine]] = {}
        for ln in line_rows:
            lines_by_clip.setdefault(ln.clip_id, []).append(ln)

        clips_in: list[ClipIn] = []
        for c in clip_rows:
            video = None if variant == "animatic" else _take_media(s, c.video_take_id)
            image = _take_media(s, c.image_take_id) or (s.get(Media, c.input_media_id) if c.input_media_id else None)
            vpath, ipath = _media_file(services, video), _media_file(services, image)
            grok_keep = any(ln.source == "grok" for ln in lines_by_clip.get(c.id, []))
            src: dict[str, Any]
            if vpath is not None and video is not None:
                sm = normalize.SourceMedia(vpath, video.sha256, (video.meta or {}).get("probe", {}).get("has_audio"),
                                           video.duration_ms)
                if sm.has_audio is None or sm.duration_ms is None:
                    from sagareel.media.probe import probe

                    info = probe(vpath)
                    sm = normalize.SourceMedia(vpath, video.sha256, info.has_audio, info.duration_ms)
                cp = normalize.ClipParams(c.trim_in_ms, c.trim_out_ms, float(c.speed or 1.0),
                                          "keep" if grok_keep else (c.grok_audio or "ambient"), None)
                try:
                    p = normalize.plan(sm, cp, prof)
                except AppError as e:
                    loaded.missing.append({"clip_id": c.id, "key": c.key, "reason": e.detail})
                    src = {"kind": "placeholder"}
                    dur = prof.frame_ms(c.duration_ms)
                else:
                    dur = p["dur_ms"]
                    src = {"kind": "video", "media": sm, "clip": cp, "fp": {"kind": "video", "seg": p["fingerprint"]}}
            elif ipath is not None and image is not None:
                if variant == "final" and c.engine == "ai_video":
                    loaded.missing.append({"clip_id": c.id, "key": c.key, "reason": "Chưa có video"})
                elif c.engine == "ai_video" and variant == "draft":
                    loaded.warnings.append({"code": "video_missing", "clip_id": c.id,
                                            "message": f"Clip {c.key} chưa có video; dùng ảnh chuyển động thay."})
                key = c.motion_preset or _stable_pick(c.key or str(c.id), cycle)
                try:
                    data = still_motion.resolve_preset(key, motion)
                except AppError:
                    loaded.warnings.append({"code": "motion_preset_unknown", "clip_id": c.id,
                                            "message": f"Preset chuyển động “{key}” không có; dùng mặc định."})
                    data = still_motion.resolve_preset("", motion)
                dur = prof.frame_ms(c.duration_ms)
                src = {"kind": "still", "image": ipath, "sha": image.sha256, "preset": data,
                       "fp": {"kind": "still", "sha": image.sha256, "preset": data, "dur": dur}}
            else:
                loaded.missing.append({"clip_id": c.id, "key": c.key, "reason": "Chưa có ảnh/video"})
                dur = prof.frame_ms(c.duration_ms)
                src = {"kind": "placeholder"}
            if src["kind"] == "placeholder":
                src["fp"] = {"kind": "placeholder", "dur": dur}
                if variant != "final":
                    loaded.warnings.append({"code": "media_missing", "clip_id": c.id,
                                            "message": f"Clip {c.key} chưa có ảnh/video; dùng khung đen."})
            clips_in.append(ClipIn(c.id, dur, key=c.key, scene_id=c.scene_id, link=c.link or "cut",
                                   transition=dict(c.transition or {}), src=src))

        primary = (project.language or "vi") == lang
        lines_in: list[LineIn] = []
        clip_by_id = {c.id: c for c in clip_rows}
        for ln in line_rows:
            text = ln.text if primary else str((ln.translations or {}).get(lang) or ln.text)
            audio: Media | None = None
            take: Take | None = None
            if ln.source in ("tts", "upload"):
                if primary and ln.audio_take_id:
                    take = s.get(Take, ln.audio_take_id)
                else:
                    take = s.scalars(
                        select(Take).where(Take.owner_type == "dialogue", Take.owner_id == ln.id,
                                           Take.part == "audio", Take.status == "succeeded", Take.lang == lang)
                        .order_by(Take.id.desc()).limit(1)
                    ).first()
                    if take is None and primary:
                        take = s.scalars(
                            select(Take).where(Take.owner_type == "dialogue", Take.owner_id == ln.id,
                                               Take.part == "audio", Take.status == "succeeded")
                            .order_by(Take.id.desc()).limit(1)
                        ).first()
                audio = s.get(Media, take.media_id) if take is not None and take.media_id else None
            apath = _media_file(services, audio)
            if ln.source in ("tts", "upload") and apath is None:
                loaded.warnings.append({"code": "dialogue_audio_missing", "line_id": ln.id,
                                        "message": "Câu thoại chưa có âm thanh; chỉ hiện phụ đề."})
            dur = int(audio.duration_ms or 0) if audio is not None and apath is not None else 0
            if dur <= 0:
                dur = max(1000, 300 * len(text.split()))  # ước lượng theo số âm tiết
            sub = bool(ln.subtitle) and (clip_by_id[ln.clip_id].subtitle_mode or "auto") != "off"
            words = (take.inputs or {}).get("words") if take is not None else None
            lines_in.append(LineIn(ln.id, ln.clip_id, int(ln.offset_ms or 0), dur, text=text, subtitle=sub, src={
                "path": apath, "words": words,
                "fp": {"sha": audio.sha256 if audio is not None and apath is not None else None,
                       "source": ln.source, "words": words},
            }))

        items_in: list[ItemIn] = []
        for it in s.scalars(select(TimelineItem).where(TimelineItem.sequence_id == sequence_id,
                                                       TimelineItem.deleted_at.is_(None))
                            .order_by(TimelineItem.id)).all():
            m: Media | None = None
            if it.media_id:
                m = s.get(Media, it.media_id)
            elif it.sound_id:
                snd = s.get(Sound, it.sound_id)
                m = s.get(Media, snd.media_id) if snd is not None else None
            mpath = _media_file(services, m)
            if it.track in ("music", "sfx") and mpath is None:
                loaded.warnings.append({"code": "item_media_missing", "item_id": it.id,
                                        "message": f"Item {it.track} #{it.id} không có file âm thanh; bỏ qua."})
                continue
            items_in.append(ItemIn(it.id, it.track, it.anchor_clip_id, int(it.offset_ms or 0), it.duration_ms,
                                   it.end_anchor, m.duration_ms if m is not None else None, dict(it.params or {}),
                                   src={"path": mpath, "fp": {"sha": m.sha256 if m is not None else None}}))

        safe_zone = None
        if platform and (presets.PRESETS_DIR / "platform").is_dir():
            safe_zone = presets.load("platform", s, project.id).get(platform, {}).get("safe_zone")
        loaded.seq = SequenceIn(clips_in, lines_in, items_in)
        loaded.options = chunks.PlanOptions(
            profile=prof, variant=variant, lang=lang, subtitle_style=_subtitle_style(s, project, sequence),
            subtitles=True, safe_zone=safe_zone,
            target_ms=int((_setting(services, "render.chunk_target_s") or 60) * 1000),
            scene_max_ms=int((_setting(services, "render.scene_max_s") or 180) * 1000),
        )
    return loaded


# --- Kế hoạch -------------------------------------------------------------------------------------------------------


@dataclass
class Plan:
    loaded: Loaded
    timeline: Timeline
    specs: list[chunks.ChunkSpec]
    total_ms: int  # thời lượng đầu ra (đã cắt nếu xem trước)
    fingerprint: str
    music: list[dict[str, Any]]


def make_plan(loaded: Loaded, preview_seconds: float | None = None) -> Plan:
    from sagareel.render import preview

    tl = layout.compute(loaded.seq, transitions=loaded.transitions, fps=loaded.profile.fps)
    specs = chunks.plan(tl, loaded.options)
    total = tl.total_ms
    if preview_seconds:
        specs, total = preview.select(specs, preview_seconds, total)
    music = [{"path": m.src.get("path"), "start_ms": m.start_ms, "end_ms": min(m.end_ms, total),
              "params": m.params, "sha": m.src.get("fp", {}).get("sha")}
             for m in tl.music if m.start_ms < total]
    fp = cache.fingerprint({
        "v": RENDER_VERSION, "chunks": [s.fingerprint for s in specs], "total": total,
        "music": [{k: v for k, v in m.items() if k != "path"} for m in music],
        "platform": loaded.platform, "variant": loaded.options.variant, "lang": loaded.lang,
        "preview": preview_seconds or None, "encode": loaded.profile.encode,
    })
    return Plan(loaded, tl, specs, total, fp, music)


# --- Khối có sẵn ----------------------------------------------------------------------------------------------------


def _existing_chunk(services, fingerprint: str) -> tuple[RenderChunk, Path] | None:
    """Dòng `render_chunk` cùng fingerprint còn đủ file khối + stem (ở bất kỳ tập nào)."""
    stem = cache.stem_path(services.settings.cache_dir, fingerprint)
    with services.db.session() as s:
        rows = s.scalars(select(RenderChunk).where(RenderChunk.fingerprint == fingerprint)
                         .order_by(RenderChunk.id.desc())).all()
        for row in rows:
            path = _media_file(services, s.get(Media, row.media_id))
            if path is not None and cache.hit(path) and cache.hit(stem):
                return row, path
    return None


def _record_chunk(services, sequence_id: int, variant: str, spec: chunks.ChunkSpec, file: Path) -> tuple[int, Path]:
    """Đăng ký file khối (cache) + dòng `render_chunk` trong transaction ngắn riêng."""
    prepared = media_svc.prepare(file, kind="video", media_dir=services.settings.cache_dir / "render" / "media",
                                 origin="derived", data_dir=services.settings.data_dir,
                                 meta={"render_chunk": spec.fingerprint})
    with services.db.session(immediate=True) as s:
        m = media_svc.record(s, prepared)
        s.add(RenderChunk(sequence_id=sequence_id, variant=variant, index=spec.index, fingerprint=spec.fingerprint,
                          media_id=m.id, duration_ms=spec.dur_ms))
        s.commit()
        mid = m.id
        mp = m.path
    media_svc.reconcile(prepared, m)
    return mid, services.settings.data_dir / mp


def _link_chunk(services, sequence_id: int, variant: str, spec: chunks.ChunkSpec, row: RenderChunk) -> None:
    """Khối dùng lại từ tập khác/biến thể khác: thêm dòng cho tập này nếu chưa có."""
    with services.db.session(immediate=True) as s:
        exists = s.scalars(select(RenderChunk.id).where(RenderChunk.sequence_id == sequence_id,
                                                        RenderChunk.fingerprint == spec.fingerprint)).first()
        if exists is None:
            s.add(RenderChunk(sequence_id=sequence_id, variant=variant, index=spec.index,
                              fingerprint=spec.fingerprint, media_id=row.media_id, duration_ms=spec.dur_ms))
            s.commit()


# --- Segment --------------------------------------------------------------------------------------------------------


async def _placeholder(prof: Profile, dur_ms: int, cache_dir: Path, cancel: asyncio.Event | None) -> Path:
    from sagareel.render.filters import samples

    frames = prof.frames(dur_ms)
    fp = cache.fingerprint({"v": 1, "kind": "placeholder", "frames": frames, "w": prof.width, "h": prof.height,
                            "fps": prof.fps, "tier": prof.tier})
    out = cache.segment_path(cache_dir, fp)
    if cache.hit(out):
        return out
    graph = (f"color=c=black:s={prof.width}x{prof.height}:r={prof.fps},format=yuv420p,trim=end_frame={frames}[v];"
             f"anullsrc=r=48000:cl=stereo,atrim=end_sample={samples(frames, prof.fps)}[a]")
    with ffmpeg.atomic_output(out) as part:
        await ffmpeg.run(["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-frames:v", str(frames),
                          *prof.intermediate_args(), str(part)], cancel=cancel, timeout=600)
    return out


async def segment_for(slot, prof: Profile, cache_dir: Path, motion: dict, cancel: asyncio.Event | None) -> Path:
    src = slot.src
    if src["kind"] == "video":
        return await normalize.segment(src["media"], src["clip"], prof, cache_dir=cache_dir, cancel=cancel)
    if src["kind"] == "still":
        return await still_motion.make_segment(src["image"], src["preset"], slot.dur_ms, prof, cache_dir=cache_dir,
                                               image_sha256=src["sha"], presets_table=motion, cancel=cancel)
    return await _placeholder(prof, slot.dur_ms, cache_dir, cancel)


# --- Dựng -----------------------------------------------------------------------------------------------------------


def _update_render(services, render_id: int, **values: Any) -> None:
    with services.db.session(immediate=True) as s:
        row = s.get(Render, render_id)
        if row is None:
            return
        meta = values.pop("meta", None)
        for k, v in values.items():
            setattr(row, k, v)
        if meta is not None:
            row.meta = {**dict(row.meta or {}), **meta}
        s.commit()


def render_dict(r: Render) -> dict[str, Any]:
    return {
        "id": r.id, "sequence_id": r.sequence_id, "variant": r.variant, "platform": r.platform, "lang": r.lang,
        "status": r.status, "fingerprint": r.fingerprint, "media_id": r.media_id, "job_id": r.job_id,
        "duration_ms": r.duration_ms, "meta": dict(r.meta or {}),
        "url": f"/media/{r.media_id}" if r.media_id else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _cached_render(services, sequence_id: int, fingerprint: str) -> dict[str, Any] | None:
    with services.db.session() as s:
        rows = s.scalars(select(Render).where(Render.sequence_id == sequence_id, Render.fingerprint == fingerprint,
                                              Render.status == "succeeded").order_by(Render.id.desc())).all()
        for r in rows:
            if r.media_id and _media_file(services, s.get(Media, r.media_id)) is not None:
                return render_dict(r)
    return None


async def render(
    services,
    sequence_id: int,
    variant: str = "draft",
    platform: str | None = None,
    lang: str | None = None,
    preview_seconds: float | None = None,
    *,
    builders: Builders = DEFAULT,
    on_progress: Callable[[float, str], None] | None = None,
    log: Callable[[str, str], None] | None = None,
    cancel: asyncio.Event | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Dựng (tiếp tục được) một sequence. Trả `render_dict` + `chunks: {total, reused, rendered}`."""

    def progress(pct: float, stage: str) -> None:
        if on_progress is not None:
            on_progress(round(max(0.0, min(100.0, pct)), 1), stage)

    def note(level: str, msg: str) -> None:
        if log is not None:
            log(level, msg)

    loaded = await anyio.to_thread.run_sync(load, services, sequence_id, variant, platform, lang)
    if variant == "final" and loaded.missing:
        keys = ", ".join(m["key"] or str(m["clip_id"]) for m in loaded.missing[:10])
        raise AppError("render_missing_media", f"Thiếu ảnh/video cho clip: {keys}.",
                       hint="Tạo hoặc chọn take cho các clip này, hoặc dựng bản nháp.", status=422,
                       extra={"missing": loaded.missing})
    plan = make_plan(loaded, preview_seconds)
    if not plan.specs:
        raise AppError("render_empty", "Tập chưa có clip nào để dựng.", status=422)
    warnings = list(loaded.warnings) + list(plan.timeline.warnings)

    cached = await anyio.to_thread.run_sync(_cached_render, services, sequence_id, plan.fingerprint)
    if cached is not None:
        progress(100, "done")
        return {**cached, "cached": True, "chunks": {"total": len(plan.specs), "reused": len(plan.specs),
                                                      "rendered": 0}}

    def create_row() -> int:
        with services.db.session(immediate=True) as s:
            row = Render(sequence_id=sequence_id, variant=variant, platform=loaded.platform, lang=loaded.lang,
                         status="running", fingerprint=plan.fingerprint, job_id=job_id,
                         meta={"preview_seconds": preview_seconds, "profile": plan.loaded.profile.key()})
            s.add(row)
            s.commit()
            return row.id

    render_id = await anyio.to_thread.run_sync(create_row)
    settings = services.settings
    cache_dir = settings.cache_dir
    work = cache_dir / "render" / "work" / f"r{render_id}-{secrets.token_hex(3)}"
    reused = rendered = 0
    chunk_files: list[Path] = []
    stems: list[Path] = []
    try:
        n = len(plan.specs)
        for i, spec in enumerate(plan.specs):
            base = 85 * i / n
            found = await anyio.to_thread.run_sync(_existing_chunk, services, spec.fingerprint)
            if found is not None:
                row, path = found
                await anyio.to_thread.run_sync(_link_chunk, services, sequence_id, variant, spec, row)
                reused += 1
                chunk_files.append(path)
                stems.append(cache.stem_path(cache_dir, spec.fingerprint))
                progress(85 * (i + 1) / n, f"chunk {i + 1}/{n}")
                continue
            progress(base, f"segments {i + 1}/{n}")
            segs = [await segment_for(c, loaded.profile, cache_dir, loaded.motion, cancel) for c in spec.clips]
            dlg = [d.src.get("path") for d in spec.dialogue]
            # Câu thoại không có file âm (grok/none/thiếu) vẫn có phụ đề → thay bằng âm câm ngắn.
            dlg_paths = [p if p is not None else await _silence(cache_dir, cancel) for p in dlg]
            sfx_paths = [s["src"].get("path") for s in spec.sfx]
            out_file = work / f"chunk_{spec.fingerprint[:16]}.mov"
            stem = cache.stem_path(cache_dir, spec.fingerprint)
            await chunks.render(
                spec, segs, out_path=out_file, stem_out=stem, work_dir=work, dialogue_audio=dlg_paths,
                sfx_audio=sfx_paths, builders=builders, cancel=cancel,
                on_progress=lambda p, b=base, k=i: progress(b + 85 / n * p / 100, f"chunk {k + 1}/{n}"),
            )
            _, path = await anyio.to_thread.run_sync(_record_chunk, services, sequence_id, variant, spec, out_file)
            rendered += 1
            chunk_files.append(path)
            stems.append(stem)
            note("info", f"Khối {i + 1}/{n} xong ({spec.dur_ms / 1000:.1f} s).")
            progress(85 * (i + 1) / n, f"chunk {i + 1}/{n}")

        progress(86, "concat")
        joined_ms = sum(s.dur_ms for s in plan.specs)
        joined = await concat.join(chunk_files, work / "joined.mov", expected_ms=joined_ms, cancel=cancel)
        stem_joined = None
        if plan.music:
            stem_joined = await concat.join(stems, work / "stem.wav", expected_ms=joined_ms, cancel=cancel)
        progress(90, "audio")
        final_file = work / "final.mp4"
        result = await concat.final_audio(
            joined, final_file, stem=stem_joined, music=plan.music, total_ms=plan.total_ms,
            audio_bitrate=loaded.profile.audio_bitrate, builders=builders, cancel=cancel,
            on_progress=lambda p: progress(90 + p * 0.08, "audio"),
        )
        warnings += result["warnings"]
        permanent = variant == "final" and not preview_seconds
        out_dir = settings.media_dir if permanent else cache_dir / "render" / "media"

        def register() -> tuple[int, int | None]:
            prepared = media_svc.prepare(final_file, kind="video", media_dir=out_dir,
                                         origin="generated" if permanent else "derived", data_dir=settings.data_dir,
                                         meta={"render_id": render_id, "variant": variant})
            with services.db.session(immediate=True) as s:
                m = media_svc.record(s, prepared, project_id=loaded.project_id)
                s.commit()
                mid, dur = m.id, m.duration_ms
            media_svc.reconcile(prepared, m)
            return mid, dur

        media_id, dur_ms = await anyio.to_thread.run_sync(register)
        stats = {"total": len(plan.specs), "reused": reused, "rendered": rendered}
        await anyio.to_thread.run_sync(lambda: _update_render(
            services, render_id, status="succeeded", media_id=media_id, duration_ms=dur_ms or plan.total_ms,
            meta={"chunks": stats, "chunk_fingerprints": [s.fingerprint for s in plan.specs],
                  "loudness": result["loudness"], "warnings": warnings, "timeline_ms": plan.timeline.total_ms}))
        progress(99, "cleanup")
        await anyio.to_thread.run_sync(_prune, services, set(chunk_files) | set(stems))
        progress(100, "done")
        with services.db.session() as s:
            out = render_dict(s.get(Render, render_id))
        return {**out, "cached": False, "chunks": stats}
    except BaseException as e:
        status = "cancelled" if isinstance(e, asyncio.CancelledError) or getattr(e, "code", "") == \
            "ffmpeg_cancelled" else "failed"
        err = {"code": getattr(e, "code", type(e).__name__), "detail": getattr(e, "detail", str(e))}
        with contextlib.suppress(Exception):
            await asyncio.shield(anyio.to_thread.run_sync(lambda: _update_render(
                services, render_id, status=status,
                meta={"error": err, "chunks": {"total": len(plan.specs), "reused": reused, "rendered": rendered}})))
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def _silence(cache_dir: Path, cancel: asyncio.Event | None) -> Path:
    out = cache_dir / "render" / "stems" / "_silence_100ms.wav"
    if cache.hit(out):
        return out
    with ffmpeg.atomic_output(out) as part:
        await ffmpeg.run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.1", "-c:a", "pcm_s16le",
                          str(part)], cancel=cancel, timeout=60)
    return out


def _prune(services, keep: set[Path]) -> dict:
    gb = _setting(services, "render.cache_max_gb")
    free_gb = _setting(services, "render.min_free_gb")
    return cache.prune(services.settings.cache_dir, max_bytes=int(float(gb if gb is not None else 30) * 1024 ** 3),
                       min_free_bytes=int(float(free_gb if free_gb is not None else 10) * 1024 ** 3), keep=keep)


# --- Ước tính -------------------------------------------------------------------------------------------------------


def estimate(services, sequence_id: int, variant: str = "draft", platform: str | None = None,
             lang: str | None = None, preview_seconds: float | None = None) -> dict[str, Any]:
    """Đồng bộ: số clip/khối, khối đã có, giây phim, thời gian chạy và dung lượng ước tính (spec §12.6)."""
    loaded = load(services, sequence_id, variant, platform, lang)
    plan = make_plan(loaded, preview_seconds)
    cached_chunks = 0
    todo_ms = still_ms = 0
    for spec in plan.specs:
        if _existing_chunk(services, spec.fingerprint) is not None:
            cached_chunks += 1
            continue
        todo_ms += spec.dur_ms
        still_ms += sum(c.dur_ms for c in spec.clips if c.src.get("kind") == "still")
    kinds = [c.src.get("kind") for c in loaded.seq.clips]
    run_s = todo_ms / 1000 * _RUN_FACTOR[variant] + still_ms / 1000 * _STILL_EXTRA[variant] + plan.total_ms / 4000
    return {
        "sequence_id": sequence_id, "variant": variant, "clips": len(kinds),
        "videos": kinds.count("video"), "stills": kinds.count("still"), "missing": len(loaded.missing),
        "missing_clips": loaded.missing[:50],
        "seconds": round(plan.total_ms / 1000, 3), "chunks": len(plan.specs), "chunks_cached": cached_chunks,
        "est_run_seconds": round(run_s, 1),
        "est_disk_bytes": int(plan.total_ms / 1000 * _BITRATE[variant] / 8 * 2),  # khối + bản cuối
        "width": loaded.profile.width, "height": loaded.profile.height,
        "warnings": (loaded.warnings + plan.timeline.warnings)[:50],
    }
