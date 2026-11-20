"""Các bước AI viết (spec §14.3–14.4, §15; phụ lục film-craft §2, §5; craft-and-prompts §1, §4, §6).

Mọi hàm là `async def f(ctx, ..., feedback="", on_delta=None, log=None) -> dict`: đọc DB trong thread, gọi LLM qua
`llmio.run` (template `story_*` sửa được, kiểm schema, thử lại một lần), ghi kết quả qua UnitOfWork với actor
`llm:<model>` (hoàn tác được). `feedback` = góp ý khi viết lại sau `gate.reject`.

Số liệu thể loại (độ dài tập, số clip, độ dài clip, hook 3 s, kiểu kết) lấy từ preset `genre` của dự án và được đưa vào
prompt + dùng để kiểm/kẹp kết quả (cảnh báo, không chặn).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sagareel.compile import compiler
from sagareel.core import rank
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound, ValidationFailed
from sagareel.db.models import (
    Asset,
    AssetVariant,
    BibleItem,
    Clip,
    DialogueLine,
    GroupNode,
    Project,
    Scene,
    Sequence,
)
from sagareel.services import clips as clip_svc
from sagareel.services import entities, presets
from sagareel.services import sequences as seq_svc
from sagareel.services.story import bible, llmio, memory, state
from sagareel.services.story import schemas as S
from sagareel.services.story import text as T

Delta = Callable[[str], None] | None
Log = Callable[[str, str], None] | None

STILL_GENRES = frozenset({"storyboard", "narration"})
PROMPT_BATCH = 20
NARRATOR = frozenset({"narrator", "nguoi_ke", "nguoi ke", "narration", ""})
TEMPLATE_RESERVE_CHARS = 6000  # chỗ cho thân template + biến nhỏ khi chia ngân sách ngữ cảnh

COVERAGE_RULES = """\
Mẫu phủ cảnh cho một cảnh drama dọc (mỗi cảnh 3–8 shot):
1. establishing (ws/ews, 2–3 s): ở đâu, giờ nào — TRỪ cảnh đầu tập: clip đầu tiên phải là HOOK (cu/mcu/reaction hoặc
   hành động đang diễn ra), hình hook xuất hiện trong 1,5 s đầu.
2. master (ms, 3–5 s): các nhân vật cùng khung, ai ở đâu.
3. shot / reverse (mcu hoặc ots, 2–4 s mỗi cú) xen kẽ theo lời thoại; giữ trục 180° (cặp thoại cùng `axis_side`,
   A nhìn screen_right thì B nhìn screen_left).
4. insert (ecu/insert, 1–2 s) cho vật có ý nghĩa (điện thoại, nhẫn, giấy).
5. reaction (cu, 1–2 s) ngay sau câu thoại gây sốc.
6. button (cu/ecu hoặc push_in): chốt cảm xúc cuối cảnh. Clip cuối tập là điểm treo (beat "cliffhanger") nếu thể loại
   kết bằng cliffhanger, hoặc khớp khung đầu nếu kết vòng lặp.
Tránh hai clip liền cùng cỡ + cùng góc cùng nhân vật; đổi ít nhất một bậc cỡ. Một clip = một chuyển động máy; thoại
dọc ưu tiên static/push_in chậm. ≤ 3 nhân vật mỗi cảnh. `link: continue` tối đa 3 clip liền.

Giá trị hợp lệ (để trống nếu không chắc):
- beat: hook, establishing, master, shot, reverse, insert, reaction, button, action, cliffhanger
- shot_size: ews, ws, fs, mws, ms, mcu, cu, ecu, insert
- angle: eye, low, high, dutch, ots, pov, overhead
- camera_move: static, push_in, pull_out, pan_l, pan_r, tilt_u, tilt_d, truck_l, truck_r, track, arc, crane_up,
  crane_down, handheld, whip_pan, rack_focus
