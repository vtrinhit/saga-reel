"""`pipeline.idea_to_video`: ý tưởng → dự án + N tập → quy trình (bible → dàn ý → … → video), có `dry_run` ước tính."""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, select

from sagareel.core.context import OpContext
from sagareel.core.errors import ValidationFailed
from sagareel.db.models import Sequence
from sagareel.services import entities, genres, projects, samples
from sagareel.services import sequences as seq_svc
from sagareel.services.pipeline import engine
from sagareel.services.pipeline import workflow as W
from sagareel.services.story import text as T
from sagareel.services.story import writer


def _genre(ctx: OpContext, genre: str, project_id: int | None, from_project_id: int | None = None) -> dict[str, Any]:
    """Số liệu thể loại: của dự án đích (hoặc dự án nguồn sẽ nhân bản) nếu cùng thể loại, không thì số liệu tham khảo."""
    with ctx.services.db.session() as s:
        for pid in (project_id, from_project_id):
            if pid is not None:
                p = entities.get(s, "project", pid)
                if p.genre and p.genre_key == genre:
                    return dict(p.genre)
    ref = genres.reference(genre)
    if ref is None:
        raise ValidationFailed(f"Không có số liệu thể loại '{genre}'.", code="genre_unknown",
                               hint="Thể loại tham khảo: " + ", ".join(x["key"] for x in genres.references()) + ".")
    return ref


def _prompt_warnings(ctx: OpContext, project_id: int | None) -> list[dict[str, Any]]:
    """Cảnh báo prompt (tĩnh, không chặn) của dự án sẽ chạy; chưa có dự án nguồn → mọi bước AI viết sẽ chờ nhập."""
    from sagareel.services import prompts

    if project_id is None:
        return [{"key": "", "code": "prompt_missing", "message": "Không có dự án mẫu cùng thể loại: dự án mới trống, các "
                 "bước AI viết sẽ dừng chờ nhập (viết prompt bằng prompt.set hoặc gửi kết quả bằng story.submit)."}]
    with ctx.services.db.session() as s:
        return prompts.check_session(s, project_id)["warnings"]


def _source(ctx: OpContext, genre: str, from_project_id: int | None) -> int | None:
    """Dự án nguồn để nhân bản (prompt + phong cách): chỉ định rõ, hoặc dự án mẫu ứng với thể loại."""
    if from_project_id is not None:
        return from_project_id
    with ctx.services.db.session() as s:
        row = samples.find_sample(s, genre)
        return row.id if row is not None else None


def estimate(ctx: OpContext, *, idea: str, genre: str, episodes: int, clips_per_episode: int,
             workflow_key: str = W.DEFAULT_WORKFLOW, project_id: int | None = None,
             steps: dict[str, bool] | None = None, from_project_id: int | None = None) -> dict[str, Any]:
    src = None if project_id is not None else _source(ctx, genre, from_project_id)
    g = _genre(ctx, genre, project_id, src)
    with ctx.services.db.session() as s:
        wf = W.load(s, workflow_key, project_id if project_id is not None else src)
        enabled = [x["step"] for x in W.apply_overrides(wf, dict(steps or {})) if x["enabled"]]
        conns = {k: writer.setting(s, f"{k}.connection") for k in ("image", "video")}
    warnings: list[dict[str, Any]] = []
    lo, hi = writer._span(g.get("clips"), (1, 1000))
    if not lo <= clips_per_episode <= hi:
        warnings.append({"code": "clips_outside_genre", "message": f"{clips_per_episode} clip/tập ngoài khoảng "
                         f"{lo:g}–{hi:g} của thể loại {genre}."})
    cs = writer._span(g.get("clip_s"), (6, 10))
    still = genre in writer.STILL_GENRES
    clips = episodes * clips_per_episode
    images = clips if "generate_images" in enabled else 0
    videos = clips if ("generate_videos" in enabled and not still) else 0
    batches = math.ceil(clips_per_episode / writer.PROMPT_BATCH)
    per_ep = sum(n for step, n in (("write_script", 1), ("split_clips", 1), ("image_prompts", batches),
                                   ("motion_prompts", 0 if still else batches), ("memory_summarize", 1))
                 if step in enabled)
    llm_calls = ("write_bible" in enabled) + ("outline" in enabled) * math.ceil(episodes / 10) + episodes * per_ep
    seconds = round(clips * (cs[0] + cs[1]) / 2, 1)
    ep_lo, ep_hi = writer._span(g.get("episode_s"), (60, 90))
    if not ep_lo <= seconds / max(1, episodes) <= ep_hi * 1.25:
        warnings.append({"code": "episode_length", "message": f"≈ {seconds / max(1, episodes):.0f} s/tập, thể loại "
                         f"khuyên {ep_lo:g}–{ep_hi:g} s."})
    return {"dry_run": True, "idea": T.clip_text(idea, 200), "genre": genre, "workflow": workflow_key,
            "steps": enabled, "episodes": episodes, "clips": clips, "images": images, "videos": videos,
            "still_motion_clips": clips if still else 0, "llm_calls": int(llm_calls), "seconds": seconds,
            "connections": conns, "from_project_id": src,
            "gates": [x for x in enabled if W.is_gate(x)], "warnings": warnings,
            "prompt_warnings": _prompt_warnings(ctx, project_id if project_id is not None else src)}


def create(ctx: OpContext, *, idea: str, genre: str, episodes: int, clips_per_episode: int,
           workflow_key: str = W.DEFAULT_WORKFLOW, project_id: int | None = None, name: str | None = None,
           steps: dict[str, bool] | None = None, platforms: list[str] | None = None,
           from_project_id: int | None = None) -> dict[str, Any]:
    est = estimate(ctx, idea=idea, genre=genre, episodes=episodes, clips_per_episode=clips_per_episode,
                   workflow_key=workflow_key, project_id=project_id, steps=steps, from_project_id=from_project_id)
    if project_id is None:
        title = (name or T.clip_text(idea.strip().splitlines()[0], 60)).strip() or "Dự án mới"
        project_id = projects.create(ctx, name=title, genre_key=genre, from_project_id=est["from_project_id"],
                                     include={"library": False},
                                     target_platforms=list(platforms) if platforms else None)["id"]
    else:
        with ctx.services.db.session() as s:
            entities.get(s, "project", project_id)
    ids = []
    for _ in range(episodes):
        with ctx.services.db.session() as s:
            n = int(s.scalar(select(func.coalesce(func.max(Sequence.number), 0)).where(
                Sequence.project_id == project_id, Sequence.deleted_at.is_(None))) or 0) + 1
        ids.append(seq_svc.create(ctx, project_id, f"Tập {n}", number=n)["id"])
    overrides = {"steps": dict(steps or {}), "params": {"idea": idea, "write_bible": {"idea": idea, "genre": genre}},
                 "genre_overrides": {"clips": [clips_per_episode, clips_per_episode]}}
    if platforms:
        overrides["platforms"] = list(platforms)
    started = engine.start(ctx, ids, workflow_key, overrides)
    return {"project_id": project_id, "sequence_ids": ids, "estimate": est, "pipeline": started}
