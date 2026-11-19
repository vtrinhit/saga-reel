"""Ba cách "viết" (spec §7.4): `author.<task>` ∈ manual|llm|agent theo tầng + `context.pack` + `prompts.submit`.

- `mode(...)`: giá trị hiệu lực default → global → project → sequence → run.
- `pack(...)`: mọi thứ một agent/người cần để tự viết (style, genre, bible liên quan trong ngân sách, tài sản + mô tả +
  look, clip trước/sau, thoại, giới hạn provider, template của bước) — app không gọi LLM nào.
- `submit(...)`: ghi hàng loạt prompt/thoại/kịch bản/dàn ý/clip (nguồn `agent` mặc định, ghi ở `*_src`), rồi đánh thức
  quy trình đang `waiting_input` ở các tập liên quan.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.compile import compiler
from sagareel.core import rank
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ValidationFailed
from sagareel.db.models import Asset, AssetVariant, Clip, DialogueLine, Scene
from sagareel.services import clips as clip_svc
from sagareel.services import entities, llm, presets
from sagareel.services import sequences as seq_svc
from sagareel.services.story import memory, state, writer
from sagareel.services.story import schemas as S
from sagareel.services.story import text as T

MODES = ("manual", "llm", "agent")
AUTHOR_TASKS = ("write_bible", "outline", "write_script", "split_clips", "image_prompts", "motion_prompts",
                "dialogue")
CLIP_FIELDS = ("image_prompt", "motion_prompt", "dialogue")
SEQUENCE_FIELDS = ("script", "synopsis", "title", "clips")
SOURCES = ("agent", "user", "import")
MAX_ITEMS = 1000
TASK_TEMPLATE = {t: f"story_{t}" for t in AUTHOR_TASKS}


def mode(s: Session, task: str, project: Any, seq: Any = None, run: dict[str, Any] | None = None) -> str:
    if task not in AUTHOR_TASKS:
        return "llm"
    value = writer.setting(s, f"author.{task}", project, seq, run=run)
    return value if value in MODES else "llm"


# --- context.pack -----------------------------------------------------------------------------------------------------


def _asset_dict(a: Asset, looks: list[AssetVariant]) -> dict[str, Any]:
    return {"id": a.id, "key": a.key, "kind": a.kind, "name": a.name, "description": a.description,
            "locked": a.locked, "custom": dict(a.custom or {}),
            "looks": [{"id": v.id, "key": v.key, "name": v.name, "description_delta": v.description_delta,
                       "lock_word": v.lock_word, "keep_features": list(v.keep_features or []),
                       "valid_from": v.valid_from, "valid_to": v.valid_to, "is_default": v.is_default}
                      for v in looks]}


def _clip_dict(c: Clip, lines: list[DialogueLine], keys: dict[int, str]) -> dict[str, Any]:
    d = {k: getattr(c, k) for k in (
        "id", "key", "title", "scene_id", "engine", "duration_ms", "link", "prompt_mode", "image_prompt",
        "image_prompt_src", "motion_prompt", "motion_prompt_src", "shot_size", "angle", "camera_move", "lens",
        "composition", "eyeline", "screen_dir", "first_frame_desc", "last_frame_desc", "sfx_desc", "assets",
        "custom", "review")}
    d["dialogue"] = [{"id": ln.id, "speaker": keys.get(ln.speaker_asset_id, "narrator") if ln.speaker_asset_id
                      else "narrator", "speaker_asset_id": ln.speaker_asset_id, "text": ln.text,
                      "emotion": ln.emotion} for ln in lines]
    d["dialogue_limit_chars"] = max(0, int((c.duration_ms / 1000 - 2) * 4.5))
    return d


def pack(ctx: OpContext, *, sequence_id: int | None = None, clip_ids: list[int] | None = None,
         task: str | None = None) -> dict[str, Any]:
    presets.ensure_builtins(ctx.services)
    if task is not None and task not in AUTHOR_TASKS:
        raise ValidationFailed(f"task phải là một trong: {', '.join(AUTHOR_TASKS)}.")
    with ctx.services.db.session() as s:
        if clip_ids:
            chosen = clip_svc.require_clip_ids(s, list(dict.fromkeys(clip_ids)))
            seq_ids = {c.sequence_id for c in chosen}
            if len(seq_ids) != 1:
                raise ValidationFailed("Các clip phải thuộc cùng một tập.")
            sequence_id = seq_ids.pop()
        if sequence_id is None:
            raise ValidationFailed("Cần sequence_id hoặc clip_ids.")
        seq = writer.get_sequence(s, sequence_id)
        p = writer.get_project(s, seq.project_id)
        gkey, g = writer.genre_of(s, p)
        all_clips = clip_svc.ordered(s, seq.id)
        if clip_ids:
            want = {c.id for c in chosen}
            idx = [i for i, c in enumerate(all_clips) if c.id in want]
            lo, hi = max(0, min(idx) - 1), min(len(all_clips), max(idx) + 2)
            window = all_clips[lo:hi]  # clip đã chọn + một clip trước/sau làm ngữ cảnh
        else:
            want = {c.id for c in all_clips}
            window = all_clips
        lines: dict[int, list[DialogueLine]] = {}
        for ln in s.scalars(select(DialogueLine).where(DialogueLine.clip_id.in_([c.id for c in window]),
                                                       DialogueLine.deleted_at.is_(None))
                            .order_by(DialogueLine.clip_id, DialogueLine.rank)):
            lines.setdefault(ln.clip_id, []).append(ln)
        assets = s.scalars(select(Asset).where(Asset.project_id == p.id, Asset.deleted_at.is_(None))
                           .order_by(Asset.kind, Asset.id)).all()
        looks: dict[int, list[AssetVariant]] = {}
        for v in s.scalars(select(AssetVariant).where(AssetVariant.asset_id.in_([a.id for a in assets]),
                                                      AssetVariant.deleted_at.is_(None))):
            looks.setdefault(v.asset_id, []).append(v)
        keys = {a.id: a.key for a in assets}
        scenes = s.scalars(select(Scene).where(Scene.sequence_id == seq.id, Scene.deleted_at.is_(None))
                           .order_by(Scene.rank)).all()
        budget = writer.budget_chars(s, p, seq) // 2
        packed = memory.build(s, p, seq.number, writer.query_text(seq), budget, exclude_sequence_id=seq.id)
        caps = {}
        for kind in ("image", "video"):
            try:
                c, warns = compiler.connection_caps(s, kind, None, p, seq)
                caps[kind] = {**c, "warnings": warns}
            except Exception as e:  # noqa: BLE001 — thiếu kết nối không được chặn việc lấy ngữ cảnh
                caps[kind] = {"error": str(e)}
        templates = {}
        for t in ([task] if task else AUTHOR_TASKS):
            try:
                templates[TASK_TEMPLATE[t]] = llm.get_template(s, TASK_TEMPLATE[t]).body
            except Exception:  # noqa: BLE001 — template chưa seed (DB test) → bản cài sẵn
                body = llm.builtin_template(TASK_TEMPLATE[t])
                if body:
                    templates[TASK_TEMPLATE[t]] = body
        run_state = state.of(seq, "run")
        out = {
            "project": {"id": p.id, "key": p.key, "name": p.name, "description": p.description,
                        "aspect": p.aspect, "language": p.language, "style_block": p.style_block,
                        "negative_block": p.negative_block, "genre": gkey},
            "genre": {"key": gkey, "data": g, "brief": writer.genre_block(gkey, g)},
            "sequence": {"id": seq.id, "number": seq.number, "key": seq.key, "title": seq.title,
                         "synopsis": seq.synopsis, "script": seq.script,
                         "outline": state.of(seq, "memory").get("outline") or {},
                         "source_text": state.of(seq, "source").get("text") or ""},
            "bible": packed.to_dict(),
            "assets": [_asset_dict(a, looks.get(a.id, [])) for a in assets],
            "scenes": [{"id": sc.id, "key": sc.key, "title": sc.title, "location_asset_id": sc.location_asset_id,
                        "summary": sc.summary, "state": dict(sc.state or {})} for sc in scenes],
            "clips": [{**_clip_dict(c, lines.get(c.id, []), keys), "context_only": c.id not in want}
                      for c in window],
            "provider_caps": caps,
            "coverage_rules": writer.COVERAGE_RULES,
            "templates": templates,
            "pipeline": {"status": run_state.get("status"), "waiting": run_state.get("waiting")},
            "author_modes": {t: mode(s, t, p, seq) for t in AUTHOR_TASKS},
            "submit_help": ("prompts.submit items: {clip_id, field: image_prompt|motion_prompt|dialogue, value} hoặc "
                            "{sequence_id, field: script|synopsis|title|clips, value}; dialogue = [{speaker: mã nhân "
                            "vật|narrator, text, emotion}]; clips = JSON như đầu ra story_split_clips."),
        }
        return out


# --- prompts.submit ---------------------------------------------------------------------------------------------------


def _speaker_ids(s: Session, project_id: int) -> dict[str, int]:
    return {a.key: a.id for a in s.scalars(select(Asset).where(Asset.project_id == project_id,
                                                               Asset.kind == "character",
                                                               Asset.deleted_at.is_(None)))}


def _dialogue_rows(value: Any, speakers: dict[str, int], warnings: list[dict], where: str) -> list[dict]:
    if not isinstance(value, list):
        raise ValidationFailed(f"{where}: dialogue phải là danh sách câu thoại.")
    rows = []
    for ln in value:
        if isinstance(ln, str):
            ln = {"text": ln}
        if not isinstance(ln, dict) or not str(ln.get("text") or "").strip():
            raise ValidationFailed(f"{where}: mỗi câu thoại cần `text`.")
        spk = ln.get("speaker") if ln.get("speaker") is not None else ln.get("speaker_asset_id")
        sid = None
        if isinstance(spk, int):
            sid = spk if spk in speakers.values() else None
            if sid is None:
                warnings.append({"code": "speaker_unknown", "message": f"{where}: người nói #{spk} không có."})
        elif spk and T.fold(spk) not in writer.NARRATOR:
            sid = speakers.get(str(spk)) or speakers.get(T.slug_key(spk))
            if sid is None:
                warnings.append({"code": "speaker_unknown", "message": f"{where}: người nói '{spk}' không có; coi là "
                                 "người kể."})
        rows.append({"speaker_asset_id": sid, "text": str(ln["text"]).strip(), "emotion": str(ln.get("emotion") or "")})
    return rows


def submit(ctx: OpContext, items: list[dict[str, Any]], *, source: str = "agent", name: str | None = None,
           model: str | None = None) -> dict[str, Any]:
    if not items:
        raise ValidationFailed("Cần ít nhất một mục.")
    if len(items) > MAX_ITEMS:
        raise ValidationFailed(f"Mỗi lần gửi tối đa {MAX_ITEMS} mục.")
    if source not in SOURCES:
        raise ValidationFailed(f"source phải là một trong: {', '.join(SOURCES)}.")
    who = name or ctx.actor
    actx = ctx if source == "user" else dataclasses.replace(ctx, actor=f"{source}:{who.split(':', 1)[-1]}")
    stamp = writer.src(source, who, model)
    warnings: list[dict] = []
    touched: set[int] = set()
    results = []
    # kiểm toàn bộ trước khi ghi
    with ctx.services.db.session() as s:
        plan = []
        for i, it in enumerate(items):
            field = it.get("field")
            where = f"items[{i}]"
            if it.get("clip_id") is not None:
                if field not in CLIP_FIELDS:
                    raise ValidationFailed(f"{where}: field của clip phải là {', '.join(CLIP_FIELDS)}.")
                c = clip_svc.get_clip(s, int(it["clip_id"]))
                seq = writer.get_sequence(s, c.sequence_id)
                plan.append(("clip", c.id, seq.id, seq.project_id, field, it.get("value")))
            elif it.get("sequence_id") is not None:
                if field not in SEQUENCE_FIELDS:
                    raise ValidationFailed(f"{where}: field của tập phải là {', '.join(SEQUENCE_FIELDS)}.")
                seq = writer.get_sequence(s, int(it["sequence_id"]))
                plan.append(("sequence", seq.id, seq.id, seq.project_id, field, it.get("value")))
            else:
                raise ValidationFailed(f"{where}: cần clip_id hoặc sequence_id.")
            if field != "dialogue" and field != "clips" and not (isinstance(it.get("value"), str)
                                                                  and it["value"].strip()):
                raise ValidationFailed(f"{where}: value phải là chuỗi không rỗng.")
        speakers = {pid: _speaker_ids(s, pid) for pid in {x[3] for x in plan}}
    for kind, oid, sid, pid, field, value in plan:
        touched.add(sid)
        if kind == "clip" and field in ("image_prompt", "motion_prompt"):
            clip_svc.update(actx, oid, {field: value, f"{field}_src": stamp})
            results.append({"clip_id": oid, "field": field})
        elif kind == "clip":
            rows = _dialogue_rows(value, speakers[pid], warnings, f"clip #{oid}")
            _replace_dialogue(actx, oid, pid, rows)
            results.append({"clip_id": oid, "field": "dialogue", "lines": len(rows)})
        elif field == "clips":
            results.append(_submit_clips(actx, sid, value, warnings))
        else:
            seq_svc.update(actx, sid, {field: value})
            results.append({"sequence_id": sid, "field": field})
    resumed = _wake(ctx, sorted(touched))
    return {"applied": results, "warnings": warnings, "source": stamp, "resumed": resumed}


def _replace_dialogue(ctx: OpContext, clip_id: int, project_id: int, rows: list[dict]) -> None:
    from datetime import UTC, datetime

    u = entities.uow(ctx, f"Gửi lời thoại clip #{clip_id}", "prompts.submit")
    with u as s:
        u.project_id = project_id
        now = datetime.now(UTC)
        for old in s.scalars(select(DialogueLine).where(DialogueLine.clip_id == clip_id,
                                                        DialogueLine.deleted_at.is_(None))):
            old.deleted_at = now
        for rk, ln in zip(rank.n_after(None, len(rows)), rows, strict=True):
            s.add(DialogueLine(clip_id=clip_id, rank=rk, **ln))


def _submit_clips(ctx: OpContext, sequence_id: int, value: Any, warnings: list[dict]) -> dict[str, Any]:
    """Agent chia clip: `value` theo schema đầu ra `story_split_clips` → cảnh + clip + thoại (thay clip cũ)."""
    from pydantic import ValidationError

    try:
        split = S.SplitOut.model_validate(value)
    except ValidationError as e:
        raise ValidationFailed("clips không đúng định dạng story_split_clips.",
                               extra={"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:20]]}
                               ) from None
    with ctx.services.db.session() as s:
        seq = writer.get_sequence(s, sequence_id)
        p = writer.get_project(s, seq.project_id)
        key, g = writer.genre_of(s, p)
        cast = writer.load_cast(s, p, seq.number)
    scenes, planned, warns = writer.plan_clips(split, cast, key, g)
    warnings.extend(warns)
    writer._clear_sequence(ctx, sequence_id)
    scene_ids = {sc["key"]: seq_svc.scene_create(ctx, sequence_id, sc["title"],
                                                 location_asset_id=sc["location_asset_id"], state=sc["state"])["id"]
                 for sc in scenes}
    fields = [{**pc["fields"], **({"scene_id": scene_ids[pc["scene"]]} if pc["scene"] in scene_ids else {})}
              for pc in planned]
    created = clip_svc.bulk_create(ctx, sequence_id, fields)
    for row, pc in zip(created, planned, strict=True):
        if pc["lines"]:
            _replace_dialogue(ctx, row["id"], p.id, pc["lines"])
    return {"sequence_id": sequence_id, "field": "clips", "clips": len(created)}


def _wake(ctx: OpContext, sequence_ids: list[int]) -> list[int]:
    from sagareel.services.pipeline import engine  # import muộn: pipeline dùng story

    out = []
    for sid in sequence_ids:
        try:
            if engine.resume_if_waiting(ctx.services, sid, statuses=("waiting_input",)):
                out.append(sid)
        except AppError:
            continue
    return out