- lens: 24mm, 35mm, 50mm, 85mm, anamorphic
- composition: center, thirds_l, thirds_r, symmetry, leading_lines, frame_in_frame, negative_space, headroom_tight
- eyeline: screen_left, screen_right, to_camera, down, up
- screen_dir: L→R, R→L, toward_cam, away_cam, none
- axis_side: A, B
- cut_in / cut_out: cut, match_action, match_graphic, j_cut, l_cut, smash, cutaway, dissolve
- link: cut, fade, continue"""

BEATS = frozenset({"hook", "establishing", "master", "shot", "reverse", "insert", "reaction", "button", "action",
                   "cliffhanger"})
HOOK_BEATS = frozenset({"hook", "reaction", "action", "insert", "cliffhanger"})
END_BEATS = frozenset({"cliffhanger", "button"})
_EMPTY_TOKENS = frozenset({"none", "null", "n_a", "na", "nil", "khong", "-"})
_MODIFIERS = ("slow", "slowly", "fast", "quick", "gentle", "subtle", "smooth", "very", "slight", "soft")


def enums_text() -> str:
    """Danh sách giá trị hợp lệ cho prompt LLM (cùng nguồn với validator)."""
    out = []
    for field, values in {**clip_svc.SHOT_ENUMS, **clip_svc.CUSTOM_ENUMS}.items():
        out.append(f"- {field}: {', '.join(values)}")
    return "\n".join(out)


ALIASES: dict[str, dict[str, str]] = {
    "shot_size": {"extreme_wide": "ews", "extreme_wide_shot": "ews", "wide": "ws", "wide_shot": "ws",
                  "establishing": "ws", "full": "fs", "full_shot": "fs", "medium_wide": "mws", "medium": "ms",
                  "medium_shot": "ms", "medium_close_up": "mcu", "medium_closeup": "mcu", "close_up": "cu",
                  "closeup": "cu", "extreme_close_up": "ecu", "extreme_closeup": "ecu"},
    "angle": {"eye_level": "eye", "low_angle": "low", "high_angle": "high", "dutch_angle": "dutch",
              "over_the_shoulder": "ots", "birds_eye": "overhead", "top_down": "overhead", "point_of_view": "pov"},
    "camera_move": {"push": "push_in", "dolly_in": "push_in", "zoom_in": "push_in", "pull": "pull_out",
                    "dolly_out": "pull_out", "zoom_out": "pull_out", "pan_left": "pan_l", "pan_right": "pan_r",
                    "tilt_up": "tilt_u", "tilt_down": "tilt_d", "truck_left": "truck_l", "truck_right": "truck_r",
                    "tracking": "track", "follow": "track", "none": "static", "still": "static"},
    "composition": {"rule_of_thirds_left": "thirds_l", "rule_of_thirds_right": "thirds_r", "centered": "center",
                    "symmetrical": "symmetry"},
    "eyeline": {"left": "screen_left", "right": "screen_right", "camera": "to_camera"},
}


# --- Tiện ích --------------------------------------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def src(by: str, name: str, model: str | None = None) -> dict[str, Any]:
    """Nguồn prompt (spec §7.4): `{by: user|llm|agent|import, name, model?, at}`."""
    out: dict[str, Any] = {"by": by, "name": name, "at": now_iso()}
    if model:
        out["model"] = model
    return out


def llm_ctx(ctx: OpContext, model: str) -> OpContext:
    return dataclasses.replace(ctx, actor=f"llm:{model or 'unknown'}")


def get_project(s: Session, project_id: int) -> Project:
    return entities.get(s, "project", project_id)


def get_sequence(s: Session, sequence_id: int) -> Sequence:
    return entities.get(s, "sequence", sequence_id)


def genre_of(s: Session, project: Project, key: str | None = None) -> tuple[str, dict[str, Any]]:
    key = key or project.genre_key or "series"
    try:
        return key, presets.get_data(s, "genre", key, project.id)
    except NotFound:
        d = presets.builtin_definition("genre", key)
        return key, dict(d["data"]) if d else {}


def _span(v: Any, default: tuple[float, float]) -> tuple[float, float]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return float(v[0]), float(v[1])
        except (TypeError, ValueError):
            pass
    return default


def genre_block(key: str, g: dict[str, Any]) -> str:
    """Số liệu thể loại dạng chữ cho prompt."""
    ep = _span(g.get("episode_s"), (60, 90))
    cl = _span(g.get("clips"), (12, 18))
    cs = _span(g.get("clip_s"), (6, 10))
    lines = [f"- Thể loại: {key}" + (f" — {g['label']}" if g.get("label") else "")]
    if g.get("description"):
        lines.append(f"- Mô tả: {g['description']}")
    lines += [
        f"- Độ dài mỗi tập: {ep[0]:g}–{ep[1]:g} giây",
        f"- Số clip mỗi tập: {cl[0]:g}–{cl[1]:g}; độ dài mỗi clip: {cs[0]:g}–{cs[1]:g} giây",
    ]
    if g.get("avg_shot_s"):
        a = _span(g["avg_shot_s"], (3, 4))
        lines.append(f"- Nhịp cắt trung bình: {a[0]:g}–{a[1]:g} giây/cú")
    hook = g.get("hook") or {}
    if hook:
        lines.append(f"- Hook bắt buộc trong {hook.get('window_s', 3):g} giây đầu, hình hook trong "
                     f"{hook.get('visual_in_s', 1.5):g} giây đầu")
    end = g.get("end")
    end_text = {"cliffhanger": "điểm treo (cliffhanger) — cắt trước khi giải quyết, kéo sang tập sau",
                "loop": "vòng lặp — khung cuối khớp khung đầu", "loop_or_cta": "vòng lặp hoặc lời kêu gọi",
                "cta": "lời kêu gọi hành động", "resolve": "giải quyết trọn vẹn"}.get(str(end), "")
    if end_text:
        lines.append(f"- Kết tập: {end_text}")
    style = {"narration": "thuyết minh (người kể dẫn dắt)", "dialogue": "đối thoại", "mixed": "đối thoại + lời dẫn",
             "none": "không/ít lời"}.get(str(g.get("dialogue_style")), "")
    if style:
        lines.append(f"- Kiểu lời: {style}")
    if g.get("max_chars_per_scene"):
        lines.append(f"- Tối đa {g['max_chars_per_scene']} nhân vật mỗi cảnh")
    if key in STILL_GENRES:
        lines.append("- Clip là ảnh tĩnh có chuyển động nhẹ (still_motion); mỗi ý/câu một hình")
    return "\n".join(lines)


def setting(s: Session, key: str, *scope: Any, run: dict[str, Any] | None = None) -> Any:
    if run and key in run:
        return run[key]
    return compiler.setting(s, key, *scope)


def budget_chars(s: Session, project: Project, seq: Sequence | None, run: dict[str, Any] | None = None) -> int:
    tokens = int(setting(s, "story.context_budget_tokens", project, seq, run=run) or 12000)
    return max(2000, tokens * T.CHARS_PER_TOKEN)


@dataclasses.dataclass
class Cast:
    text: str
    assets: dict[str, Asset]  # key → asset
    looks: dict[tuple[int, str], AssetVariant]
    characters: dict[str, Asset]

    def ids_for(self, keys: list[str]) -> list[int]:
        return [self.assets[k].id for k in keys if k in self.assets]


def load_cast(s: Session, project: Project, number: int | None = None) -> Cast:
    rows = s.scalars(select(Asset).where(Asset.project_id == project.id, Asset.deleted_at.is_(None))
                     .order_by(Asset.kind, Asset.id)).all()
    looks: dict[tuple[int, str], AssetVariant] = {}
    lines = []
    for a in rows:
        vs = s.scalars(select(AssetVariant).where(AssetVariant.asset_id == a.id, AssetVariant.deleted_at.is_(None))
                       .order_by(AssetVariant.id)).all()
        names = []
        for v in vs:
            if number is not None and ((v.valid_from and number < v.valid_from) or (v.valid_to and number > v.valid_to)):
                continue
            looks[(a.id, v.key)] = v
            rng = f" (tập {v.valid_from or '…'}–{v.valid_to or '…'})" if (v.valid_from or v.valid_to) else ""
            names.append(f"`{v.key}`{rng}")
        desc = T.clip_text(a.description or "", 160)
        look_txt = f" | looks: {', '.join(names)}" if names else ""
        lines.append(f"- {a.kind} `{a.key}` — {a.name}: {desc}{look_txt}")
    return Cast(text="\n".join(lines) or "(Library chưa có tài sản)", assets={a.key: a for a in rows}, looks=looks,
                characters={a.key: a for a in rows if a.kind == "character"})


async def _thread(fn, *args, **kw):
    return await anyio.to_thread.run_sync(lambda: fn(*args, **kw))


def _warn(warnings: list[dict], code: str, message: str, **extra: Any) -> None:
    warnings.append({"code": code, "message": message, **extra})


# --- write_bible -----------------------------------------------------------------------------------------------------


def _upsert_owned(s: Session, project_id: int, type_: str, key: str, summary: str, attrs: dict[str, Any], *,
                  locked: bool, asset_id: int | None = None, status: str = "confirmed") -> str:
    """Mục do `write_bible` sở hữu (`attributes.origin == write_bible`): viết lại được khi chạy lại; mục người dùng tự
    tạo cùng mã thì giữ nguyên."""
    key = T.slug_key(key, default=type_)
    item = bible.find(s, project_id, type_, key)
    attrs = {**attrs, "origin": "write_bible"}
    if item is None:
        item = BibleItem(project_id=project_id, type=type_, key=key, summary=summary, attributes=attrs,
                         status=status, locked=locked, asset_id=asset_id)
        s.add(item)
        s.flush()
        bible.reindex(s, item)
        return "created"
    if item.deleted_at is not None or (item.attributes or {}).get("origin") != "write_bible":
        return "skipped"
    item.summary, item.attributes, item.locked = summary, attrs, locked
    if asset_id is not None:
        item.asset_id = asset_id
    bible.reindex(s, item)
    return "updated"


def _upsert_asset(s: Session, project_id: int, kind: str, key: str, name: str, description: str,
                  custom: dict[str, Any]) -> tuple[Asset | None, str]:
    key = T.slug_key(key, default=kind)
    a = s.scalars(select(Asset).where(Asset.project_id == project_id, Asset.key == key)).first()
    if a is None:
        a = Asset(project_id=project_id, kind=kind, key=key, name=name or key, description=description or "",
                  custom=dict(custom))
        s.add(a)
        s.flush()
        return a, "created"
    if a.deleted_at is not None:
        return None, "skipped"
    if not a.locked and not (a.description or "").strip() and description:
        a.description = description
    a.custom = {**(a.custom or {}), **{k: v for k, v in custom.items() if k not in (a.custom or {})}}
    return a, "kept"


async def write_bible(ctx: OpContext, project_id: int, idea: str, *, genre: str | None = None, feedback: str = "",
                      on_delta: Delta = None, log: Log = None) -> dict[str, Any]:
    if not (idea or "").strip():
        raise ValidationFailed("Cần ý tưởng để viết Story Bible.")
    presets.ensure_builtins(ctx.services)

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            p = get_project(s, project_id)
            key, g = genre_of(s, p, genre)
            if genre and not g:
                raise ValidationFailed(f"Không có preset thể loại '{genre}'.")
            return {"genre": genre_block(key, g)}

    v = await _thread(load)
    out = await llmio.run(ctx.services, "story_write_bible", {"idea": idea, "genre": v["genre"],
                                                              "feedback": feedback}, S.BibleOut,
                          on_delta=on_delta, log=log)
    b: S.BibleOut = out.data
    counts: dict[str, int] = {}

    def bump(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    def save() -> dict[str, Any]:
        u = entities.uow(llm_ctx(ctx, out.model), "AI viết Story Bible", "story.write_bible")
        with u as s:
            p = get_project(s, project_id)
            u.project_id = p.id
            if genre:
                p.genre_key = genre
            if not (p.style_block or "").strip() and b.style:
                p.style_block = b.style
            if not (p.description or "").strip():
                p.description = b.logline
            for c in b.characters:
                a, act = _upsert_asset(s, p.id, "character", c.key, c.name, c.description,
                                       {"role": c.role, "traits": c.traits, "voice": c.voice, "lock_word": c.lock_word})
                bump(f"asset_{act}")
                attrs = {"name": c.name, "role": c.role, "traits": c.traits, "voice": c.voice,
                         "lock_word": c.lock_word, "description": c.description}
                summary = f"{c.name} — {c.role}. {c.description}".strip()
                bump("item_" + _upsert_owned(s, p.id, "character", c.key, summary, attrs, locked=True,
                                             asset_id=a.id if a else None))
            for kind, rows in (("location", b.locations), ("prop", b.props)):
                for r in rows:
                    a, act = _upsert_asset(s, p.id, kind, r.key, r.name, r.description, {})
                    bump(f"asset_{act}")
                    if kind == "location":
                        bump("item_" + _upsert_owned(s, p.id, "location", r.key, f"{r.name}: {r.description}",
                                                     {"name": r.name}, locked=False, asset_id=a.id if a else None))
            for r in b.relationships:
                bump("item_" + _upsert_owned(s, p.id, "relationship", f"{r.a}__{r.b}", r.summary,
                                             {"a": r.a, "b": r.b}, locked=False))
            premise = " ".join(x for x in (b.logline, b.world, b.tone and f"Giọng: {b.tone}") if x)
            bump("item_" + _upsert_owned(s, p.id, "storyline", "premise", premise, {"title": b.title}, locked=True))
            for i, rule in enumerate(b.rules, 1):
                bump("item_" + _upsert_owned(s, p.id, "fact", f"rule_{i}", rule, {"rule": True}, locked=True))
            for q in b.open_questions:
                bump("item_" + _upsert_owned(s, p.id, "open_question", T.slug_key(q, default="q")[:40], q, {},
                                             locked=False))
        return {"project_id": project_id, "model": out.model, "counts": counts, "bible": b.model_dump()}

    return await _thread(save)


# --- outline ---------------------------------------------------------------------------------------------------------


async def outline(ctx: OpContext, project_id: int, *, episodes: int | None = None, sequence_ids: list[int] | None = None,
                  arc: str = "", group_id: int | None = None, feedback: str = "", run: dict[str, Any] | None = None,
                  genre_overrides: dict[str, Any] | None = None, on_delta: Delta = None,
                  log: Log = None) -> dict[str, Any]:
    """Dàn ý một lô tập: `sequence_ids` (điền synopsis các tập có sẵn) hoặc `episodes` (tạo N tập mới nối tiếp)."""
    if not sequence_ids and not episodes:
        raise ValidationFailed("Cần episodes (số tập mới) hoặc sequence_ids.")
    presets.ensure_builtins(ctx.services)

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            p = get_project(s, project_id)
            if group_id is not None:
                g = entities.get(s, "group_node", group_id)
                if g.project_id != project_id:
                    raise ValidationFailed(f"Nhóm #{group_id} không thuộc dự án.")
            if sequence_ids:
                seqs = [get_sequence(s, i) for i in sequence_ids]
                if any(q.project_id != project_id for q in seqs):
                    raise ValidationFailed("Có tập không thuộc dự án.")
                numbers = [q.number for q in seqs]
            else:
                top = int(s.scalar(select(func.coalesce(func.max(Sequence.number), 0)).where(
                    Sequence.project_id == project_id, Sequence.deleted_at.is_(None))) or 0)
                numbers = list(range(top + 1, top + 1 + int(episodes or 0)))
            key, g = genre_of(s, p)
            g = {**g, **(genre_overrides or {})}
            query = " ".join([arc] + [q.title + " " + q.synopsis for q in (seqs if sequence_ids else [])])
            gb = genre_block(key, g)
            budget = budget_chars(s, p, None, run) - len(gb) - len(arc) - len(feedback) - TEMPLATE_RESERVE_CHARS
            packed = memory.build(s, p, min(numbers), query, budget)
            return {"numbers": numbers, "genre": gb, "context": packed.text or "(chưa có)",
                    "ids": list(sequence_ids or [])}

    v = await _thread(load)
    out = await llmio.run(ctx.services, "story_outline",
                          {"genre": v["genre"], "context": v["context"], "arc": arc,
                           "episodes": ", ".join(str(n) for n in v["numbers"]), "feedback": feedback},
                          S.OutlineOut, on_delta=on_delta, log=log)
    o: S.OutlineOut = out.data
    by_num = {e.number: e for e in o.episodes}
    eps = [by_num.get(n) for n in v["numbers"]]
    leftovers = [e for e in o.episodes if e.number not in set(v["numbers"])]
    warnings: list[dict] = []
    for i, e in enumerate(eps):
        if e is None:
            eps[i] = leftovers.pop(0) if leftovers else None
    if any(e is None for e in eps):
        _warn(warnings, "outline_short", f"LLM chỉ trả {len(o.episodes)}/{len(v['numbers'])} tập.")
    lctx = llm_ctx(ctx, out.model)

    def save() -> dict[str, Any]:
        ids: list[int] = []
        for n, e, sid in zip(v["numbers"], eps, v["ids"] or [None] * len(v["numbers"]), strict=True):
            if e is None:
                continue
            if sid is None:
                q = seq_svc.create(lctx, project_id, e.title, number=n, group_id=group_id, synopsis=e.synopsis)
                sid = q["id"]
            else:
                values: dict[str, Any] = {"synopsis": e.synopsis}
                if group_id is not None:
                    values["group_id"] = group_id
                with ctx.services.db.session() as s:
                    cur = get_sequence(s, sid)
                    if not cur.title.strip() or re.fullmatch(r"(Tập|Tap|Ep|Episode)\s*\d+", cur.title.strip(), re.IGNORECASE):
                        values["title"] = e.title
                seq_svc.update(lctx, sid, values)
            state.merge(ctx.services.db, sid, "memory", {"outline": {
                "hook": e.hook, "cliffhanger": e.cliffhanger, "characters": e.characters, "keywords": e.keywords,
                "model": out.model, "at": now_iso()}})
            ids.append(sid)
        if group_id is not None and o.arc_summary:
            u = entities.uow(lctx, "Cập nhật tóm tắt arc", "story.outline")
            with u as s:
                g = s.get(GroupNode, group_id)
                u.project_id = g.project_id
                if not (g.synopsis or "").strip() or feedback:
                    g.synopsis = o.arc_summary
        return {"project_id": project_id, "sequence_ids": ids, "arc_summary": o.arc_summary, "model": out.model,
                "warnings": warnings}

    return await _thread(save)


# --- write_script ----------------------------------------------------------------------------------------------------


def episode_text(seq: Sequence) -> str:
    o = state.of(seq, "memory").get("outline") or {}
    parts = [f"Tập {seq.number}: {seq.title}", f"Dàn ý: {seq.synopsis or '(chưa có)'}"]
    if o.get("hook"):
        parts.append(f"Hook mở đầu: {o['hook']}")
    if o.get("cliffhanger"):
        parts.append(f"Điểm treo cuối tập: {o['cliffhanger']}")
    if o.get("characters"):
        parts.append("Nhân vật: " + ", ".join(map(str, o["characters"])))
    return "\n".join(parts)


def query_text(seq: Sequence) -> str:
    o = state.of(seq, "memory").get("outline") or {}
    return " ".join([seq.title, seq.synopsis or "", " ".join(map(str, o.get("characters") or [])),
                     " ".join(map(str, o.get("keywords") or [])), str(o.get("hook") or ""),
                     str(o.get("cliffhanger") or "")])


def script_variables(s: Session, sequence_id: int, feedback: str = "", run: dict[str, Any] | None = None,
                     genre_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Biến cho `story_write_script` (dùng chung cho LLM và `context.pack`): ngữ cảnh được cắt theo ngân sách."""
    seq = get_sequence(s, sequence_id)
    p = get_project(s, seq.project_id)
    key, g = genre_of(s, p)
    g = {**g, **(genre_overrides or {})}
    cast = load_cast(s, p, seq.number)
    source = state.of(seq, "source").get("text") or ""
    base = {"genre": genre_block(key, g), "episode": episode_text(seq), "source": source, "cast": cast.text,
            "feedback": feedback}
    used = sum(len(str(x)) for x in base.values())
    budget = budget_chars(s, p, seq, run) - used - TEMPLATE_RESERVE_CHARS
    packed = memory.build(s, p, seq.number, query_text(seq), budget, exclude_sequence_id=seq.id)
    return {**base, "context": packed.text or "(đây là tập đầu, chưa có bộ nhớ)", "_packed": packed.to_dict()}


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


