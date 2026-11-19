"""Gói xuất: mỗi (tập x nền tảng x ngôn ngữ) một thư mục `outputs/exports/<project>/<seq-key>/<platform>-<lang>/`.

Luồng một gói (`run_unit`): dựng bản cuối qua `sagareel.render.pipeline.render` (Task E, import muộn) → đưa về đúng
khung/mã hoá nền tảng (đổi tỉ lệ `blur_pad`/`crop`, chia phần khi vượt độ dài tối đa) → phụ đề srt/vtt → bìa 1..3 →
metadata (LLM `caption` hoặc mẫu) → kiểm tra → ghi vào thư mục tạm rồi đổi tên thay bản cũ. Manifest json/csv cho cả lô
nằm ở `outputs/exports/<project>/_batches/<batch>/`.

Giả định về module của task khác (kiểm khi nối thật ở task e2e S):
- `render.pipeline.render(sequence_id, variant=, platform=, lang=)` (sync hoặc async; nếu tham số đầu tên `services`/
  `ctx`/`run` thì được truyền vào) trả đường dẫn / `media_id` / object-dict có `media_id|path` (+ tuỳ chọn `cues`).
- `services.audio.mix.measure_loudness(path)` trả dict/object có `integrated_lufs|lufs|input_i` (+ `true_peak*`).
- `services.audio.subtitles.to_srt(cues)` / `to_vtt(cues)`; cue có `start_ms`, `end_ms` (+ tuỳ chọn `box`).
"""

from __future__ import annotations

import asyncio
import copy
import csv
import dataclasses
import importlib
import inspect
import io
import json
import logging
import os
import re
import shutil
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import anyio
from pydantic import BaseModel, Field
from sqlalchemy import select

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound, ValidationFailed
from sagareel.db.base import utcnow
from sagareel.db.models import Clip, ExportPackage, Scene, Sound, TimelineItem
from sagareel.media import ffmpeg, probe
from sagareel.services import entities, media
from sagareel.services.export import checks, covers, metadata, platforms
from sagareel.services.jobs import (
    JobFailed,
    JobStateLost,
    NeedsAttention,
    TransientError,
)

logger = logging.getLogger("sagareel.export")

JOB_TYPE = "export.package"
DONE_RENDER = {"done", "ready", "completed", "finished", "success"}


class ExportOptions(BaseModel):
    reframe: Literal["blur_pad", "crop"] = Field(default="blur_pad", description="Cách đổi tỉ lệ khung khi khác nền tảng")
    focus_x: float = Field(default=0.5, ge=0, le=1, description="Tâm crop theo chiều ngang (0 trái - 1 phải)")
    focus_y: float = Field(default=0.5, ge=0, le=1, description="Tâm crop theo chiều dọc (0 trên - 1 dưới)")
    split_parts: bool = Field(default=True, description="Cắt nhiều phần khi vượt độ dài tối đa của nền tảng")
    covers: int = Field(default=3, ge=1, le=3, description="Số ảnh bìa")
    cover_title: bool = Field(default=True, description="Vẽ tiêu đề lên bìa (trong vùng an toàn)")
    cover_marks_ms: list[int] = Field(default_factory=list, description="Khung người dùng đánh dấu làm bìa (ms)")
    use_llm: bool = Field(default=True, description="Dùng công việc LLM `caption` viết metadata (không có thì dùng mẫu)")


# --- Đường dẫn ------------------------------------------------------------------------------------------------------


def exports_root(services: Any, project_key: str) -> Path:
    return services.settings.outputs_dir / "exports" / project_key


_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def _safe_part(value: str, what: str) -> str:
    if not _SAFE_PART.match(value) or ".." in value:
        raise ValidationFailed(f"Giá trị {what} không hợp lệ cho tên thư mục: {value!r}.", code="invalid_path_part")
    return value


def package_folder(services: Any, project_key: str, seq_key: str, platform: str, lang: str) -> Path:
    """Thư mục gói xuất; mọi phần tên được kiểm (chống thoát khỏi `outputs/exports`)."""
    root = (services.settings.outputs_dir / "exports").resolve()
    p = (root / _safe_part(project_key, "dự án") / _safe_part(seq_key, "tập")
         / f"{_safe_part(platform, 'nền tảng')}-{_safe_part(lang or 'und', 'ngôn ngữ')}").resolve()
    if not p.is_relative_to(root):
        raise ValidationFailed("Đường dẫn xuất nằm ngoài thư mục outputs/exports.", code="invalid_path_part")
    return p


