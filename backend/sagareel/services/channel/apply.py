"""`channel.apply`: ghi kết quả phân tích kênh mẫu vào MỘT dự án (số liệu thể loại → `project.genre`, phong cách →
`style_block`/`negative_block`) + gợi ý Library (không tự tạo). Không có dự án đích → tạo dự án mẫu mới (nhân bản prompt
từ dự án mẫu cài sẵn gần nhất) để nhân bản cho các dự án sau."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.db.models import ChannelStudy
from sagareel.services import entities


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:30] or "kenh"


def genre_data(agg: dict[str, Any], title: str, study_id: int) -> dict[str, Any]:
    g = agg["genre_suggestion"]
    pacing = agg["pacing"]
    return {
        "label": f"{title} - thể loại",
        "description": (f"Rút từ kênh mẫu '{title}': {pacing['avg_shot_s']} s/cảnh, {pacing['cut_rate_per_min']} cắt/phút."),
        "episode_s": g["episode_s"], "clip_s": g["clip_s"], "clips": g["clips"],
        "transition": g["transition"], "end": g["end"], "hook": g["hook"],
        "structure": {"groups": False, "scenes": False},
        "dialogue_style": g["dialogue_style"],
        "default_platforms": [],
        "source": {"channel_study_id": study_id, "hook_patterns": agg["hook"]["patterns"],
                   "beats": agg["structure"]["beats"]},
    }


def style_data(agg: dict[str, Any]) -> dict[str, str]:
    st = agg["style"]
    block = agg["style_block"] or ", ".join(
        x for x in (st["rendering"], st["lighting"], st["lens"], *st["palette"], *st["look_words"]) if x)
    return {"style_block": block, "negative_block": agg["negative_block"] or ""}


def library_suggestions(agg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for kind, items in (("character", agg["characters"]), ("location", agg["locations"])):
        for it in items:
            out.append({"kind": kind, "name": it["name"], "description": it["description"],
                        "key": _slug(it["name"]).upper().replace("-", "_")[:12], "seen_in_videos": it["count"]})
    return out


def apply(ctx: OpContext, study_id: int, project_id: int | None, genre: bool, style: bool,
          library: bool) -> dict[str, Any]:
    from sagareel.services import genres, projects, samples

    with ctx.services.db.session() as s:
        st = entities.get(s, "channel_study", study_id)
        if not isinstance(st, ChannelStudy) or st.status != "done" or not (st.result or {}).get("aggregate"):
            raise AppError("study_not_analyzed", f"Phân tích kênh #{study_id} chưa có kết quả.", status=409,
                           hint="Chạy channel.analyze và đợi job xong trước.")
        agg = st.result["aggregate"]
        title = st.title
        target = project_id if project_id is not None else st.project_id
        created = target is None
        if created:
            style_kind = agg["genre_suggestion"].get("dialogue_style")
            src = samples.find_sample(s, "narration" if style_kind == "narration" else "series")
            src_id = src.id if src is not None else None
    if created:
        p = projects.create(ctx, f"Mẫu · {title}"[:200], from_project_id=src_id, include={"library": False})
        projects.update(ctx, p["id"], {"is_sample": True,
                                       "description": f"Dự án mẫu rút từ kênh '{title}' (phân tích #{study_id})."})
        target = p["id"]
    values: dict[str, Any] = {}
    if genre:
        with ctx.services.db.session() as s:
            current = dict(entities.get(s, "project", target).genre or {})
        values["genre"] = genres.validate({**current, **genre_data(agg, title, study_id)})
    if style:
        values.update(style_data(agg))
    if values:
        projects.update(ctx, target, values)
    return {"study_id": study_id, "project_id": target, "created_project": created,
            "applied": sorted(values), "library_suggestions": library_suggestions(agg) if library else []}
