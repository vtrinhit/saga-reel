"""Phân tích video mẫu: đo nhịp cắt bằng ffmpeg, lưới keyframe, LLM vision (JSON chặt), gộp nhiều video (spec §14.5)."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio

from sagareel.container import Services
from sagareel.core.errors import NotFound
from sagareel.db.base import utcnow
from sagareel.db.models import ChannelStudy
from sagareel.media import probe
from sagareel.services import llm, media
from sagareel.services.channel import scenes as sc
from sagareel.services.jobs import JobFailed

JOB_TYPE = "channel.analyze"
TASK = "analyze_channel"
TRANSITIONS = ("cut", "crossfade", "fade", "fadeblack", "fadewhite", "dissolve")
ENDS = ("cliffhanger", "loop", "loop_or_cta", "cta", "resolve", "none")
DIALOGUE_STYLES = ("narration", "dialogue", "mixed", "none")


# --- Chuẩn hoá JSON của LLM -----------------------------------------------------------------------------------------


def _s(v: Any, limit: int = 600) -> str:
    return v.strip()[:limit] if isinstance(v, str) else ""


def _strs(v: Any, n: int = 12) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return [x.strip() for x in v if isinstance(x, str) and x.strip()][:n] if isinstance(v, list) else []


def _named(v: Any, n: int = 8) -> list[dict[str, str]]:
    out = []
    for x in v if isinstance(v, list) else []:
        if isinstance(x, dict) and _s(x.get("name")):
            out.append({"name": _s(x["name"], 120), "description": _s(x.get("description"))})
    return out[:n]


def _pick(v: Any, allowed: tuple[str, ...]) -> str:
    return v if isinstance(v, str) and v in allowed else ""


def _obj(data: dict, key: str) -> dict:
    return data.get(key) if isinstance(data.get(key), dict) else {}


def normalize(data: Any) -> dict[str, Any]:
    """JSON của LLM -> cấu trúc chuẩn (trường thiếu = rỗng). Không phải object -> `llm_invalid_json`."""
    if not isinstance(data, dict):
        raise JobFailed("llm_invalid_json", "LLM không trả một object JSON cho analyze_channel.",
                        hint="Thử model khác hoặc sửa template analyze_channel.")
    style, pacing, hook = _obj(data, "style"), _obj(data, "pacing"), _obj(data, "hook")
    structure, subs, audio = _obj(data, "structure"), _obj(data, "subtitles"), _obj(data, "audio")
    gen = _obj(data, "genre_suggestion")
    return {
        "style": {"palette": _strs(style.get("palette")), "lighting": _s(style.get("lighting")),
                  "lens": _s(style.get("lens"), 60), "look_words": _strs(style.get("look_words")),
                  "rendering": _s(style.get("rendering"))},
        "pacing": {"feel": _s(pacing.get("feel"), 60), "notes": _s(pacing.get("notes"))},
        "hook": {"pattern": _s(hook.get("pattern"), 60), "description": _s(hook.get("description"))},
        "structure": {"beats": _strs(structure.get("beats"), 16), "formula": _s(structure.get("formula"))},
        "subtitles": {"present": bool(subs.get("present")), "style": _s(subs.get("style")),
                      "position": _s(subs.get("position"), 60)},
        "audio": {"music": _s(audio.get("music")), "voice": _s(audio.get("voice")), "sfx": _s(audio.get("sfx"))},
        "genre_suggestion": {"transition": _pick(gen.get("transition"), TRANSITIONS),
                             "end": _pick(gen.get("end"), ENDS),
                             "dialogue_style": _pick(gen.get("dialogue_style"), DIALOGUE_STYLES)},
        "style_block": _s(data.get("style_block"), 1500),
        "negative_block": _s(data.get("negative_block"), 800),
        "characters": _named(data.get("characters")),
        "locations": _named(data.get("locations")),
    }


# --- Số đo nhịp -------------------------------------------------------------------------------------------------------


def metrics(duration_s: float, scene_list: list[sc.Scene]) -> dict[str, Any]:
    lengths = [round(x.duration_s, 3) for x in scene_list]
    n = max(1, len(scene_list))
    cuts = [x.start_s for x in scene_list[1:]]
    return {
        "duration_s": round(duration_s, 3), "scenes": len(scene_list), "cuts": len(cuts),
        "cut_rate_per_min": round(len(cuts) / duration_s * 60, 2) if duration_s > 0 else 0.0,
        "avg_shot_s": round(duration_s / n, 3),
        "median_shot_s": round(statistics.median(lengths), 3) if lengths else 0.0,
        "min_shot_s": min(lengths, default=0.0), "max_shot_s": max(lengths, default=0.0),
        "hook_cuts_3s": sum(1 for c in cuts if c < sc.HOOK_S),
        "cut_times_s": [round(c, 3) for c in cuts[:200]],
    }


def sample_text(title: str, m: dict[str, Any], captions: str, frame_times: list[float]) -> str:
    lines = [
        f"Video: {title}",
        (f"Measured: duration {m['duration_s']} s, {m['cuts']} cuts ({m['cut_rate_per_min']} cuts/min), "
         f"average shot length {m['avg_shot_s']} s, {m['hook_cuts_3s']} cuts in the first 3 s."),
        f"Contact sheet: {len(frame_times)} keyframes in time order (left to right, top to bottom), at "
        + ", ".join(f"{t:.1f}s" for t in frame_times) + ". The first frames are the 3-second hook.",
    ]
    if captions.strip():
        lines.append("Titles/notes from the user:\n" + captions.strip()[:2000])
    return "\n".join(lines)


# --- Gộp nhiều video ---------------------------------------------------------------------------------------------------


def _top(items: list[str], n: int) -> list[str]:
    c = Counter(i.lower() for i in items)
    first: dict[str, str] = {}
    for i in items:
        first.setdefault(i.lower(), i)
    return [first[k] for k, _ in c.most_common(n)]


def _mode(items: list[str]) -> str:
    items = [i for i in items if i]
    return Counter(items).most_common(1)[0][0] if items else ""


def _merge_named(lists: list[list[dict[str, str]]], n: int = 8) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for lst in lists:
        for it in lst:
            k = it["name"].lower()
            if k in seen:
                seen[k]["count"] += 1
            else:
                seen[k] = {**it, "count": 1}
    return sorted(seen.values(), key=lambda x: -x["count"])[:n]


def _span(lo: float, hi: float) -> list[float]:
    lo, hi = round(max(0.5, lo), 1), round(max(0.5, hi), 1)
    return [lo, max(lo, hi)]


def genre_suggestion(met: list[dict[str, Any]], votes: list[dict[str, str]]) -> dict[str, Any]:
    """Số liệu preset thể loại lấy từ số đo thật; transition/end/dialogue_style theo đa số LLM (hợp lệ) rồi mặc định."""
    durs = [m["duration_s"] for m in met if m["duration_s"] > 0] or [30.0]
    avg_shot = statistics.mean(m["avg_shot_s"] for m in met) if met else 4.0
    lo_d, hi_d = min(durs), max(durs)
    if hi_d - lo_d < 0.2 * lo_d:  # ít video / độ dài gần nhau -> nới +-20%
        lo_d, hi_d = lo_d * 0.8, hi_d * 1.2
    episode = _span(lo_d, hi_d)
    clip_s = _span(avg_shot * 0.8, avg_shot * 1.25)
    clips = [max(1, math.floor(episode[0] / clip_s[1])), max(1, math.ceil(episode[1] / clip_s[0]))]
    clips[1] = max(clips)
    return {
        "episode_s": episode, "clip_s": clip_s, "clips": clips,
        "transition": _mode([v["transition"] for v in votes]) or "cut",
        "end": _mode([v["end"] for v in votes]) or "loop",
        "dialogue_style": _mode([v["dialogue_style"] for v in votes]) or "narration",
        "hook": {"window_s": sc.HOOK_S, "visual_in_s": 1.5},
    }


def aggregate(videos: list[dict[str, Any]]) -> dict[str, Any]:
    """Gộp kết quả của các video mẫu thành một hồ sơ kênh."""
    an = [v["analysis"] for v in videos]
    met = [v["metrics"] for v in videos]
    n = max(1, len(videos))
    beats = max((a["structure"]["beats"] for a in an), key=len, default=[])
    present = [a["subtitles"] for a in an if a["subtitles"]["present"]]
    blocks = [a["style_block"] for a in an if a["style_block"]]
    negs = [a["negative_block"] for a in an if a["negative_block"]]
    return {
        "videos": len(videos),
        "pacing": {
            "avg_duration_s": round(sum(m["duration_s"] for m in met) / n, 2),
            "avg_shot_s": round(sum(m["avg_shot_s"] for m in met) / n, 3),
            "cut_rate_per_min": round(sum(m["cut_rate_per_min"] for m in met) / n, 2),
            "avg_hook_cuts_3s": round(sum(m["hook_cuts_3s"] for m in met) / n, 2),
            "feel": _mode([a["pacing"]["feel"] for a in an]),
            "notes": [a["pacing"]["notes"] for a in an if a["pacing"]["notes"]][:5],
        },
        "style": {
            "palette": _top([c for a in an for c in a["style"]["palette"]], 6),
            "look_words": _top([w for a in an for w in a["style"]["look_words"]], 10),
            "lighting": _mode([a["style"]["lighting"] for a in an]),
            "lens": _mode([a["style"]["lens"] for a in an]),
            "rendering": _mode([a["style"]["rendering"] for a in an]),
        },
        "hook": {"patterns": _top([a["hook"]["pattern"] for a in an if a["hook"]["pattern"]], 4),
                 "descriptions": [a["hook"]["description"] for a in an if a["hook"]["description"]][:5]},
        "structure": {"beats": beats, "formulas": [a["structure"]["formula"] for a in an
                                                    if a["structure"]["formula"]][:5]},
        "subtitles": {"present_ratio": round(len(present) / n, 2), "style": _mode([p["style"] for p in present]),
                      "position": _mode([p["position"] for p in present])},
        "audio": {k: [a["audio"][k] for a in an if a["audio"][k]][:3] for k in ("music", "voice", "sfx")},
        "genre_suggestion": genre_suggestion(met, [a["genre_suggestion"] for a in an]),
        "style_block": max(blocks, key=len, default=""),
        "negative_block": max(negs, key=len, default=""),
        "characters": _merge_named([a["characters"] for a in an]),
        "locations": _merge_named([a["locations"] for a in an]),
    }


# --- Chạy ----------------------------------------------------------------------------------------------------------


def _inputs(services: Services, study_id: int) -> tuple[dict[str, Any], list[tuple[int, Path, int]]]:
    with services.db.session() as s:
        st = s.get(ChannelStudy, study_id)
        if st is None or st.deleted_at is not None:
            raise NotFound(f"Không tìm thấy phân tích kênh #{study_id}.")
        info = {"title": st.title, "captions": st.captions, "project_id": st.project_id}
        files = []
        for mid in st.media_ids:
            m = media.get(s, int(mid))
            files.append((m.id, media.file_path(services.settings, m), m.duration_ms or 0))
    return info, files


async def analyze_video(services: Services, work: Path, mid: int, path: Path, db_duration_ms: int, title: str,
                        captions: str, project_id: int | None, *, threshold: float, min_scene_s: float,
                        max_frames: int, log: Callable[[str, str], None] | None, cancel=None) -> dict[str, Any]:
    duration_s = db_duration_ms / 1000
    if duration_s <= 0:
        duration_s = (await anyio.to_thread.run_sync(probe.probe, path)).duration_ms / 1000
    if duration_s <= 0:
        raise JobFailed("media_kind_mismatch", f"Media #{mid} không có thời lượng video.")
    cuts = await sc.detect_cuts(path, threshold, cancel=cancel)
    scene_list = sc.build_scenes(cuts, duration_s, min_scene_s)
    met = metrics(duration_s, scene_list)
    times = sc.pick_times(duration_s, scene_list, max_frames)
    frames = [await sc.extract_frame(path, t, work / f"v{mid}_{i:02d}.jpg", cancel=cancel)
              for i, t in enumerate(times)]
    sheet = await anyio.to_thread.run_sync(lambda: sc.contact_sheet(frames, work / f"sheet_{mid}.jpg"))
    outcome = await llm.run_task(services, TASK, {"input": sample_text(title, met, captions, times)},
                                 images=[sheet], log=log, project_id=project_id)
    analysis = normalize(outcome.data)

    def store() -> int:
        with services.db.session() as s:
            return media.register(s, sheet, kind="image", origin="derived", settings=services.settings,
                                  project_id=project_id,
                                  meta={"derived_from": mid, "role": "channel_contact_sheet",
                                        "frame_times_s": times}).id

    sheet_id = await anyio.to_thread.run_sync(store)
    return {"media_id": mid, "metrics": met, "frame_times_s": times, "sheet_media_id": sheet_id,
            "analysis": analysis, "model": outcome.model}


async def analyze_study(services: Services, study_id: int, *, threshold: float = 0.3, min_scene_s: float = 0.5,
                        max_frames: int = sc.MAX_SHEET_FRAMES,
                        progress: Callable[[float, str], None] | None = None,
                        log: Callable[[str, str], None] | None = None, cancel=None) -> dict[str, Any]:
    from sagareel.services.channel import study as study_svc

    info, files = await anyio.to_thread.run_sync(_inputs, services, study_id)
    await anyio.to_thread.run_sync(study_svc.set_state, services, study_id, "running")
    work = services.settings.cache_dir / "channel" / str(study_id)
    work.mkdir(parents=True, exist_ok=True)
    videos: list[dict[str, Any]] = []
    try:
        for i, (mid, path, dur_ms) in enumerate(files):
            if progress:
                progress(i / len(files) * 100, f"Phân tích video {i + 1}/{len(files)}")
            videos.append(await analyze_video(
                services, work, mid, path, dur_ms, f"#{mid}", info["captions"], info["project_id"],
                threshold=threshold, min_scene_s=min_scene_s, max_frames=max_frames, log=log, cancel=cancel))
    except BaseException:
        await anyio.to_thread.run_sync(study_svc.set_state, services, study_id, "failed")
        raise
    result = {"videos": videos, "aggregate": aggregate(videos),
              "params": {"scene_threshold": threshold, "min_scene_s": min_scene_s, "max_frames": max_frames},
              "analyzed_at": utcnow().isoformat()}
    await anyio.to_thread.run_sync(study_svc.set_state, services, study_id, "done", result)
    if progress:
        progress(100, "Xong")
    return {"study_id": study_id, "videos": len(videos), "aggregate": result["aggregate"]}