async def write_script(ctx: OpContext, sequence_id: int, *, feedback: str = "", run: dict[str, Any] | None = None,
                       genre_overrides: dict[str, Any] | None = None, on_delta: Delta = None,
                       log: Log = None) -> dict[str, Any]:
    presets.ensure_builtins(ctx.services)

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            return script_variables(s, sequence_id, feedback, run, genre_overrides)

    v = await _thread(load)
    packed = v.pop("_packed")
    out = await llmio.run(ctx.services, "story_write_script", v, None, min_chars=40, on_delta=on_delta, log=log)
    script = out.data
    res = await _thread(seq_svc.update, llm_ctx(ctx, out.model), sequence_id, {"script": script})
    return {"sequence_id": sequence_id, "chars": len(script), "model": out.model, "context_tokens": packed["tokens"],
            "context_sections": packed["sections"], "updated_at": _iso(res.get("updated_at"))}


# --- split_clips -----------------------------------------------------------------------------------------------------


def _norm_token(v: str) -> str:
    return re.sub(r"[\s\-]+", "_", T.fold(v)).strip("_")


def coerce_enum(field: str, value: str, warnings: list[dict], clip_no: int) -> str:
    """Giá trị shot của LLM → enum chuẩn; bí danh được dịch, lạ → '' + cảnh báo."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if field == "screen_dir":
        v = clip_svc.SCREEN_DIR_ALIASES.get(raw, raw)
        allowed = clip_svc.SHOT_ENUMS["screen_dir"]
        if v in allowed:
            return v
        low = _norm_token(raw)
        for cand in allowed:
            if _norm_token(cand) == low:
                return cand
    else:
        allowed = clip_svc.SHOT_ENUMS.get(field) or clip_svc.CUSTOM_ENUMS.get(field) or clip_svc.FIELD_ENUMS.get(field)
        if allowed is None:
            return raw
        if raw in allowed:
            return raw
        tok = _norm_token(raw)
        if field == "axis_side":
            tok = tok.upper()
        if tok in allowed:
            return tok
        alias = ALIASES.get(field, {}).get(tok)
        if alias in allowed:
            return alias
        if tok in _EMPTY_TOKENS:
            return ""
        # "slow_push_in", "gentle_pan_left" → bỏ trạng từ tốc độ/cường độ rồi thử lại
        stripped = re.sub(rf"^(?:{'|'.join(_MODIFIERS)})_", "", tok)
        if stripped != tok:
            if stripped in allowed:
                return stripped
            alias = ALIASES.get(field, {}).get(stripped)
            if alias in allowed:
                return alias
    _warn(warnings, "enum_unknown", f"Clip {clip_no}: giá trị '{raw}' của {field} không hợp lệ; để trống.",
          field=field, clip=clip_no)
    return ""


def _resolve_look(cast: Cast, asset: Asset, look_key: str) -> int | None:
    v = cast.looks.get((asset.id, T.slug_key(look_key))) or cast.looks.get((asset.id, look_key))
    return v.id if v is not None else None


def plan_clips(split: S.SplitOut, cast: Cast, genre_key: str, g: dict[str, Any]) -> tuple[list[dict], list[dict],
                                                                                          list[dict]]:
    """SplitOut → (cảnh, clip [trường + thoại], cảnh báo). Kẹp độ dài theo thể loại, kiểm hook/kết/số clip."""
    warnings: list[dict] = []
    lo_s, hi_s = _span(g.get("clip_s"), (6, 10))
    engine = "still_motion" if genre_key in STILL_GENRES else "ai_video"
    scenes = []
    scene_keys = set()
    for sc in split.scenes:
        k = T.slug_key(sc.key, default="s")
        if k in scene_keys:
            continue
        scene_keys.add(k)
        loc = cast.assets.get(T.slug_key(sc.location)) if sc.location else None
        scenes.append({"key": k, "title": sc.title, "location_asset_id": loc.id if loc else None,
                       "state": {x: getattr(sc, x) for x in ("time_of_day", "weather") if getattr(sc, x)}})
    planned = []
    for i, c in enumerate(split.clips, 1):
        enums = {f: coerce_enum(f, getattr(c, f), warnings, i) for f in
                 ("shot_size", "angle", "camera_move", "lens", "composition", "eyeline", "screen_dir")}
        custom_enums = {f: coerce_enum(f, getattr(c, f), warnings, i) for f in ("axis_side", "cut_in", "cut_out")}
        link = c.link if c.link in clip_svc.FIELD_ENUMS["link"] else "cut"
        beat = _norm_token(c.beat)
        beat = beat if beat in BEATS else ""
        dur = float(c.duration_s)
        clamped = min(max(dur, lo_s), hi_s)
        if abs(clamped - dur) > 0.5:
            _warn(warnings, "duration_clamped", f"Clip {i}: {dur:g} s ngoài khoảng {lo_s:g}–{hi_s:g} s của thể loại; "
                  f"dùng {clamped:g} s (cắt bằng trim nếu cần).", clip=i)
        assets: list[dict[str, Any]] = []
        unknown = []
        for key in c.characters:
            a = cast.assets.get(T.slug_key(key)) or cast.assets.get(key)
            if a is None:
                unknown.append(key)
                continue
            item: dict[str, Any] = {"asset_id": a.id}
            look = c.looks.get(key) or c.looks.get(a.key)
            if look:
                vid = _resolve_look(cast, a, look)
                if vid is None:
                    _warn(warnings, "look_unknown", f"Clip {i}: look '{look}' của {a.key} không có (hoặc không hợp "
                          f"lệ ở tập này); để trình ghép tự chọn.", clip=i)
                else:
                    item["variant_id"] = vid
            assets.append(item)
        for key in ([c.location] if c.location else []) + list(c.props):
            a = cast.assets.get(T.slug_key(key)) or cast.assets.get(key)
            if a is None:
                unknown.append(key)
            elif all(x["asset_id"] != a.id for x in assets):
                assets.append({"asset_id": a.id})
        if unknown:
            _warn(warnings, "asset_unknown", f"Clip {i}: mã tài sản không có trong Library: {', '.join(unknown)}.",
                  clip=i, keys=unknown)
        if len(c.characters) > int(g.get("max_chars_per_scene") or 3):
            _warn(warnings, "too_many_characters", f"Clip {i}: {len(c.characters)} nhân vật (khuyên ≤ 3).", clip=i)
        lines = []
        chars = 0
        for ln in c.dialogue:
            spk = (ln.speaker or "").strip()
            sid = None
            if T.fold(spk) not in NARRATOR:
                a = cast.characters.get(T.slug_key(spk)) or cast.characters.get(spk)
                if a is None:
                    _warn(warnings, "speaker_unknown", f"Clip {i}: người nói '{spk}' không có trong Library; "
                          "coi là người kể.", clip=i)
                else:
                    sid = a.id
            lines.append({"speaker_asset_id": sid, "text": ln.text.strip(), "emotion": ln.emotion})
            chars += len(ln.text.strip())
        limit = max(0.0, (clamped - 2) * 4.5)
        if lines and chars > limit:
            _warn(warnings, "dialogue_too_long", f"Clip {i}: thoại {chars} ký tự > {limit:.0f} (≈ (giây − 2) × 4,5).",
                  clip=i)
        fields = {
            "title": T.clip_text(c.action, 80), "duration_ms": round(clamped * 1000), "engine": engine,
            "link": link, **enums, "first_frame_desc": c.first_frame_desc, "last_frame_desc": c.last_frame_desc,
            "sfx_desc": c.sfx_desc, "assets": assets,
            "custom": {**{k: v for k, v in custom_enums.items() if v}, "beat": beat, "action": c.action,
                       "story": {"characters": list(c.characters), "location": c.location, "props": list(c.props)}},
        }
        planned.append({"scene": T.slug_key(c.scene, default="s") if c.scene else "", "fields": fields,
                        "lines": lines})
    # nhịp: hook, kết, số clip
    n_lo, n_hi = _span(g.get("clips"), (1, 1000))
    if not n_lo <= len(planned) <= n_hi:
        _warn(warnings, "clip_count", f"{len(planned)} clip, ngoài khoảng {n_lo:g}–{n_hi:g} của thể loại.")
    first = planned[0]["fields"] if planned else None
    if first is not None and g.get("hook"):
        beat = first["custom"].get("beat")
        if first["shot_size"] in ("ews", "ws") or (beat and beat not in HOOK_BEATS):
            _warn(warnings, "hook_weak", "Clip đầu không phải hook (cảnh toàn/establishing); hook phải có hình trong "
                  f"{(g.get('hook') or {}).get('visual_in_s', 1.5):g} s đầu.")
    if planned and g.get("end") == "cliffhanger" and planned[-1]["fields"]["custom"].get("beat") not in END_BEATS:
        _warn(warnings, "no_cliffhanger", "Clip cuối không đánh dấu điểm treo (beat cliffhanger/button).")
    return scenes, planned, warnings


async def split_clips(ctx: OpContext, sequence_id: int, *, feedback: str = "", replace: bool = True,
                      run: dict[str, Any] | None = None, genre_overrides: dict[str, Any] | None = None,
                      on_delta: Delta = None, log: Log = None) -> dict[str, Any]:
    presets.ensure_builtins(ctx.services)

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            seq = get_sequence(s, sequence_id)
            if not (seq.script or "").strip():
                raise AppError("script_missing", "Tập chưa có kịch bản để chia clip.",
                               hint="Chạy story.write_script hoặc nhập kịch bản trước.", status=409)
            p = get_project(s, seq.project_id)
            key, g = genre_of(s, p)
            g = {**g, **(genre_overrides or {})}
            existing = s.scalar(select(func.count()).select_from(Clip).where(
                Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None))) or 0
            return {"genre_key": key, "g": g, "genre": genre_block(key, g), "cast": load_cast(s, p, seq.number),
                    "episode": f"{seq.number} — {seq.title}", "script": seq.script, "existing": int(existing)}

    v = await _thread(load)
    if v["existing"] and not replace:
        raise AppError("clips_exist", f"Tập đã có {v['existing']} clip.",
                       hint="Truyền replace=true để xoá (mềm) và chia lại.", status=409)
    out = await llmio.run(ctx.services, "story_split_clips",
                          {"genre": v["genre"], "rules": COVERAGE_RULES, "cast": v["cast"].text, "enums": enums_text(),
                           "episode": v["episode"], "script": v["script"], "feedback": feedback},
                          S.SplitOut, on_delta=on_delta, log=log)
    scenes, planned, warnings = plan_clips(out.data, v["cast"], v["genre_key"], v["g"])
    lctx = llm_ctx(ctx, out.model)

    def save() -> dict[str, Any]:
        removed = _clear_sequence(lctx, sequence_id) if v["existing"] else {"clips": 0, "scenes": 0}
        scene_ids: dict[str, int] = {}
        for sc in scenes:
            row = seq_svc.scene_create(lctx, sequence_id, sc["title"], location_asset_id=sc["location_asset_id"],
                                       state=sc["state"])
            scene_ids[sc["key"]] = row["id"]
        fields = []
        for pc in planned:
            f = dict(pc["fields"])
            if pc["scene"] in scene_ids:
                f["scene_id"] = scene_ids[pc["scene"]]
            fields.append(f)
        created = clip_svc.bulk_create(lctx, sequence_id, fields)
        n_lines = 0
        if any(pc["lines"] for pc in planned):
            u = entities.uow(lctx, "Thêm lời thoại từ chia clip", "story.split_clips")
            with u as s:
                u.project_id = get_sequence(s, sequence_id).project_id
                for row, pc in zip(created, planned, strict=True):
                    for rk, ln in zip(rank.n_after(None, len(pc["lines"])), pc["lines"], strict=True):
                        s.add(DialogueLine(clip_id=row["id"], rank=rk, speaker_asset_id=ln["speaker_asset_id"],
                                           text=ln["text"], emotion=ln["emotion"] or ""))
                        n_lines += 1
        return {"sequence_id": sequence_id, "clips": len(created), "scenes": len(scene_ids), "dialogue_lines": n_lines,
                "clip_ids": [r["id"] for r in created], "replaced": removed, "warnings": warnings,
                "model": out.model}

    return await _thread(save)


def _clear_sequence(ctx: OpContext, sequence_id: int) -> dict[str, int]:
    """Xoá (mềm, hoàn tác được) clip + cảnh hiện có trước khi chia lại."""
    with ctx.services.db.session() as s:
        clip_ids = list(s.scalars(select(Clip.id).where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None))))
        scene_ids = list(s.scalars(select(Scene.id).where(Scene.sequence_id == sequence_id,
                                                          Scene.deleted_at.is_(None))))
    for i in range(0, len(clip_ids), clip_svc.MAX_BULK):
        part = clip_ids[i:i + clip_svc.MAX_BULK]
        clip_svc.delete(ctx, clip_svc.delete_preview(ctx, part)["confirm_token"])
    if scene_ids:
        seq_svc.scene_delete(ctx, seq_svc.scene_delete_preview(ctx, scene_ids)["confirm_token"])
    return {"clips": len(clip_ids), "scenes": len(scene_ids)}


# --- image / motion prompts, dialogue -------------------------------------------------------------------------------


def _target_clips(s: Session, sequence_id: int | None, clip_ids: list[int] | None) -> list[Clip]:
    if clip_ids:
        rows = clip_svc.require_clip_ids(s, list(dict.fromkeys(clip_ids)))
        if len({c.sequence_id for c in rows}) > 1:
            raise ValidationFailed("Các clip phải thuộc cùng một tập.")
        return sorted(rows, key=lambda c: (c.rank, c.id))
    if sequence_id is None:
        raise ValidationFailed("Cần sequence_id hoặc clip_ids.")
    get_sequence(s, sequence_id)
    return clip_svc.ordered(s, sequence_id)


def _lines_of(s: Session, clip_ids: list[int]) -> dict[int, list[DialogueLine]]:
    out: dict[int, list[DialogueLine]] = {}
    if not clip_ids:
        return out
    for ln in s.scalars(select(DialogueLine).where(DialogueLine.clip_id.in_(clip_ids),
                                                   DialogueLine.deleted_at.is_(None))
                        .order_by(DialogueLine.clip_id, DialogueLine.rank)):
        out.setdefault(ln.clip_id, []).append(ln)
    return out


def _names(s: Session, ids: set[int]) -> dict[int, Asset]:
    if not ids:
        return {}
    return {a.id: a for a in s.scalars(select(Asset).where(Asset.id.in_(ids)))}


def clip_brief(c: Clip, assets: dict[int, Asset], lines: list[DialogueLine], scene: Scene | None,
               part: str) -> str:
    cu = c.custom or {}
    who = [assets[a["asset_id"]] for a in (c.assets or []) if isinstance(a, dict) and a.get("asset_id") in assets]
    chars = ", ".join(f"{a.name} (`{a.key}`)" for a in who if a.kind == "character")
    where = ", ".join(a.name for a in who if a.kind != "character")
    shot = ", ".join(x for x in (c.shot_size, c.angle, c.camera_move, c.lens, c.composition, c.eyeline) if x)
    head = f"[{c.key}] {c.duration_ms / 1000:g}s"
    if scene is not None:
        st = scene.state or {}
        head += f" | cảnh {scene.key}" + (f" ({st.get('time_of_day')})" if st.get("time_of_day") else "")
    parts = [head]
    if shot:
        parts.append(f"shot: {shot}")
    if chars:
        parts.append(f"nhân vật: {chars}")
    if where:
        parts.append(f"bối cảnh/đạo cụ: {where}")
    parts.append(f"hành động: {cu.get('action') or c.title}")
    if part == "image" and c.first_frame_desc:
        parts.append(f"khung đầu: {c.first_frame_desc}")
    if part == "motion":
        if c.last_frame_desc:
            parts.append(f"khung cuối: {c.last_frame_desc}")
        if c.sfx_desc:
            parts.append(f"âm thanh: {c.sfx_desc}")
    if part in ("motion", "dialogue") and lines:
        say = " / ".join(f"{assets[ln.speaker_asset_id].key if ln.speaker_asset_id in assets else 'narrator'}: "
                         f"{ln.text}" for ln in lines)
        parts.append(f"thoại: {say}")
    if part == "dialogue":
        parts.append(f"giới hạn: {max(0, int((c.duration_ms / 1000 - 2) * 4.5))} ký tự")
    return " | ".join(parts)


async def write_prompts(ctx: OpContext, part: str, *, sequence_id: int | None = None, clip_ids: list[int] | None = None,
                        feedback: str = "", overwrite: bool = False, on_delta: Delta = None,
                        log: Log = None) -> dict[str, Any]:
    """`part` = image|motion. Mặc định chỉ viết clip còn trống; `overwrite`/`feedback` → viết lại mọi clip trừ prompt
    người dùng tự viết (`*_src.by == user`)."""
    field = "image_prompt" if part == "image" else "motion_prompt"
    task = "story_image_prompts" if part == "image" else "story_motion_prompts"
    rewrite = overwrite or bool(feedback)

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            rows = _target_clips(s, sequence_id, clip_ids)
            if not rows:
                return {"briefs": [], "keys": {}}
            seq = get_sequence(s, rows[0].sequence_id)
            p = get_project(s, seq.project_id)
            targets = [c for c in rows if not getattr(c, field).strip()
                       or (rewrite and (getattr(c, f"{field}_src") or {}).get("by") != "user")]
            aids = {a["asset_id"] for c in targets for a in (c.assets or []) if isinstance(a, dict)
                    and isinstance(a.get("asset_id"), int)}
            assets = _names(s, aids)
            lines = _lines_of(s, [c.id for c in targets])
            scenes = {sc.id: sc for sc in s.scalars(select(Scene).where(Scene.sequence_id == seq.id))}
            briefs = [clip_brief(c, assets, lines.get(c.id, []), scenes.get(c.scene_id), part) for c in targets]
            cast = load_cast(s, p, seq.number).text
            return {"briefs": briefs, "keys": {c.key: c.id for c in targets}, "style": p.style_block or "(chưa có)",
                    "cast": cast, "sequence_id": seq.id, "skipped": len(rows) - len(targets)}

    v = await _thread(load)
    if not v["briefs"]:
        return {"sequence_id": sequence_id, "part": part, "written": 0, "skipped": v.get("skipped", 0),
                "missing": [], "model": None}
    written: dict[int, str] = {}
    model = ""
    for i in range(0, len(v["briefs"]), PROMPT_BATCH):
        chunk = v["briefs"][i:i + PROMPT_BATCH]
        variables = {"clips": "\n".join(chunk), "feedback": feedback}
        if part == "image":
            variables.update(style=v["style"], cast=v["cast"])
        out = await llmio.run(ctx.services, task, variables, S.PromptsOut, on_delta=on_delta, log=log)
        model = out.model
        for it in out.data.items:
            cid = v["keys"].get(it.clip.strip().strip("[]`"))
            if cid is not None:
                written[cid] = it.prompt.strip()
    lctx = llm_ctx(ctx, model)

    def save() -> None:
        stamp = src("llm", task, model)
        for cid, prompt in written.items():
            clip_svc.update(lctx, cid, {field: prompt, f"{field}_src": stamp})

    await _thread(save)
    missing = [k for k, cid in v["keys"].items() if cid not in written]
    return {"sequence_id": v["sequence_id"], "part": part, "written": len(written), "skipped": v["skipped"],
            "missing": missing, "model": model}


async def write_dialogue(ctx: OpContext, *, sequence_id: int | None = None, clip_ids: list[int] | None = None,
                         feedback: str = "", overwrite: bool = False, on_delta: Delta = None,
                         log: Log = None) -> dict[str, Any]:
    """Chỉnh thoại: mặc định chỉ các clip có thoại quá dài; `overwrite`/`feedback`/`clip_ids` → mọi clip đã chọn."""

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            rows = _target_clips(s, sequence_id, clip_ids)
            if not rows:
                return {"briefs": []}
            seq = get_sequence(s, rows[0].sequence_id)
            p = get_project(s, seq.project_id)
            lines = _lines_of(s, [c.id for c in rows])
            if overwrite or feedback or clip_ids:
                targets = rows
            else:
                targets = [c for c in rows if sum(len(ln.text) for ln in lines.get(c.id, []))
                           > max(0, (c.duration_ms / 1000 - 2) * 4.5)]
            aids = {a["asset_id"] for c in targets for a in (c.assets or []) if isinstance(a, dict)
                    and isinstance(a.get("asset_id"), int)}
            aids |= {ln.speaker_asset_id for c in targets for ln in lines.get(c.id, []) if ln.speaker_asset_id}
            assets = _names(s, aids)
            scenes = {sc.id: sc for sc in s.scalars(select(Scene).where(Scene.sequence_id == seq.id))}
            cast = load_cast(s, p, seq.number)
            speakers = "\n".join(f"- `{a.key}` — {a.name}" for a in cast.characters.values()) or "(chỉ narrator)"
            return {"briefs": [clip_brief(c, assets, lines.get(c.id, []), scenes.get(c.scene_id), "dialogue")
                               for c in targets],
                    "keys": {c.key: c.id for c in targets}, "chars": {k: a.id for k, a in cast.characters.items()},
                    "cast": speakers, "sequence_id": seq.id}

    v = await _thread(load)
    if not v["briefs"]:
        return {"sequence_id": sequence_id, "written": 0, "lines": 0, "warnings": [], "model": None}
    result: dict[int, list[dict]] = {}
    warnings: list[dict] = []
    model = ""
    for i in range(0, len(v["briefs"]), PROMPT_BATCH):
        out = await llmio.run(ctx.services, "story_dialogue",
                              {"cast": v["cast"], "clips": "\n".join(v["briefs"][i:i + PROMPT_BATCH]),
                               "feedback": feedback}, S.DialogueOut, on_delta=on_delta, log=log)
        model = out.model
        for it in out.data.items:
            cid = v["keys"].get(it.clip.strip().strip("[]`"))
            if cid is None:
                continue
            rows = []
            for ln in it.lines:
                sid = None
                if T.fold(ln.speaker) not in NARRATOR:
                    sid = v["chars"].get(T.slug_key(ln.speaker)) or v["chars"].get(ln.speaker)
                    if sid is None:
                        _warn(warnings, "speaker_unknown", f"{it.clip}: người nói '{ln.speaker}' không có; coi là "
                              "người kể.")
                rows.append({"speaker_asset_id": sid, "text": ln.text.strip(), "emotion": ln.emotion})
            result[cid] = rows

    def save() -> int:
        n = 0
        u = entities.uow(llm_ctx(ctx, model), "AI viết lời thoại", "story.dialogue")
        with u as s:
            u.project_id = get_project(s, get_sequence(s, v["sequence_id"]).project_id).id
            stamp = datetime.now(UTC)
            for cid, rows in result.items():
                for old in s.scalars(select(DialogueLine).where(DialogueLine.clip_id == cid,
                                                                DialogueLine.deleted_at.is_(None))):
                    old.deleted_at = stamp
                for rk, ln in zip(rank.n_after(None, len(rows)), rows, strict=True):
                    s.add(DialogueLine(clip_id=cid, rank=rk, speaker_asset_id=ln["speaker_asset_id"],
                                       text=ln["text"], emotion=ln["emotion"] or ""))
                    n += 1
        return n

    n = await _thread(save)
    return {"sequence_id": v["sequence_id"], "written": len(result), "lines": n, "warnings": warnings, "model": model}


# --- adapt_novel ------------------------------------------------------------------------------------------------------


def _read_text(services: Any, text_: str | None, upload_id: str | None) -> str:
    if text_ and text_.strip():
        return text_
    if not upload_id:
        raise ValidationFailed("Cần text hoặc upload_id.")
    from sagareel.services import uploads

    data, _name = uploads.read_bytes(services, upload_id)
    for enc in ("utf-8-sig", "utf-16", "cp1258"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValidationFailed("Không đọc được file truyện (cần UTF-8).")


def _boundary(text_: str, start: int, size: int) -> int:
    """Vị trí cắt đoạn ~`size` ký tự từ `start`, lùi về cuối đoạn văn/câu gần nhất."""
    end = min(len(text_), start + size)
    if end >= len(text_):
        return len(text_)
    window = text_[start:end]
    for pat in ("\n\n", "\n", ". ", "! ", "? ", "… "):
        k = window.rfind(pat)
        if k > size // 2:
            return start + k + len(pat)
    return end


async def adapt_novel(ctx: OpContext, project_id: int, *, text: str | None = None, upload_id: str | None = None,
                      target_episode_s: int = 75, max_episodes: int = 200, feedback: str = "",
                      on_delta: Delta = None, log: Log = None,
                      progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Tách truyện dài thành tập theo độ dài mục tiêu; mỗi tập kết ở câu treo LLM chọn (`cut_after`)."""
    presets.ensure_builtins(ctx.services)
    body = (await _thread(_read_text, ctx.services, text, upload_id)).replace("\r\n", "\n").strip()
    if not body:
        raise ValidationFailed("Truyện rỗng.")

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            p = get_project(s, project_id)
            key, g = genre_of(s, p)
            cps = int(setting(s, "story.novel_chars_per_second", p) or 40)
            top = int(s.scalar(select(func.coalesce(func.max(Sequence.number), 0)).where(
                Sequence.project_id == project_id, Sequence.deleted_at.is_(None))) or 0)
            known = load_cast(s, p).characters
            return {"genre": genre_block(key, g), "size": max(400, int(target_episode_s) * cps), "top": top,
                    "known": {k: a.name for k, a in known.items()}}

    v = await _thread(load)
    pos = 0
    number = v["top"]
    created: list[int] = []
    warnings: list[dict] = []
    known = dict(v["known"])
    total = len(body)
    while pos < total and len(created) < max_episodes:
        end = _boundary(body, pos, int(v["size"] * 1.25))
        passage = body[pos:end]
        number += 1
        if progress is not None:
            progress(100.0 * pos / total, f"Chuyển thể tập {number}")
        known_txt = "\n".join(f"- `{k}` — {n}" for k, n in known.items()) or "(chưa có)"
        out = await llmio.run(ctx.services, "story_adapt_novel",
                              {"genre": v["genre"], "number": number, "known": known_txt, "passage": passage,
                               "feedback": feedback}, S.AdaptOut, on_delta=on_delta, log=log)
        a: S.AdaptOut = out.data
        cut = end
        if a.cut_after.strip() and end < total:
            k = passage.find(a.cut_after.strip())
            if k >= len(passage) // 3:
                cut = pos + k + len(a.cut_after.strip())
            else:
                _warn(warnings, "cut_not_found", f"Tập {number}: không thấy câu treo trong nửa sau đoạn; cắt ở cuối "
                      "đoạn văn.", episode=number)
        ep_text = body[pos:cut].strip()
        lctx = llm_ctx(ctx, out.model)

        def save(a=a, ep_text=ep_text, number=number, pos=pos, cut=cut, lctx=lctx, model=out.model) -> int:
            q = seq_svc.create(lctx, project_id, a.title, number=number, synopsis=a.synopsis)
            state.merge(ctx.services.db, q["id"], "source", {"text": ep_text, "start": pos, "end": cut})
            state.merge(ctx.services.db, q["id"], "memory", {"outline": {
                "cliffhanger": a.cliffhanger, "characters": [c.key for c in a.characters], "model": model,
                "at": now_iso()}})
            u = entities.uow(lctx, f"Chuyển thể tập {number}: Story Bible", "story.adapt_novel")
            with u as s:
                u.project_id = project_id
                for c in a.characters:
                    asset, _ = _upsert_asset(s, project_id, "character", c.key, c.name, c.description,
                                             {"role": c.role, "traits": c.traits})
                    item, act = bible.upsert_proposal(
                        s, project_id, "character", c.key, f"{c.name} — {c.role}. {c.description}".strip(),
                        attributes={"name": c.name, "role": c.role, "traits": c.traits}, story_order=number,
                        asset_id=asset.id if asset else None, status="confirmed")
                    if act == "created":
                        item.locked = True  # nhãn nhân thiết khoá
                for f in a.facts:
                    type_ = f.type if f.type in bible.TYPES else "fact"
                    pos_q = body.find(f.quote, pos) if f.quote else -1
                    bible.upsert_proposal(
                        s, project_id, type_, f.key or f.summary, f.summary, story_order=number,
                        evidence=[{"quote": f.quote, "source": "novel", "sequence_id": q["id"],
                                   "start": pos_q if pos_q >= 0 else None,
                                   "end": pos_q + len(f.quote) if pos_q >= 0 else None}] if f.quote else [])
            return q["id"]

        created.append(await _thread(save))
        for c in a.characters:
            known.setdefault(T.slug_key(c.key), c.name)
        pos = cut
        while pos < total and body[pos] in " \n\t":
            pos += 1
    if pos < total:
        _warn(warnings, "truncated", f"Dừng ở {max_episodes} tập; còn {total - pos} ký tự chưa chuyển thể.")
    return {"project_id": project_id, "sequence_ids": created, "episodes": len(created), "chars": total,
            "warnings": warnings}


