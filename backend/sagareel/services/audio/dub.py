"""Dịch + lồng tiếng một tập (`audio.translate_dub`, Task BF): LLM `translate` → `dialogue_line.translations[lang]`
(một change set, hoàn tác được) → TTS theo `voice_profile.per_lang` (`dialogue.generate(lang=…)`).

Câu đã có bản dịch được giữ (trừ `force`). `dry_run`: số câu cần dịch, số lần gọi LLM, số câu TTS (chưa gọi gì).
"""

from __future__ import annotations

import json
import re
from typing import Any

import anyio
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ValidationFailed
from sagareel.db.models import DialogueLine, Project
from sagareel.services import entities
from sagareel.services.audio import dialogue

LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
BATCH = 40  # câu mỗi lần gọi LLM
JOB_TYPE = "audio.translate_dub"
LANE = "story"  # cùng lane với các công việc LLM


def _load(ctx: OpContext, sequence_id: int, lang: str, force: bool) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        seq = entities.get(s, "sequence", sequence_id)
        project = s.get(Project, seq.project_id)
        clip_ids = [c.id for c in dialogue.clips_of_sequence(s, sequence_id)]
        lines = list(dialogue.lines_of(s, clip_ids)) if clip_ids else []
        primary = (project.language or "vi")
        todo = [{"id": str(ln.id), "text": ln.text} for ln in lines
                if ln.text.strip() and (force or not str((ln.translations or {}).get(lang) or "").strip())]
        # Câu chưa có bản dịch nhưng sẽ được đọc sau khi dịch: TTS dry-run (plan_tts) bỏ sót vì chưa có chữ để đọc.
        fresh = [ln for ln in lines if ln.source == "tts" and ln.text.strip()
                 and not str((ln.translations or {}).get(lang) or "").strip()]
        return {"project_id": project.id, "primary": primary, "lines": len(lines), "todo": todo,
                "title": seq.title, "untranslated_tts": {"lines": len(fresh), "chars": sum(len(x.text) for x in fresh)}}


async def _translate(ctx: OpContext, lang: str, items: list[dict[str, str]]) -> dict[str, str]:
    from sagareel.services import llm

    out: dict[str, str] = {}
    for i in range(0, len(items), BATCH):
        chunk = items[i:i + BATCH]
        payload = json.dumps({"target_language": lang, "items": chunk}, ensure_ascii=False)
        outcome = await llm.run_task(ctx.services, "translate", {"input": payload})
        data = outcome.data if outcome.data is not None else _json(outcome.text)
        rows = data.get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise AppError("llm_bad_output", "LLM dịch trả kết quả không đúng định dạng (thiếu items).",
                           hint="Thử lại hoặc đổi model cho công việc translate.", status=502)
        want = {x["id"] for x in chunk}
        for r in rows:
            if isinstance(r, dict) and str(r.get("id")) in want and str(r.get("text") or "").strip():
                out[str(r["id"])] = str(r["text"]).strip()
    return out


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _save(ctx: OpContext, lang: str, translated: dict[str, str], project_id: int) -> int | None:
    u = entities.uow(ctx, f"Dịch lời thoại sang {lang}", "audio.translate_dub")
    with u as s:
        u.project_id = project_id
        for lid, text in translated.items():
            ln = s.get(DialogueLine, int(lid))
            if ln is None or ln.deleted_at is not None:
                continue
            tr = dict(ln.translations or {})
            if tr.get(lang) != text:
                tr[lang] = text
                ln.translations = tr
    return u.change_set_id


async def _prepare(ctx: OpContext, sequence_id: int, lang: str, force: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    if not LANG_RE.match(lang or ""):
        raise ValidationFailed("Mã ngôn ngữ không hợp lệ (vd. en, ja, zh-CN).")
    info = await anyio.to_thread.run_sync(_load, ctx, sequence_id, lang, force)
    if lang == info["primary"]:
        raise ValidationFailed(f"'{lang}' là ngôn ngữ chính của dự án — không cần dịch.",
                               hint="Chọn ngôn ngữ đích khác; đọc lại giọng chính bằng audio.generate.")
    todo = info["todo"]
    estimate = {"lines": info["lines"], "to_translate": len(todo), "chars": sum(len(x["text"]) for x in todo),
                "llm_calls": -(-len(todo) // BATCH) if todo else 0}
    return info, estimate


async def translate_dub(ctx: OpContext, sequence_id: int, lang: str, *, dry_run: bool = False, force: bool = False,
                        tts: bool = True) -> dict[str, Any]:
    """Op: `dry_run` → ước tính (đếm cả câu chưa dịch); còn lại xếp job `audio.translate_dub` và trả `job_id`."""
    from sagareel.services import jobs

    info, estimate = await _prepare(ctx, sequence_id, lang, force)
    if dry_run:
        tts_est = None
        if tts:
            def est() -> dict:
                return dialogue.generate(ctx, sequence_id=sequence_id, lang=lang, dry_run=True)
            try:
                tts_est = dict((await anyio.to_thread.run_sync(est))["estimate"])
                extra = info["untranslated_tts"]
                tts_est["lines"] = int(tts_est.get("lines", 0)) + extra["lines"]
                tts_est["chars"] = int(tts_est.get("chars", 0)) + extra["chars"]
                tts_est["untranslated"] = extra["lines"]
            except AppError as e:
                tts_est = {"error": e.code, "message": e.detail}
        return {"dry_run": True, "lang": lang, "estimate": {**estimate, "tts": tts_est}}

    def submit() -> dict:
        return jobs.submit(ctx.services, JOB_TYPE,
                           {"sequence_id": sequence_id, "lang": lang, "force": force, "tts": tts,
                            "project_id": info["project_id"], "actor": ctx.actor},
                           lane=LANE, project_id=info["project_id"], sequence_id=sequence_id,
                           client_ref=f"{JOB_TYPE}:{sequence_id}:{lang}", max_attempts=1,
                           cost_estimate={"kind": "llm", "task": "translate", **estimate})

    job = await anyio.to_thread.run_sync(submit)
    return {"job_id": job["id"], "lang": lang, "estimate": estimate}


async def run_dub(ctx: OpContext, sequence_id: int, lang: str, *, force: bool = False, tts: bool = True,
                  progress: Any = None) -> dict[str, Any]:
    """Thân job: dịch → lưu (một change set) → xếp job TTS. Kết quả job gồm `tts.job_ids`."""
    info, estimate = await _prepare(ctx, sequence_id, lang, force)
    todo = info["todo"]
    if progress:
        progress(None, stage=f"Đang dịch {len(todo)} câu sang {lang}")
    translated = await _translate(ctx, lang, todo) if todo else {}
    cs_id = await anyio.to_thread.run_sync(_save, ctx, lang, translated, info["project_id"]) if translated else None
    missing = [int(x["id"]) for x in todo if x["id"] not in translated]
    out: dict[str, Any] = {"lang": lang, "translated": len(translated), "missing": missing, "change_set_id": cs_id,
                           "estimate": estimate}
    if tts:
        if progress:
            progress(None, stage="Đang xếp lịch đọc TTS")

        def gen() -> dict:
            return dialogue.generate(ctx, sequence_id=sequence_id, lang=lang)
        out["tts"] = await anyio.to_thread.run_sync(gen)
    return out
