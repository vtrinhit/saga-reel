"""Clone từ video (spec §14.6): tách cảnh, LLM vision viết lại prompt ảnh/chuyển động thay nhân vật, tạo clip.

Chỉ phân tích: khung hình giữa cảnh chỉ đi tới LLM rồi bị xoá — không đăng ký thành media, không vào đầu ra.
Nhịp được giữ: thời lượng cảnh -> thời lượng clip (mọi engine giữ đúng độ dài cảnh; ai_video xin mức provider ≥ độ
dài đó rồi bản dựng cắt sát). STT chưa có: lời thoại nhận từ `transcript` dán tay, chia theo tỉ lệ thời lượng các cảnh.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select

from sagareel.container import Services
from sagareel.core import rank
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound, ValidationFailed
from sagareel.db.models import Asset, DialogueLine, Sequence
from sagareel.media import probe
from sagareel.services import clips, entities, llm, media
from sagareel.services.channel import scenes as sc
from sagareel.services.jobs import JobFailed

JOB_TYPE = "clone.from_video"
TASK = "clone_scene"
AUTO_MIN_S = 4.5  # ngắn hơn -> still_motion giữ đúng thời lượng
AUTO_MAX_S = 17.0
MAX_SCENES = 60
ACTOR = "system:clone"
_SENT_RE = re.compile(r"[^.!?…\n]+[.!?…]*", re.UNICODE)


# --- Nhịp / thời lượng ------------------------------------------------------------------------------------------------


def engine_and_duration(dur_s: float, engine: str = "auto") -> tuple[str, int]:
    """Cảnh dur_s -> (engine, duration_ms). Thời lượng clip = đúng độ dài cảnh (làm tròn ms, ≥ 0,5 s) cho mọi engine;
    ai_video tự xin mức provider ≥ thời lượng đó lúc tạo rồi bản dựng cắt sát (core.durations)."""
    if engine == "auto":
        engine = "ai_video" if AUTO_MIN_S <= dur_s <= AUTO_MAX_S else "still_motion"
    return engine, max(500, round(dur_s * 1000))


def split_transcript(text: str, scene_list: list[sc.Scene]) -> list[str]:
    """Chia câu của `text` cho các cảnh theo tỉ lệ thời lượng: câu thuộc cảnh chứa điểm giữa của nó (theo số ký tự)."""
    out = [""] * len(scene_list)
    sents = [m.group(0).strip() for m in _SENT_RE.finditer(text or "") if m.group(0).strip()]
    total_chars = sum(len(x) for x in sents)
    total_s = scene_list[-1].end_s if scene_list else 0.0
    if not sents or total_s <= 0:
        return out
    pos = 0
    parts: list[list[str]] = [[] for _ in scene_list]
    for sent in sents:
        mid_t = (pos + len(sent) / 2) / total_chars * total_s
        pos += len(sent)
        idx = next((i for i, x in enumerate(scene_list) if mid_t < x.end_s), len(scene_list) - 1)
        parts[idx].append(sent)
    return [" ".join(p) for p in parts]


# --- Kế hoạch cảnh ---------------------------------------------------------------------------------------------------


def _video(services: Services, media_id: int) -> tuple[Path, float, int | None]:
    with services.db.session() as s:
        m = media.get(s, media_id)
        if m.kind != "video":
            raise AppError("media_kind_mismatch", f"Media #{media_id} là {m.kind}, cần video.", status=422)
        return media.file_path(services.settings, m), (m.duration_ms or 0) / 1000, m.project_id


async def detect(services: Services, media_id: int, threshold: float, min_scene_s: float,
                 cancel=None) -> tuple[Path, list[sc.Scene]]:
    path, duration_s, _pid = await anyio.to_thread.run_sync(_video, services, media_id)
    if duration_s <= 0:
        duration_s = (await anyio.to_thread.run_sync(probe.probe, path)).duration_ms / 1000
    if duration_s <= 0:
        raise AppError("media_kind_mismatch", f"Media #{media_id} không có thời lượng video.", status=422)
    cuts = await sc.detect_cuts(path, threshold, cancel=cancel)
    return path, sc.build_scenes(cuts, duration_s, min_scene_s)


def resolve_replace_map(services: Services, sequence_id: int, replace_map: dict[str, str]) -> tuple[int, list[dict]]:
    """Kiểm tra tập + khoá asset trong replace_map. Trả (project_id, [{original, asset_id, key, name, description}])."""
    with services.db.session() as s:
        seq = s.get(Sequence, sequence_id)
        if seq is None or seq.deleted_at is not None:
            raise NotFound(f"Không tìm thấy tập #{sequence_id}.")
        rows, missing = [], []
        for original, key in replace_map.items():
            a = s.scalars(select(Asset).where(Asset.project_id == seq.project_id, Asset.key == key,
                                              Asset.deleted_at.is_(None))).first()
            if a is None:
                missing.append(key)
                continue
            rows.append({"original": original.strip(), "asset_id": a.id, "key": a.key, "name": a.name,
                         "description": a.description})
        if missing:
            raise AppError("asset_not_found", f"Không có tài sản với mã: {', '.join(missing)}.", status=422,
                           hint="Mã trong replace_map phải là `key` của tài sản trong dự án (asset.list).",
                           extra={"keys": missing})
        return seq.project_id, rows


async def plan(services: Services, media_id: int, threshold: float, min_scene_s: float, engine: str,
               transcript: str | None, cancel=None) -> dict[str, Any]:
    _path, scene_list = await detect(services, media_id, threshold, min_scene_s, cancel)
    lines = split_transcript(transcript or "", scene_list)
    items = []
    for i, (x, line) in enumerate(zip(scene_list, lines, strict=True), 1):
        eng, dur = engine_and_duration(x.duration_s, engine)
        items.append({"index": i, "start_s": round(x.start_s, 3), "end_s": round(x.end_s, 3),
                      "scene_s": round(x.duration_s, 3), "engine": eng, "duration_ms": dur, "dialogue": line})
    return {"scenes": items, "scene_count": len(items), "llm_calls": len(items),
            "total_s": round(scene_list[-1].end_s, 3) if scene_list else 0.0}


# --- LLM ----------------------------------------------------------------------------------------------------------------

_ENUM_KEYS = tuple(clips.SHOT_ENUMS)


def scene_input(i: int, n: int, x: sc.Scene, line: str, style_block: str, rmap: list[dict]) -> str:
    lines = [f"Scene {i} of {n}, duration {x.duration_s:.1f} s. The attached image is the keyframe from the middle of the scene."]
    if style_block:
        lines.append(f"Project style: {style_block}")
    if rmap:
        lines.append("Replacement table (original character description -> new asset):")
        for r in rmap:
            lines.append(f'- original: "{r["original"]}" -> {r["name"]} ({r["description"][:300]})')
    else:
        lines.append("No replacement table: invent neutral characters; do not describe real people.")
    if line:
        lines.append(f"Narration/dialogue spoken during this scene: {line}")
    return "\n".join(lines)


def clean_scene(data: Any, rmap: list[dict]) -> dict[str, Any]:
    """JSON của LLM -> trường clip hợp lệ (enum lạ bị bỏ, không làm hỏng cả job). Không phải object -> lỗi."""
    if not isinstance(data, dict):
        raise JobFailed("llm_invalid_json", "LLM không trả object JSON cho clone_scene.",
                        hint="Thử model khác hoặc sửa template clone_scene.")
    out: dict[str, Any] = {}
    for k in ("title", "image_prompt", "motion_prompt", "first_frame_desc", "last_frame_desc", "sfx_desc"):
        v = data.get(k)
        out[k] = v.strip() if isinstance(v, str) else ""
    for k in _ENUM_KEYS:
        v = data.get(k)
        if isinstance(v, str):
            v = clips.SCREEN_DIR_ALIASES.get(v.strip(), v.strip()) if k == "screen_dir" else v.strip()
            out[k] = v if v in clips.SHOT_ENUMS[k] else ""
    present = {str(p).strip().lower() for p in data.get("present", []) if isinstance(p, str)} \
        if isinstance(data.get("present"), list) else set()
    out["assets"] = [{"asset_id": r["asset_id"], "role": "character"} for r in rmap
                     if r["original"].lower() in present]
    if not out["image_prompt"]:
        raise JobFailed("llm_invalid_json", "LLM không trả image_prompt cho một cảnh.",
                        hint="Thử model khác hoặc sửa template clone_scene.")
    return out


# --- Chạy ----------------------------------------------------------------------------------------------------------------


def _style_block(services: Services, project_id: int) -> str:
    from sagareel.db.models import Project

    with services.db.session() as s:
        p = s.get(Project, project_id)
        return (p.style_block if p else "") or ""


def _create(services: Services, sequence_id: int, rows: list[dict[str, Any]], dialogues: list[str]) -> list[dict]:
    ctx = OpContext(services=services, actor=ACTOR, role="owner")
    created = clips.bulk_create(ctx, sequence_id, rows)
    todo = [(c["id"], t) for c, t in zip(created, dialogues, strict=True) if t]
    if todo:
        u = entities.uow(ctx, f"Gắn lời thoại cho {len(todo)} clip clone", "clone.from_video")
        with u as s:
            u.project_id = clips.get_sequence(s, sequence_id).project_id
            for cid, text in todo:
                s.add(DialogueLine(clip_id=cid, rank=rank.between(None, None), text=text, source="tts"))
    return created


async def run(services: Services, p: dict[str, Any], *, progress: Callable[[float, str], None] | None = None,
              log: Callable[[str, str], None] | None = None, cancel=None, job_id: int = 0) -> dict[str, Any]:
    replace_map = {str(k): str(v) for k, v in (p.get("replace_map") or {}).items()}
    project_id, rmap = await anyio.to_thread.run_sync(
        resolve_replace_map, services, int(p["sequence_id"]), replace_map)
    path, scene_list = await detect(services, int(p["media_id"]), float(p.get("scene_threshold", 0.3)),
                                    float(p.get("min_scene_s", 1.0)), cancel)
    if len(scene_list) > MAX_SCENES:
        raise JobFailed("too_many_scenes", f"Video có {len(scene_list)} cảnh (tối đa {MAX_SCENES}).",
                        hint="Tăng scene_threshold hoặc min_scene_s để ít cảnh hơn.")
    lines = split_transcript(p.get("transcript") or "", scene_list)
    style_block = await anyio.to_thread.run_sync(_style_block, services, project_id)
    work = services.settings.cache_dir / "clone" / f"job{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    try:
        n = len(scene_list)
        for i, (x, line) in enumerate(zip(scene_list, lines, strict=True), 1):
            if progress:
                progress((i - 1) / n * 100, f"Viết lại cảnh {i}/{n}")
            frame = await sc.extract_frame(path, x.mid_s, work / f"scene_{i:03d}.jpg", cancel=cancel)
            outcome = await llm.run_task(services, TASK, {"input": scene_input(i, n, x, line, style_block, rmap)},
                                         images=[frame], log=log, project_id=project_id)
            f = clean_scene(outcome.data, rmap)
            eng, dur = engine_and_duration(x.duration_s, p.get("engine") or "auto")
            rows.append({**f, "engine": eng, "duration_ms": dur, "prompt_mode": "synced",
                         "custom": {"clone": {"media_id": int(p["media_id"]), "start_s": round(x.start_s, 3),
                                              "end_s": round(x.end_s, 3), "scene": i}}})
    finally:
        shutil.rmtree(work, ignore_errors=True)  # khung gốc chỉ phục vụ phân tích, không giữ lại
    created = await anyio.to_thread.run_sync(_create, services, int(p["sequence_id"]), rows, lines)
    if progress:
        progress(100, "Xong")
    return {"sequence_id": int(p["sequence_id"]), "clip_ids": [c["id"] for c in created], "scenes": len(created),
            "engines": {e: sum(1 for r in rows if r["engine"] == e) for e in {r["engine"] for r in rows}},
            "dialogue_lines": sum(1 for t in lines if t)}


def validate_params(threshold: float, min_scene_s: float) -> None:
    if not 0 < threshold < 1:
        raise ValidationFailed("scene_threshold phải nằm trong khoảng (0, 1).")
    if min_scene_s <= 0:
        raise ValidationFailed("min_scene_s phải lớn hơn 0.")