# --- memory_summarize ------------------------------------------------------------------------------------------------


async def memory_summarize(ctx: OpContext, sequence_id: int, *, feedback: str = "", on_delta: Delta = None,
                           log: Log = None) -> dict[str, Any]:
    """Tóm tắt tập + đề xuất mục Story Bible `pending` kèm bằng chứng (trích từ kịch bản)."""

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            seq = get_sequence(s, sequence_id)
            if not (seq.script or "").strip():
                raise AppError("script_missing", "Tập chưa có kịch bản để tóm tắt.", status=409,
                               hint="Viết kịch bản trước (story.write_script).")
            keys = s.scalars(select(BibleItem.key).where(BibleItem.project_id == seq.project_id,
                                                         BibleItem.deleted_at.is_(None),
                                                         BibleItem.status != "rejected")).all()
            oq = s.scalars(select(BibleItem).where(BibleItem.project_id == seq.project_id,
                                                   BibleItem.type == "open_question",
                                                   BibleItem.deleted_at.is_(None),
                                                   BibleItem.status != "rejected")).all()
            oq_txt = "\n".join(f"- `{q.key}`: {q.summary}" for q in oq if not (q.attributes or {}).get("resolved"))
            return {"project_id": seq.project_id, "number": seq.number, "group_id": seq.group_id,
                    "episode": f"{seq.number} — {seq.title}", "script": seq.script,
                    "known": ", ".join(sorted(set(keys))[:400]) or "(chưa có)", "open": oq_txt or "(không có)"}

    v = await _thread(load)
    out = await llmio.run(ctx.services, "story_memory_summarize",
                          {"episode": v["episode"], "script": v["script"], "known": v["known"],
                           "open_questions": v["open"], "feedback": feedback}, S.MemoryOut,
                          on_delta=on_delta, log=log)
    m: S.MemoryOut = out.data
    lctx = llm_ctx(ctx, out.model)

    def ev(quote: str) -> list[dict]:
        if not quote:
            return []
        k = v["script"].find(quote)
        return [{"quote": quote, "source": "script", "sequence_id": sequence_id, "start": k if k >= 0 else None,
                 "end": k + len(quote) if k >= 0 else None}]

    def save() -> dict[str, Any]:
        counts: dict[str, int] = {}
        state.merge(ctx.services.db, sequence_id, "memory", {"summary": m.summary, "model": out.model,
                                                            "summarized_at": now_iso()})
        u = entities.uow(lctx, f"Bộ nhớ truyện tập {v['number']}", "story.memory_summarize")
        with u as s:
            u.project_id = v["project_id"]
            for it in m.items:
                type_ = it.type if it.type in bible.TYPES else "fact"
                _, act = bible.upsert_proposal(s, v["project_id"], type_, it.key or it.summary, it.summary,
                                               attributes=it.attributes or None, story_order=v["number"],
                                               evidence=ev(it.quote))
                counts[act] = counts.get(act, 0) + 1
            for q in m.open_questions:
                _, act = bible.upsert_proposal(s, v["project_id"], "open_question", q.key or q.summary, q.summary,
                                               story_order=v["number"], evidence=ev(q.quote))
                counts[act] = counts.get(act, 0) + 1
            resolved = 0
            for key in m.resolved:
                item = bible.find(s, v["project_id"], "open_question", T.slug_key(key))
                if item is not None and item.deleted_at is None:
                    item.attributes = {**(item.attributes or {}), "resolved": True, "resolved_in": v["number"]}
                    bible.reindex(s, item)
                    resolved += 1
            if v["group_id"] is not None and m.arc_summary:
                g = s.get(GroupNode, v["group_id"])
                if g is not None:
                    g.synopsis = m.arc_summary
        return {"sequence_id": sequence_id, "summary": m.summary, "items": counts, "resolved": resolved,
                "model": out.model}

    return await _thread(save)


TASKS: dict[str, Any] = {
    "write_bible": write_bible,
    "outline": outline,
    "write_script": write_script,
    "split_clips": split_clips,
    "image_prompts": lambda ctx, **kw: write_prompts(ctx, "image", **kw),
    "motion_prompts": lambda ctx, **kw: write_prompts(ctx, "motion", **kw),
    "dialogue": write_dialogue,
    "adapt_novel": adapt_novel,
    "memory_summarize": memory_summarize,
}
