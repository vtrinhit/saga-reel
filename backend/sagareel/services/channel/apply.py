"""`channel.apply`: biến kết quả phân tích thành preset genre/style_pack (không builtin) + gợi ý Library (không tự tạo)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.db.models import ChannelStudy
from sagareel.services import entities, presets


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:30] or "kenh"


def _free_key(ctx: OpContext, kind: str, base: str, project_id: int | None) -> str:
    with ctx.services.db.session() as s:
        for i in range(1, 100):
            key = base if i == 1 else f"{base}-{i}"
            if not presets._key_taken(s, kind, key, project_id):
                return key
    raise AppError("preset_key_taken", "Không tìm được key preset trống.", status=409)


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
        "script_template": "write_script", "default_platforms": [],
        "source": {"channel_study_id": study_id, "hook_patterns": agg["hook"]["patterns"],
                   "beats": agg["structure"]["beats"]},
    }


def style_pack_data(agg: dict[str, Any], title: str, study_id: int) -> dict[str, Any]:
    st = agg["style"]
    block = agg["style_block"] or ", ".join(
        x for x in (st["rendering"], st["lighting"], st["lens"], *st["palette"], *st["look_words"]) if x)
    return {
        "label": f"{title} - phong cách", "description": f"Phong cách rút từ kênh mẫu '{title}'.",
        "style_block": block, "negative_block": agg["negative_block"], "palette": st["palette"],
        "look_words": st["look_words"], "lighting": st["lighting"], "lens": st["lens"],
        "rendering": st["rendering"], "subtitles": agg["subtitles"],
        "source": {"channel_study_id": study_id},
    }


def library_suggestions(agg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for kind, items in (("character", agg["characters"]), ("location", agg["locations"])):
        for it in items:
            out.append({"kind": kind, "name": it["name"], "description": it["description"],
                        "key": _slug(it["name"]).upper().replace("-", "_")[:12], "seen_in_videos": it["count"]})
    return out


def apply(ctx: OpContext, study_id: int, project_id: int | None, genre_preset: bool, style_pack: bool,
          library: bool) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        st = entities.get(s, "channel_study", study_id)
        if not isinstance(st, ChannelStudy) or st.status != "done" or not (st.result or {}).get("aggregate"):
            raise AppError("study_not_analyzed", f"Phân tích kênh #{study_id} chưa có kết quả.", status=409,
                           hint="Chạy channel.analyze và đợi job xong trước.")
        agg = st.result["aggregate"]
        title = st.title
        scope = project_id if project_id is not None else st.project_id
    out: dict[str, Any] = {"study_id": study_id, "genre_preset": None, "style_pack": None, "library_suggestions": []}
    base = _slug(title)
    if genre_preset:
        key = _free_key(ctx, "genre", f"{base}-{study_id}", scope)
        out["genre_preset"] = _brief(presets.create(ctx, "genre", key, f"{title} - thể loại",
                                                    genre_data(agg, title, study_id), scope))
    if style_pack:
        key = _free_key(ctx, "style_pack", f"{base}-{study_id}", scope)
        out["style_pack"] = _brief(presets.create(ctx, "style_pack", key, f"{title} - phong cách",
                                                  style_pack_data(agg, title, study_id), scope))
    if library:
        out["library_suggestions"] = library_suggestions(agg)
    return out


def _brief(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p.get(k) for k in ("id", "kind", "key", "name", "project_id", "builtin")}