def rel_path(services: Any, p: Path) -> str:
    try:
        return p.resolve().relative_to(services.settings.outputs_dir.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


# --- Lập kế hoạch ---------------------------------------------------------------------------------------------------


def load_targets(services: Any, sequence_ids: Sequence[int], plats: Sequence[str],
                 langs: Sequence[str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Kiểm đầu vào và liệt kê các đơn vị xuất `{sequence_id, seq_key, title, platform, lang}`. Đồng bộ."""
    from sagareel.db.models import Project

    if not sequence_ids:
        raise ValidationFailed("Cần ít nhất một tập để xuất.", code="export_no_sequences")
    units: list[dict[str, Any]] = []
    with services.db.session() as s:
        seqs = [entities.get(s, "sequence", i) for i in dict.fromkeys(sequence_ids)]
        pids = {q.project_id for q in seqs}
        if len(pids) != 1:
            raise ValidationFailed("Các tập phải cùng một dự án.", code="export_mixed_projects")
        project = s.get(Project, pids.pop())
        proj = {"id": project.id, "key": project.key, "name": project.name, "description": project.description,
                "genre_key": project.genre_key, "language": project.language, "languages": list(project.languages or [])}
        for q in seqs:
            plat_list = list(plats) or list(q.target_platforms or []) or list(project.target_platforms or [])
            if not plat_list:
                raise ValidationFailed("Chưa chọn nền tảng và tập/dự án cũng chưa có nền tảng đích.",
                                       code="export_no_platforms", hint="Truyền `platforms`, vd. [\"tiktok\"].")
            for plat in dict.fromkeys(plat_list):
                platforms.get(services, plat, project.id)  # ném `unknown_platform` nếu không có
                for lang in dict.fromkeys(list(langs or []) or [project.language]):
                    units.append({"sequence_id": q.id, "seq_key": q.key, "title": q.title, "platform": plat, "lang": lang})
    return proj, units


def create_rows(services: Any, project: Mapping[str, Any], units: Sequence[Mapping[str, Any]],
                options: ExportOptions, batch: str) -> list[int]:
    """Một dòng `export_package` (pending) cho mỗi đơn vị; dòng cũ cùng thư mục thành `superseded`."""
    ids: list[int] = []
    with services.db.session(immediate=True) as s:
        for u in units:
            folder = rel_path(services, package_folder(services, project["key"], u["seq_key"], u["platform"], u["lang"]))
            for old in s.scalars(select(ExportPackage).where(ExportPackage.project_id == project["id"],
                                                             ExportPackage.folder == folder,
                                                             ExportPackage.status != "superseded")):
                old.status = "superseded"
            row = ExportPackage(project_id=project["id"], sequence_ids=[u["sequence_id"]], platform=u["platform"],
                                lang=u["lang"], folder=folder, status="pending",
                                meta_data={"batch": batch, "seq_key": u["seq_key"], "title": u["title"],
                                           "options": options.model_dump()})
            s.add(row)
            s.flush()
            ids.append(row.id)
    return ids


def row_dict(row: ExportPackage) -> dict[str, Any]:
    return {"id": row.id, "project_id": row.project_id, "sequence_ids": list(row.sequence_ids or []),
            "platform": row.platform, "lang": row.lang, "folder": row.folder, "status": row.status,
            "checks": dict(row.checks or {}), "metadata": dict(row.meta_data or {}),
            "created_at": row.created_at.isoformat() if row.created_at else None}


# --- Thu thập dữ liệu của tập ---------------------------------------------------------------------------------------


def sequence_facts(services: Any, sequence_id: int) -> dict[str, Any]:
    """Dữ liệu phẳng của tập: mốc cảnh (ms ước tính theo tổng clip), tổng thời lượng clip, nhạc + giấy phép."""
    with services.db.session() as s:
        seq = entities.get(s, "sequence", sequence_id)
        out: dict[str, Any] = {"id": seq.id, "key": seq.key, "number": seq.number, "title": seq.title,
                               "synopsis": seq.synopsis, "script": seq.script}
        scenes = s.scalars(select(Scene).where(Scene.sequence_id == seq.id, Scene.deleted_at.is_(None))
                           .order_by(Scene.rank)).all()
        scene_title = {sc.id: (sc.title or sc.summary.split("\n")[0][:40] or sc.key) for sc in scenes}
        clips = s.scalars(select(Clip).where(Clip.sequence_id == seq.id, Clip.deleted_at.is_(None))
                          .order_by(Clip.rank)).all()
        marks: list[tuple[int, str]] = []
        t = 0
        last_scene: Any = object()
        for c in clips:
            if c.scene_id != last_scene:
                marks.append((t, scene_title.get(c.scene_id) or ("Mở đầu" if not marks else f"Cảnh {len(marks) + 1}")))
                last_scene = c.scene_id
            t += max(0, int(c.duration_ms))
        out["clip_total_ms"] = t
        out["scene_marks"] = marks
        music: list[dict[str, Any]] = []
        for it in s.scalars(select(TimelineItem).where(TimelineItem.sequence_id == seq.id,
                                                       TimelineItem.track == "music",
                                                       TimelineItem.deleted_at.is_(None)).order_by(TimelineItem.id)):
            snd = s.get(Sound, it.sound_id) if it.sound_id else None
            music.append({"sound_id": it.sound_id, "title": snd.title if snd else "", "source": snd.source if snd else "",
                          "license": snd.license if snd else "", "attribution": snd.attribution if snd else ""})
        out["music"] = music
        return out


def latest_render(services: Any, sequence_id: int, platform: str | None, lang: str | None) -> dict[str, Any] | None:
    """Bản dựng cuối đã xong gần nhất của tập (ưu tiên đúng nền tảng + ngôn ngữ)."""
    from sagareel.db.models import Render

    with services.db.session() as s:
        rows = s.scalars(select(Render).where(Render.sequence_id == sequence_id, Render.variant == "final",
                                              Render.media_id.is_not(None)).order_by(Render.id.desc())).all()
        rows = [r for r in rows if (r.status or "").lower() in DONE_RENDER]
        best = next((r for r in rows if r.platform == platform and r.lang == lang), None) or (rows[0] if rows else None)
        if best is None:
            return None
        m = media.get(s, best.media_id)
        return {"render_id": best.id, "media_id": m.id, "path": media.file_path(services.settings, m),
                "duration_ms": best.duration_ms or m.duration_ms, "size": m.size, "width": m.width, "height": m.height}


# --- Gọi module của task khác ---------------------------------------------------------------------------------------


async def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        res = await fn(*args, **kwargs)
    else:
        res = await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))
    return await res if inspect.isawaitable(res) else res


async def measure_loudness(path: Path) -> Any:
    """Đo ebur128 qua `services.audio.mix.measure_loudness` (Task D); module/hàm chưa có → None."""
    try:
        mix = importlib.import_module("sagareel.services.audio.mix")
        fn = mix.measure_loudness
    except (ImportError, AttributeError):
        logger.warning("Chưa có services.audio.mix.measure_loudness; bỏ qua đo loudness")
        return None
    try:
        return await _call(fn, path)
    except Exception as e:  # noqa: BLE001 — đo hỏng không được chặn xuất
        logger.warning("Đo loudness lỗi: %s", e)
        return None


async def render_final(services: Any, run: Any, sequence_id: int, platform: str, lang: str) -> Any:
    pipeline = importlib.import_module("sagareel.render.pipeline")
    fn = pipeline.render
    sig = inspect.signature(fn)
    kwargs = {"variant": "final", "platform": platform, "lang": lang}
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
    first = next(iter(sig.parameters), "")
    lead: list[Any] = []
    if first in ("services", "svc"):
        lead = [services]
    elif first == "ctx":
        lead = [OpContext(services=services, actor="system:export", role="owner")]
    elif first == "run":
        lead = [run]
    return await _call(fn, *lead, sequence_id, **kwargs)


def resolve_render(services: Any, res: Any) -> tuple[Path, dict[str, Any]]:
    """Chuẩn hoá kết quả `pipeline.render` thành (đường dẫn file, phần dư như cues)."""
    extra: dict[str, Any] = {}
    if isinstance(res, Mapping):
        extra = dict(res)
        for k in ("path", "output", "file", "media_id", "render"):
            if res.get(k) is not None:
                res = res[k]
                break
    elif hasattr(res, "media_id") or hasattr(res, "path"):
        extra = {"cues": getattr(res, "cues", None)}
        res = getattr(res, "path", None) or getattr(res, "media_id", None)
    if isinstance(res, bool) or res is None:
        raise AppError("export_render_failed", "Bước dựng phim không trả về file.", status=500)
    if isinstance(res, int):
        with services.db.session() as s:
            res = media.file_path(services.settings, media.get(s, res))
    path = Path(str(res))
    if not path.is_file():
        raise NotFound(f"File bản dựng {path.name} không tồn tại.", code="media_file_missing")
    return path, extra


# --- Phụ đề ---------------------------------------------------------------------------------------------------------


def _field(c: Any, name: str) -> Any:
    return c.get(name) if isinstance(c, Mapping) else getattr(c, name, None)


def _with_times(c: Any, start: int, end: int) -> Any:
    if dataclasses.is_dataclass(c) and not isinstance(c, type):
        return dataclasses.replace(c, start_ms=start, end_ms=end)
    if isinstance(c, Mapping):
        return {**c, "start_ms": start, "end_ms": end}
    n = copy.copy(c)
    n.start_ms, n.end_ms = start, end
    return n


def shift_cues(cues: Sequence[Any], start_ms: int, end_ms: int) -> list[Any]:
    """Cue nằm trong [start_ms, end_ms) của một phần, dời về gốc 0 (cắt cue chạm biên)."""
    out = []
    for c in cues:
        s, e = int(_field(c, "start_ms")), int(_field(c, "end_ms"))
        if e <= start_ms or s >= end_ms:
            continue
        out.append(_with_times(c, max(s, start_ms) - start_ms, min(e, end_ms) - start_ms))
    return out


def cue_boxes(cues: Sequence[Any]) -> list[Any]:
    boxes = []
    for c in cues:
        b = _field(c, "box")
        if b is not None:
            start = _field(c, "start_ms")
            boxes.append({**b, "start_ms": start} if isinstance(b, Mapping) else (*tuple(b)[:4], start))
    return boxes


async def collect_cues(services: Any, sequence_id: int, lang: str, extra: Mapping[str, Any], spec: Mapping[str, Any]) -> list[Any]:
    """Cue phụ đề của bản dựng: ưu tiên `cues` do pipeline trả; không thì dựng từ layout + `subtitles.build_cues`."""
    if extra.get("cues"):
        return list(extra["cues"])
    try:
        layout = importlib.import_module("sagareel.render.layout")
        subs = importlib.import_module("sagareel.services.audio.subtitles")
        timeline = await _call(layout.compute, sequence_id)
        lines = []
        for d in getattr(timeline, "dialogue", None) or []:
            start = _field(d, "start_ms")
            dur = _field(d, "dur_ms") or _field(d, "duration_ms")
            end = _field(d, "end_ms") if dur is None else start + dur
            tr = _field(d, "translations") or {}
            text = tr.get(lang) if isinstance(tr, Mapping) and tr.get(lang) else _field(d, "text")
            if text and start is not None and end is not None and _field(d, "subtitle") is not False:
                lines.append({"text": text, "start_ms": start, "end_ms": end})
        w, h = platforms.target_size(dict(spec))
        style = f"{spec['aspect'].replace(':', 'x')}"
        return list(await _call(subs.build_cues, lines, style, w, h, platforms.safe_zone(dict(spec))))
    except Exception as e:  # noqa: BLE001 — thiếu module/định dạng khác giả định: gói vẫn xuất, không có phụ đề
        logger.warning("Không dựng được phụ đề cho tập #%s: %s", sequence_id, e)
        return []


def write_subtitles(cues: Sequence[Any], folder: Path, suffix: str = "") -> list[str]:
    if not cues:
        return []
    subs = importlib.import_module("sagareel.services.audio.subtitles")
    names = []
    for ext, fn in (("srt", subs.to_srt), ("vtt", subs.to_vtt)):
        (folder / f"subtitles{suffix}.{ext}").write_text(fn(list(cues)), encoding="utf-8")
        names.append(f"subtitles{suffix}.{ext}")
    return names


# --- Video ----------------------------------------------------------------------------------------------------------


def video_filter(src: tuple[int, int], dst: tuple[int, int], reframe: str, fx: float, fy: float) -> str:
    (sw, sh), (w, h) = src, dst
    if abs(sw / sh - w / h) < 0.01:
        return f"scale={w}:{h}:flags=lanczos,setsar=1"
    if reframe == "crop":
        return (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={w}:{h}:(iw-ow)*{fx:g}:(ih-oh)*{fy:g},setsar=1")
    return (f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=40:5[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")


def conforms(info: probe.ProbeInfo, spec: Mapping[str, Any]) -> bool:
    return (info.width, info.height) == platforms.target_size(dict(spec)) and info.vcodec == "h264" \
        and (info.acodec in (None, "aac")) and info.fps is not None and abs(info.fps - spec["fps"]) < 0.5


async def make_video(src: Path, dest: Path, spec: Mapping[str, Any], info: probe.ProbeInfo, opt: ExportOptions,
                     start_ms: int | None = None, end_ms: int | None = None) -> None:
    """Đưa `src` về đúng khung + mã hoá nền tảng (hoặc sao chép nếu đã đạt và không cắt phần)."""
    cut = start_ms is not None
    if not cut and conforms(info, spec):
        await anyio.to_thread.run_sync(shutil.copy2, src, dest)
        return
    vf = video_filter((info.width or 1, info.height or 1), platforms.target_size(dict(spec)), opt.reframe, opt.focus_x,
                      opt.focus_y)
    seek = ["-ss", f"{start_ms / 1000:.3f}"] if cut else []
    span = ["-t", f"{(end_ms - start_ms) / 1000:.3f}"] if cut else []
    total = (end_ms - start_ms) if cut else info.duration_ms
    with ffmpeg.atomic_output(dest) as tmp:
        await ffmpeg.run([*seek, "-i", str(src), *span, "-vf", vf, *platforms.ffmpeg_args(dict(spec)), str(tmp)],
                         total_ms=total or None)


# --- Một gói --------------------------------------------------------------------------------------------------------


def _set_row(services: Any, export_id: int, **values: Any) -> None:
    with services.db.session(immediate=True) as s:
        row = s.get(ExportPackage, export_id)
        meta = dict(row.meta_data or {})
        for k, v in values.items():
            if k == "meta":
                meta.update(v)
            elif k == "meta_data":
                meta = {**meta, **v}
            else:
                setattr(row, k, v)
        row.meta_data = meta


def _swap_in(staging: Path, final: Path) -> None:
    if final.exists():
        shutil.rmtree(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)


async def run_unit(services: Any, run: Any, export_id: int, step: Any = None) -> dict[str, Any]:
    """Tạo trọn một gói. Trả tóm tắt `{export_id, folder, files, parts, duration_ms, size, checks}`."""
    def load() -> tuple[dict, dict, dict, dict, ExportOptions]:
        from sagareel.db.models import Project

        with services.db.session() as s:
            row = s.get(ExportPackage, export_id)
            p = s.get(Project, row.project_id)
            proj = {"id": p.id, "key": p.key, "name": p.name, "description": p.description, "genre_key": p.genre_key,
                    "language": p.language}
            r = {"platform": row.platform, "lang": row.lang, "folder": row.folder, "sequence_id": row.sequence_ids[0],
                 "meta": dict(row.meta_data or {})}
        return proj, r, platforms.get(services, r["platform"], proj["id"]), sequence_facts(services, r["sequence_id"]), \
            ExportOptions.model_validate(r["meta"].get("options") or {})

    proj, r, spec, seq, opt = await anyio.to_thread.run_sync(load)
    plat, lang = r["platform"], r["lang"]
    step = step or (lambda frac, stage: None)
    final = package_folder(services, proj["key"], seq["key"], plat, lang)
    staging = final.with_name(final.name + ".part")
    await anyio.to_thread.run_sync(lambda: shutil.rmtree(staging, ignore_errors=True))
    staging.mkdir(parents=True)
    try:
        step(0.0, f"Dựng {seq['key']} cho {plat}-{lang}")
        out, extra = resolve_render(services, await render_final(services, run, seq["id"], plat, lang))
        info = await anyio.to_thread.run_sync(probe.probe, out)
        total_ms = info.duration_ms
        step(0.4, "Chuẩn hoá khung hình")

        d = spec["duration_s"]
        parts = [(0, total_ms)]
        if opt.split_parts and total_ms > d["max"] * 1000:
            bounds = [b for b, _ in seq["scene_marks"]]
            scale = total_ms / seq["clip_total_ms"] if seq["clip_total_ms"] else 1.0
            parts = platforms.plan_parts(total_ms, d["max"], min_s=d["min"], boundaries_ms=[round(b * scale) for b in bounds])
        n = len(parts)
        files: list[dict[str, Any]] = []
        for i, (a, b) in enumerate(parts, 1):
            name = "video.mp4" if n == 1 else f"video-part{i}.mp4"
            await make_video(out, staging / name, spec, info, opt, *((a, b) if n > 1 else (None, None)))
            files.append({"index": i, "total": n, "file": name, "start_ms": a, "end_ms": b})
            step(0.4 + 0.3 * i / n, f"Phần {i}/{n}")

        cues = await collect_cues(services, seq["id"], lang, extra, spec)
        sub_files: list[str] = []
        for f in files:
            suffix = "" if n == 1 else f"-part{f['index']}"
            sub_files += await anyio.to_thread.run_sync(write_subtitles, shift_cues(cues, f["start_ms"], f["end_ms"]) if n > 1 else cues, staging, suffix)

        step(0.75, "Tạo ảnh bìa")
        cover_title = seq["title"] if opt.cover_title else None
        cover_info = await covers.make_covers(staging / files[0]["file"], staging, spec, duration_ms=files[0]["end_ms"] - files[0]["start_ms"],
                                              title=cover_title, n=opt.covers, marks_ms=opt.cover_marks_ms)

        step(0.85, "Viết metadata")
        chapters = metadata.build_chapters(seq["scene_marks"], total_ms, min_s=spec["chapters"].get("min_seconds", 10),
                                           min_count=spec["chapters"].get("min_count", 3)) if spec["chapters"]["supported"] else []
        md = await metadata.build(services, spec=spec, project=proj, sequence=seq, lang=lang, chapters=chapters,
                                  music=seq["music"], use_llm=opt.use_llm,
                                  cover_timestamp_ms=cover_info[0]["timestamp_ms"] if cover_info else None)
        md["covers"] = cover_info
        md["subtitle_files"] = sub_files
        if n > 1:
            md["parts"] = []
            for f in files:
                pm = metadata.part_variant(spec, md, f["index"], n, metadata.shift_chapters(chapters, f["start_ms"], f["end_ms"]))
                pm.update(file=f["file"], start_ms=f["start_ms"], end_ms=f["end_ms"])
                md["parts"].append(pm)
        txt = metadata.to_text(md) if n == 1 else "\n----------\n\n".join(metadata.to_text(p) for p in md["parts"])
        (staging / "metadata.json").write_text(json.dumps(md, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "metadata.txt").write_text(txt, encoding="utf-8")

        step(0.92, "Kiểm tra")
        loud = await measure_loudness(staging / files[0]["file"])
        size = sum((staging / f["file"]).stat().st_size for f in files)
        report = checks.run_checks(spec, duration_s=total_ms / 1000, size_bytes=max((staging / f["file"]).stat().st_size for f in files),
                                   loudness=loud, cue_boxes=cue_boxes(cues), frame=platforms.target_size(dict(spec)),
                                   metadata=md, music=seq["music"], split_parts=opt.split_parts)
        await anyio.to_thread.run_sync(_swap_in, staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)  # đồng bộ: vẫn chạy được khi task đang bị huỷ
        raise

    names = sorted(p.name for p in final.iterdir())
    summary = {"export_id": export_id, "sequence_id": seq["id"], "seq_key": seq["key"], "platform": plat, "lang": lang,
               "folder": rel_path(services, final), "files": names, "parts": n, "duration_ms": total_ms, "size": size,
               "errors": report["errors"], "warnings": report["warnings"]}
    await anyio.to_thread.run_sync(lambda: _set_row(services, export_id, status="done", checks=report,
                                                    meta={"metadata": md, "files": names, "parts": n,
                                                          "duration_ms": total_ms, "size": size, "error": None,
                                                          "finished_at": utcnow().isoformat()}))
    return summary


# --- Lô -------------------------------------------------------------------------------------------------------------


def write_manifest(services: Any, project_key: str, batch: str, summaries: Sequence[Mapping[str, Any]]) -> list[str]:
    """`manifest.json` + `manifest.csv` của cả lô, trả đường dẫn (tương đối outputs/)."""
    d = exports_root(services, project_key) / "_batches" / batch
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"batch": batch, "created_at": utcnow().isoformat(),
                                                 "packages": list(summaries)}, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ["export_id", "seq_key", "platform", "lang", "status", "folder", "parts", "duration_ms", "size", "errors", "warnings", "error"]
    with (d / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in summaries:
            w.writerow({**s, "errors": len(s.get("errors") or []), "warnings": len(s.get("warnings") or [])})
    return [rel_path(services, d / "manifest.json"), rel_path(services, d / "manifest.csv")]


async def run_batch(services: Any, run: Any, export_ids: Sequence[int], batch: str) -> dict[str, Any]:
    """Chạy lần lượt các gói của lô (gói đã `done` bỏ qua: nối lại sau khi ngắt). Lỗi một gói không chặn gói khác."""
    total = len(export_ids)
    summaries: list[dict[str, Any]] = []
    project_key = ""
    failed = 0
    for i, eid in enumerate(export_ids):
        def claim(eid: int = eid) -> dict[str, Any] | None:
            from sagareel.db.models import Project

            with services.db.session(immediate=True) as s:
                row = s.get(ExportPackage, eid)
                if row is None or row.status == "superseded":
                    return None
                proj = s.get(Project, row.project_id)
                info = {"export_id": eid, "seq_key": (row.meta_data or {}).get("seq_key", ""), "platform": row.platform,
                        "lang": row.lang, "folder": row.folder, "project_key": proj.key, "status": row.status,
                        "meta": dict(row.meta_data or {}), "checks": dict(row.checks or {})}
                if row.status != "done":
                    row.status = "running"
                return info

        info = await anyio.to_thread.run_sync(claim)
        if info is None:
            continue
        project_key = info["project_key"]
        if info["status"] == "done":
            m = info["meta"]
            summaries.append({**{k: info[k] for k in ("export_id", "seq_key", "platform", "lang", "folder")},
                              "status": "done", "parts": m.get("parts", 1), "duration_ms": m.get("duration_ms"),
                              "size": m.get("size"), "errors": info["checks"].get("errors", []),
                              "warnings": info["checks"].get("warnings", [])})
            continue

        def step(frac: float, stage: str, i: int = i) -> None:
            run.progress(100.0 * (i + frac) / total, stage=stage)

        try:
            summary = await run_unit(services, run, eid, step)
            summaries.append({**summary, "status": "done"})
            run.log("info", f"Xong gói {info['seq_key']} {info['platform']}-{info['lang']}")
        except (TransientError, NeedsAttention, JobStateLost):
            await anyio.to_thread.run_sync(lambda eid=eid: _set_row(services, eid, status="pending"))
            raise
        except asyncio.CancelledError:
            keep = "pending" if getattr(run, "shutting_down", False) else "cancelled"
            _set_row(services, eid, status=keep)
            raise
        except Exception as e:
            msg = getattr(e, "detail", None) or getattr(e, "msg", None) or f"{type(e).__name__}: {e}"
            logger.exception("Gói xuất #%s lỗi", eid)
            run.log("error", f"Gói {info['seq_key']} {info['platform']}-{info['lang']} lỗi: {msg}")
            failed += 1
            await anyio.to_thread.run_sync(lambda eid=eid, msg=msg: _set_row(services, eid, status="failed", meta={"error": msg}))
            summaries.append({**{k: info[k] for k in ("export_id", "seq_key", "platform", "lang", "folder")},
                              "status": "failed", "error": msg, "errors": [], "warnings": []})
    manifest: list[str] = []
    if summaries:
        manifest = await anyio.to_thread.run_sync(write_manifest, services, project_key, batch, summaries)
        ids = [s["export_id"] for s in summaries]
        await anyio.to_thread.run_sync(lambda: [_set_row(services, i, meta={"manifest": manifest}) for i in ids])
    if failed and failed == len(summaries):
        raise JobFailed("export_failed", f"Cả {failed} gói xuất đều lỗi.", hint="Xem log job để biết từng lỗi.")
    return {"batch": batch, "packages": summaries, "manifest": manifest, "failed": failed}


# --- Zip ------------------------------------------------------------------------------------------------------------


class _Sink(io.RawIOBase):
    """Đích không tua được cho `zipfile`: gom byte, `drain()` lấy ra từng đợt để stream."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        self._buf += b
        self._pos += len(b)
        return len(b)

    def tell(self) -> int:
        return self._pos

    def drain(self) -> bytes:
        data = bytes(self._buf)
        self._buf.clear()
        return data


def iter_zip(folder: Path, chunk: int = 1 << 20) -> Iterator[bytes]:
    """Zip của `folder` dạng stream (không tạo file tạm); video/ảnh lưu thẳng (đã nén), chữ thì deflate."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", allowZip64=True) as zf:
        for p in sorted(folder.rglob("*")):
            if not p.is_file():
                continue
            comp = zipfile.ZIP_STORED if p.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png"} else zipfile.ZIP_DEFLATED
            info = zipfile.ZipInfo.from_file(p, p.relative_to(folder).as_posix())
            info.compress_type = comp
            with p.open("rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                while block := src.read(chunk):
                    dst.write(block)
                    if data := sink.drain():
                        yield data
        if data := sink.drain():
            yield data
    if data := sink.drain():
        yield data



# --- Kiểm tra trước khi xuất ----------------------------------------------------------------------------------------


def _project_facts(services: Any, project_id: int) -> dict[str, Any]:
    from sagareel.db.models import Project

    with services.db.session() as s:
        p = s.get(Project, project_id)
        return {"id": p.id, "key": p.key, "name": p.name, "description": p.description, "genre_key": p.genre_key,
                "language": p.language}


async def precheck(services: Any, sequence_id: int, platform: str, lang: str | None = None, *,
                   split_parts: bool = False, boxes: Sequence[Any] | None = None,
                   metadata_override: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`export.check`: kiểm tra một tập cho một nền tảng/ngôn ngữ. Có bản dựng cuối → đo thật (độ dài, dung lượng,
    loudness); chưa có → ước tính từ tổng thời lượng clip và cảnh báo `no_final_render`."""
    def load() -> tuple[dict, dict, dict, dict | None]:
        with services.db.session() as s:
            pid = entities.get(s, "sequence", sequence_id).project_id
        proj = _project_facts(services, pid)
        lng = lang or proj["language"]
        return proj, platforms.get(services, platform, pid), sequence_facts(services, sequence_id), \
            latest_render(services, sequence_id, platform, lng)

    proj, spec, seq, render = await anyio.to_thread.run_sync(load)
    lang = lang or proj["language"]
    md = metadata.template_metadata(project=proj, sequence=seq, lang=lang)
    md.update({k: v for k, v in (metadata_override or {}).items() if v is not None})
    if render is not None:
        duration_ms, size, estimated = int(render["duration_ms"] or 0), int(render["size"]), False
        loud = await measure_loudness(render["path"])
        frame = (render["width"], render["height"]) if render["width"] and render["height"] else None
    else:
        duration_ms = seq["clip_total_ms"]
        size, estimated, loud, frame = platforms.estimate_size_bytes(spec, duration_ms / 1000), True, None, None
    report = checks.run_checks(spec, duration_s=duration_ms / 1000, duration_estimated=estimated, size_bytes=size,
                               size_estimated=estimated, loudness=loud, measure_loudness=render is not None,
                               cue_boxes=boxes, frame=frame, metadata=md, music=seq["music"], split_parts=split_parts)
    if render is None:
        report["warnings"].insert(0, {"code": "no_final_render", "message": "Tập chưa có bản dựng cuối cho nền tảng này: "
                                      "độ dài và dung lượng chỉ là ước tính, loudness chưa đo.",
                                      "hint": "Chạy render.start variant=final trước khi kiểm tra lại."})
    report["info"] = {"sequence_id": sequence_id, "platform": platform, "lang": lang, "duration_ms": duration_ms,
                      "size": size, "estimated": estimated}
    return report
