"""`pipeline.idea_to_video`: ý tưởng → dự án + N tập → quy trình (bible → dàn ý → … → video), có `dry_run` ước tính."""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, select

from sagareel.core.context import OpContext
from sagareel.core.errors import ValidationFailed
from sagareel.db.models import Sequence
from sagareel.services import budget, entities, presets, projects
from sagareel.services import sequences as seq_svc
from sagareel.services.pipeline import engine
from sagareel.services.pipeline import workflow as W
from sagareel.services.story import text as T
from sagareel.services.story import writer


def _genre(ctx: OpContext, genre: str, project_id: int | None) -> dict[str, Any]:
    presets.ensure_builtins(ctx.services)
    with ctx.services.db.session() as s:
        row = presets.find_row(s, "genre", genre, project_id)
        if row is None:
            raise ValidationFailed(f"Không có preset thể loại '{genre}'.", code="genre_unknown",
                                   hint="Gọi preset.list kind=genre (storyboard, montage, series, serial, narration).")
        return dict(row.data)


def estimate(ctx: OpContext, *, idea: str, genre: str, episodes: int, clips_per_episode: int,
             workflow_key: str = W.DEFAULT_WORKFLOW, project_id: int | None = None,
             steps: dict[str, bool] | None = None) -> dict[str, Any]:
    g = _genre(ctx, genre, project_id)
    with ctx.services.db.session() as s:
        wf = W.load(s, workflow_key, project_id)
        enabled = [x["step"] for x in W.apply_overrides(wf, dict(steps or {})) if x["enabled"]]
        budgets = {k: budget.status(s, k, resolver=ctx.services.resolver) for k in ("image", "video")}
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
    for kind, n in (("image", images), ("video", videos)):
        rem = budgets[kind].get("remaining")
        if n and rem is not None and n > rem:
            warnings.append({"code": "budget", "message": f"Cần {n} {kind} nhưng ngân sách còn {rem} (kết nối giả "
                             "không tính)."})
    return {"dry_run": True, "idea": T.clip_text(idea, 200), "genre": genre, "workflow": workflow_key,
            "steps": enabled, "episodes": episodes, "clips": clips, "images": images, "videos": videos,
            "still_motion_clips": clips if still else 0, "llm_calls": int(llm_calls), "seconds": seconds,
            "connections": conns, "budget": budgets,
            "gates": [x for x in enabled if W.is_gate(x)], "warnings": warnings}


def create(ctx: OpContext, *, idea: str, genre: str, episodes: int, clips_per_episode: int,
           workflow_key: str = W.DEFAULT_WORKFLOW, project_id: int | None = None, name: str | None = None,
           steps: dict[str, bool] | None = None, platforms: list[str] | None = None) -> dict[str, Any]:
    est = estimate(ctx, idea=idea, genre=genre, episodes=episodes, clips_per_episode=clips_per_episode,
                   workflow_key=workflow_key, project_id=project_id, steps=steps)
    if project_id is None:
        title = (name or T.clip_text(idea.strip().splitlines()[0], 60)).strip() or "Dự án mới"
        project_id = projects.create(ctx, name=title, genre_key=genre,
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
