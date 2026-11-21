"""Job `pipeline.run`: chạy quy trình của MỘT tập theo preset, nối lại được (spec §13, §7.4, phụ lục film-craft §2, §5).

Nguồn sự thật là `sequence.pipeline.run` (xem `services.story.state`): danh sách bước + trạng thái từng bước + job con
+ góp ý khi bị từ chối. Handler chỉ gọi `advance()`: đọc trạng thái, chạy bước kế tiếp chưa xong, lưu sau mỗi lần đổi.

- Bước viết (`write_*`, `outline`, `split_clips`, `*_prompts`, `dialogue`): tôn trọng `author.<task>` — `llm` gọi
  `services.story.writer`; `manual`/`agent` (hoặc LLM chưa cấu hình) → bước `waiting_input`, `prompts.submit` hoặc
  `pipeline.run` (tiếp tục) đánh thức; bước đã có dữ liệu (kịch bản, prompt…) thì coi là xong (chạy lại không làm lại).
- Bước job (`generate_*`, `render*`, `export`, âm thanh/phụ đề): gọi op theo tên qua registry (`clip.regenerate`,
  `render.start`, `export.create`, `audio.generate`…), lưu id job/take con vào trạng thái TRƯỚC khi chờ (nối lại thì chỉ
  chờ tiếp, không gửi lại), chờ tới khi mọi job/take con kết thúc. Op chưa có (mô-đun âm thanh chưa gộp) → bỏ qua kèm
  cảnh báo.
- Gate `gate:<tên>`: đã duyệt (`sequence.gates[tên].status == approved`) thì qua; không thì dừng `waiting_review`.
- Dừng (`waiting_*`): job được "đỗ" bằng compare-and-set `running → waiting_*` rồi handler trả về — lần ghi cuối của
  worker (CAS trên `running`) thành no-op; `recover()` không đụng job đỗ; `job.cancel` huỷ được. `gate.approve`,
  `gate.reject`, `prompts.submit`, `pipeline.run` đưa job về `queued` (trễ 0,5 s để lần chạy cũ kết thúc hẳn).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from sagareel.core import speech
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Conflict, NotFound, ValidationFailed
from sagareel.db.base import utcnow
from sagareel.db.models import BibleItem, Clip, DialogueLine, Job, Sequence, Take
from sagareel.services import entities, jobs
from sagareel.services.pipeline import workflow as W
from sagareel.services.story import author, state, writer

logger = logging.getLogger("sagareel.pipeline")

JOB_TYPE = "pipeline.run"
LANE = "pipeline"
EVENT = "pipeline.updated"
SYSTEM_ACTOR = "system:pipeline"
REQUEUE_DELAY_S = 0.5
ACTIVE = ("queued", "running", "waiting_review", "waiting_input", "pending_release")
DONE_STEP = ("done", "skipped")
TERMINAL_TAKE = frozenset({"succeeded", "failed", "cancelled"})
BLOCKED_JOB = frozenset({"needs_attention", "interrupted", "paused"})
REGEN_BATCH = 200
# Op có thể dùng cho bước âm thanh/phụ đề (mô-đun D): op đầu tiên có trong registry được dùng.
AUDIO_OPS = ("audio.generate",)
SUBTITLE_OPS = ("subtitle.generate", "subtitles.generate", "audio.subtitles")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- Trạng thái ------------------------------------------------------------------------------------------------------


def new_step(defn: dict[str, Any]) -> dict[str, Any]:
    return {"name": defn["step"], "enabled": bool(defn.get("enabled", True)), "rerun": list(defn.get("rerun") or []),
            "params": dict(defn.get("params") or {}), "status": "pending", "feedback": "", "attempts": 0,
            "children": {}, "result": {}, "warnings": [], "awaiting": False}


def init_state(workflow_key: str, steps: list[dict[str, Any]], *, settings: dict[str, Any], params: dict[str, Any],
               genre_overrides: dict[str, Any], pilot: bool, batch_ids: list[int],
               release: dict[str, Any] | None, status: str) -> dict[str, Any]:
    return {"workflow": workflow_key, "status": status, "job_id": None, "steps": [new_step(s) for s in steps],
            "waiting": None, "settings": dict(settings), "params": dict(params),
            "genre_overrides": dict(genre_overrides), "pilot": pilot, "batch_ids": list(batch_ids),
            "release": release, "error": None, "created_at": now_iso(), "updated_at": now_iso()}


def load_state(s: Session, sequence_id: int) -> dict[str, Any]:
    return state.read(s, sequence_id, "run")


def save_state(services: Any, sequence_id: int, st: dict[str, Any], *, project_id: int | None = None) -> None:
    st["updated_at"] = now_iso()
    state.replace(services.db, sequence_id, "run", st)
    try:
        services.bus.publish(EVENT, {"sequence_id": sequence_id, "status": st.get("status"),
                                     "step": current_step_name(st), "waiting": st.get("waiting")},
                             project_id=project_id)
    except Exception:
        logger.exception("Không phát được %s", EVENT)


def current_step_name(st: dict[str, Any]) -> str | None:
    for step in st.get("steps") or []:
        if step["status"] not in DONE_STEP:
            return step["name"]
    return None


def read_gates(s: Session, sequence_id: int) -> dict[str, Any]:
    seq = s.get(Sequence, sequence_id)
    return dict(seq.gates or {}) if seq is not None else {}


def write_gate(services: Any, sequence_id: int, name: str, value: dict[str, Any]) -> None:
    """`sequence.gates[name] = value` (trạng thái chạy, không qua lịch sử hoàn tác — xem `services.story.state`)."""
    import json

    with services.db.session(immediate=True) as s:
        s.execute(text(
            "UPDATE sequence SET gates = json_set(CASE WHEN json_valid(gates) THEN gates ELSE '{}' END, :path, "
            "json(:v)) WHERE id = :id"),
            {"path": f'$."{name}"', "v": json.dumps(value, ensure_ascii=False), "id": sequence_id})


# --- Job: đỗ / đánh thức -------------------------------------------------------------------------------------------


def park(services: Any, sequence_id: int, st: dict[str, Any], job_id: int | None, status: str, stage: str, *,
         project_id: int | None = None) -> bool:
    """Lưu trạng thái chờ + đỗ job (`running → waiting_*`) trong MỘT transaction khoá ghi.

    Có tín hiệu `signal.nudge` (dữ liệu mới tới trong lúc bước đang chạy: `prompts.submit`, `gate.approve`…) → không
    dừng, xoá cờ, trả False để chạy lại bước. Nhờ khoá ghi, bên đánh thức hoặc thấy `waiting_*` (và đưa job về hàng đợi)
    hoặc kịp đặt cờ trước khi job dừng — không bao giờ có job đỗ mà dữ liệu đã đủ.
    """
    data = None
    with services.db.session(immediate=True) as s:
        sig = state.read(s, sequence_id, "signal")
        if sig.get("nudge"):
            state.write(s, sequence_id, "signal", {**sig, "nudge": 0})
            return False
        st["updated_at"] = now_iso()
        state.write(s, sequence_id, "run", st)
        if job_id is not None:
            res = s.execute(update(Job).where(Job.id == job_id, Job.status == "running")
                            .values(status=status, stage=stage, updated_at=utcnow())
                            .execution_options(synchronize_session=False))
            if res.rowcount == 1:
                data = jobs.job_dict(s.get(Job, job_id))
    if data is not None:
        jobs.publish_updated(services, data)
    services.bus.publish(EVENT, {"sequence_id": sequence_id, "status": status, "step": current_step_name(st),
                                 "waiting": st.get("waiting")}, project_id=project_id)
    return True


def ensure_job(services: Any, sequence_id: int, *, delay_s: float = REQUEUE_DELAY_S) -> dict[str, Any]:
    """Đưa job quy trình của tập về hàng đợi (job đỗ → queued; không còn job sống → tạo job mới). Trả `job_dict`."""
    with services.db.session(immediate=True) as s:
        st = load_state(s, sequence_id)
        seq = entities.get(s, "sequence", sequence_id)
        job = s.get(Job, st.get("job_id")) if st.get("job_id") else None
        if job is not None and job.status in ("waiting_review", "waiting_input"):
            job.status = "queued"
            job.next_attempt_at = utcnow() + timedelta(seconds=delay_s)
            job.stage = "Chờ tiếp tục"
            job.error_code = job.error_message = job.hint = None
            s.flush()
        elif job is None or job.status not in ("queued", "running"):
            job = jobs.enqueue(s, JOB_TYPE, {"sequence_id": sequence_id}, lane=LANE, project_id=seq.project_id,
                               sequence_id=sequence_id, client_ref=f"pipeline:{sequence_id}", max_attempts=5)
            if job.status == "queued" and delay_s:
                job.next_attempt_at = utcnow() + timedelta(seconds=delay_s)
            st["job_id"] = job.id
            state.write(s, sequence_id, "run", st)
        data = jobs.job_dict(job)
    jobs.notify(services, data)
    return data


def resume_if_waiting(services: Any, sequence_id: int, *, statuses: tuple[str, ...] = ("waiting_input",)) -> bool:
    """Có dữ liệu mới cho tập: quy trình đang chờ (`statuses`) → chạy tiếp; đang chạy → đặt cờ `nudge` để bước hiện
    tại được kiểm lại thay vì dừng chờ. Trả True nếu quy trình sẽ xem lại dữ liệu mới."""
    with services.db.session(immediate=True) as s:
        st = load_state(s, sequence_id)
        if not st:
            return False
        if st.get("status") in ("running", "queued"):
            sig = state.read(s, sequence_id, "signal")
            state.write(s, sequence_id, "signal", {**sig, "nudge": int(sig.get("nudge") or 0) + 1})
            return True
        if st.get("status") not in statuses:
            return False
        st["status"] = "queued"
        st["updated_at"] = now_iso()
        state.write(s, sequence_id, "run", st)
    ensure_job(services, sequence_id)
    return True


# --- Ngữ cảnh chạy ---------------------------------------------------------------------------------------------------


@dataclasses.dataclass
class Outcome:
    kind: str  # done | skipped | wait_review | wait_input | failed
    result: dict[str, Any] = dataclasses.field(default_factory=dict)
    info: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Run:
    services: Any
    sequence_id: int
    project_id: int
    st: dict[str, Any]
    job_id: int | None = None
    log: Callable[[str, str], None] = lambda level, msg: None
    progress: Callable[..., None] = lambda *a, **k: None

    @property
    def ctx(self) -> OpContext:
        return OpContext(services=self.services, actor=SYSTEM_ACTOR, role="owner", project_id=self.project_id)

    def save(self) -> None:
        save_state(self.services, self.sequence_id, self.st, project_id=self.project_id)


async def _thread(fn, *args, **kw):
    return await anyio.to_thread.run_sync(lambda: fn(*args, **kw))


# --- Kiểm "bước đã xong" ---------------------------------------------------------------------------------------------


def _clips(s: Session, sequence_id: int) -> list[Clip]:
    return list(s.scalars(select(Clip).where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None))
                          .order_by(Clip.rank, Clip.id)))


def needs_image(c: Clip) -> bool:
    return c.engine in ("ai_video", "still_motion") and c.link != "continue"


def satisfied(s: Session, task: str, sequence_id: int) -> bool:
    seq = s.get(Sequence, sequence_id)
    if task == "write_bible":
        n = s.scalar(select(func.count()).select_from(BibleItem).where(
            BibleItem.project_id == seq.project_id, BibleItem.deleted_at.is_(None), BibleItem.status != "rejected"))
        return bool(n)
    if task == "outline":
        return bool((seq.synopsis or "").strip())
    if task == "write_script":
        return bool((seq.script or "").strip())
    if task == "memory_summarize":
        return bool(state.of(seq, "memory").get("summarized_at"))
    rows = _clips(s, sequence_id)
    if task == "split_clips":
        return bool(rows)
    if task == "image_prompts":
        return bool(rows) and all(c.image_prompt.strip() for c in rows if needs_image(c))
    if task == "motion_prompts":
        return bool(rows) and all(c.motion_prompt.strip() for c in rows if c.engine == "ai_video")
    if task == "dialogue":
        lines: dict[int, int] = {}
        for ln in s.scalars(select(DialogueLine).where(DialogueLine.clip_id.in_([c.id for c in rows]),
                                                       DialogueLine.deleted_at.is_(None))):
            lines[ln.clip_id] = lines.get(ln.clip_id, 0) + speech.syllables(ln.text)
        return all(lines.get(c.id, 0) <= speech.max_syllables(c.duration_ms / 1000) for c in rows)
    return False


# --- Bước viết -------------------------------------------------------------------------------------------------------


INPUT_HINT = {
    "write_bible": "Gửi bible (story.submit task=write_bible) hoặc thêm mục Story Bible (bible.create), rồi tiếp tục.",
    "outline": "Gửi dàn ý (story.submit task=outline) hoặc điền tóm tắt tập (prompts.submit field=synopsis).",
    "write_script": "Gửi kịch bản (prompts.submit field=script).",
    "split_clips": "Tạo clip (clip.bulk_create / import.apply) hoặc prompts.submit field=clips.",
    "image_prompts": "Gửi prompt ảnh cho mọi clip (prompts.submit field=image_prompt).",
    "motion_prompts": "Gửi prompt chuyển động cho mọi clip ai_video (prompts.submit field=motion_prompt).",
    "dialogue": "Rút gọn lời thoại quá dài (prompts.submit field=dialogue).",
}


async def run_llm_step(r: Run, step: dict[str, Any]) -> Outcome:
    task = step["name"]
    feedback = step.get("feedback") or ""
    own = (r.st.get("params") or {}).get(task)
    params = {**(own if isinstance(own, dict) else {}), **(step.get("params") or {})}

    def check() -> tuple[bool, str]:
        with r.services.db.session() as s:
            seq = s.get(Sequence, r.sequence_id)
            proj = entities.get(s, "project", seq.project_id)
            return satisfied(s, task, r.sequence_id), author.mode(s, task, proj, seq, r.st.get("settings"))

    ok, mode = await _thread(check)
    if ok and (not feedback or step.get("awaiting")):
        step["awaiting"] = False
        return Outcome("done", {"already": True, "mode": mode})
    if task == "memory_summarize":
        try:
            res = await writer.memory_summarize(r.ctx, r.sequence_id, feedback=feedback, log=r.log)
        except AppError as e:
            if e.code in ("llm_not_configured", "script_missing", "prompt_missing"):
                return Outcome("skipped", {"reason": e.code, "message": e.detail})
            raise
        return Outcome("done", res)
    if mode in ("manual", "agent"):
        step["awaiting"] = True
        return Outcome("wait_input", info={"task": task, "mode": mode, "hint": INPUT_HINT.get(task, "")})
    try:
        res = await _call_writer(r, task, feedback, params)
    except AppError as e:
        if e.code in ("llm_not_configured", "prompt_missing"):
            # Thiếu LLM hoặc thiếu prompt của dự án: không làm hỏng cả quy trình — chờ người/agent gửi kết quả bước
            # (story.submit / prompts.submit) hoặc viết prompt (prompt.set) rồi chạy tiếp.
            step["awaiting"] = True
            return Outcome("wait_input", info={"task": task, "mode": "manual", "reason": e.code,
                                               "message": e.detail, "hint": e.hint})
        raise
    if res is None:
        step["awaiting"] = True
        return Outcome("wait_input", info={"task": task, "mode": "manual", "reason": "missing_input",
                                           "hint": INPUT_HINT.get(task, "")})
    return Outcome("done", res)


async def _call_writer(r: Run, task: str, feedback: str, params: dict[str, Any]) -> dict[str, Any] | None:
    ctx, sid, gov = r.ctx, r.sequence_id, r.st.get("genre_overrides") or {}
    run_settings = r.st.get("settings") or {}
    if task == "write_bible":
        def idea() -> str:
            with r.services.db.session() as s:
                p = entities.get(s, "project", r.project_id)
                return params.get("idea") or (r.st.get("params") or {}).get("idea") or p.description or ""

        text_ = await _thread(idea)
        if not text_.strip():
            return None
        return await writer.write_bible(ctx, r.project_id, text_, genre=params.get("genre"), feedback=feedback,
                                        log=r.log)
    if task == "outline":
        def targets() -> list[int]:
            ids = list(r.st.get("batch_ids") or [sid])
            with r.services.db.session() as s:
                rows = [s.get(Sequence, i) for i in ids]
                missing = sorted((q for q in rows if q is not None and q.deleted_at is None
                                  and (not (q.synopsis or "").strip() or (q.id == sid and feedback))),
                                 key=lambda q: q.number)
                return [q.id for q in missing]

        ids = await _thread(targets)
        if sid not in ids:
            ids.append(sid)
        out: dict[str, Any] = {"sequence_ids": [], "warnings": []}
        for i in range(0, len(ids), 10):  # dàn ý theo lô 10 tập
            res = await writer.outline(ctx, r.project_id, sequence_ids=ids[i:i + 10], arc=params.get("arc", ""),
                                       feedback=feedback, run=run_settings, genre_overrides=gov, log=r.log)
            out["sequence_ids"] += res["sequence_ids"]
            out["warnings"] += res["warnings"]
            out["model"] = res["model"]
        return out
    if task == "write_script":
        return await writer.write_script(ctx, sid, feedback=feedback, run=run_settings, genre_overrides=gov,
                                         log=r.log)
    if task == "split_clips":
        return await writer.split_clips(ctx, sid, feedback=feedback, replace=True, run=run_settings,
                                        genre_overrides=gov, log=r.log)
    if task in ("image_prompts", "motion_prompts"):
        return await writer.write_prompts(ctx, "image" if task == "image_prompts" else "motion", sequence_id=sid,
                                          feedback=feedback, overwrite=bool(feedback), log=r.log)
    if task == "dialogue":
        return await writer.write_dialogue(ctx, sequence_id=sid, feedback=feedback, overwrite=bool(feedback),
                                           log=r.log)
    raise ValidationFailed(f"Bước viết lạ: {task}")


# --- Bước job con ------------------------------------------------------------------------------------------------------


async def invoke_op(r: Run, name: str, payload: dict[str, Any]) -> Any:
    """Gọi op theo tên (qua registry — dùng lại kiểm tra/ước tính của op). Tách riêng để test thay được."""
    from sagareel.core.ops import invoke

    return await invoke(name, payload, r.ctx)


def op_exists(name: str) -> bool:
    from sagareel import ops as ops_pkg
    from sagareel.core.ops import REGISTRY

    ops_pkg.load_all()
    return name in REGISTRY


def child_ids(result: Any) -> tuple[list[int], list[int]]:
    """(job ids, take ids) từ kết quả op: `job_dict` (`id`+`type`), `job_id`, `job_ids`, `jobs`, `take_ids`."""
    jobs_: list[int] = []
    takes: list[int] = []
    if isinstance(result, dict):
        if isinstance(result.get("id"), int) and "type" in result and "status" in result:
            jobs_.append(result["id"])
        if isinstance(result.get("job_id"), int):
            jobs_.append(result["job_id"])
        for j in result.get("job_ids") or []:
            if isinstance(j, int):
                jobs_.append(j)
        for j in result.get("jobs") or []:
            if isinstance(j, dict) and isinstance(j.get("id"), int):
                jobs_.append(j["id"])
            elif isinstance(j, int):
                jobs_.append(j)
        takes += [t for t in result.get("take_ids") or [] if isinstance(t, int)]
    return list(dict.fromkeys(jobs_)), list(dict.fromkeys(takes))


def _regen_targets(s: Session, sequence_id: int, part: str, force: bool) -> list[int]:
    rows = _clips(s, sequence_id)
    if part == "image":
        rows = [c for c in rows if needs_image(c) and (force or c.image_take_id is None)]
    else:
        rows = [c for c in rows if c.engine in ("ai_video", "still_motion") and (force or c.video_take_id is None)]
    # bỏ clip đang có take chạy dở cho phần này (vd. lần chạy trước đã gửi) — không gửi trùng
    busy = set(s.scalars(select(Take.owner_id).where(Take.owner_type == "clip", Take.part == part,
                                                     Take.owner_id.in_([c.id for c in rows]),
                                                     Take.status.in_(("queued", "running")))))
    return [c.id for c in rows if c.id not in busy]


async def spawn_children(r: Run, step: dict[str, Any]) -> tuple[list[int], list[int], dict[str, Any]] | Outcome:
    """Gửi job con của bước. Trả (job ids, take ids, kết quả op) hoặc `Outcome` khi không có gì để làm/bỏ qua."""
    name = step["name"]
    feedback = step.get("feedback") or ""
    params = step.get("params") or {}
    gparams = r.st.get("params") or {}
    sid = r.sequence_id
    if name in ("generate_images", "generate_videos"):
        part = "image" if name == "generate_images" else "video"
        ids = await _thread(lambda: _with(r, lambda s: _regen_targets(s, sid, part, bool(feedback))))
        if not ids:
            return Outcome("done", {"already": True})
        key = "image.synced" if part == "image" else "motion.synced"

        def missing_prompt(s: Session) -> bool:  # clip synced cần prompt ráp của dự án; verbatim thì không
            from sagareel.db.models import Clip
            from sagareel.services import prompts

            synced = s.scalar(select(Clip.id).where(Clip.id.in_(ids), Clip.prompt_mode != "verbatim").limit(1))
            return synced is not None and prompts.row(s, r.project_id, key) is None

        if await _thread(lambda: _with(r, missing_prompt)):
            step["awaiting"] = True
            return Outcome("wait_input", info={
                "task": name, "mode": "manual", "reason": "prompt_missing",
                "message": f"Dự án chưa có prompt ráp '{key}' cho clip chế độ synced.",
                "hint": f"Viết prompt '{key}' (prompt.set), hoặc đặt clip prompt_mode=verbatim với prompt cuối do bạn/agent "
                        "viết (clip.update), rồi chạy tiếp pipeline.run."})
        all_jobs: list[int] = []
        all_takes: list[int] = []
        results = []
        overrides: dict[str, Any] = {"feedback": feedback} if feedback else {}
        for i in range(0, len(ids), REGEN_BATCH):
            res = await invoke_op(r, "clip.regenerate", {"ids": ids[i:i + REGEN_BATCH], "part": part,
                                                         "overrides": overrides})
            j, t = child_ids(res)
            all_jobs += j
            all_takes += t
            results.append({"skipped": res.get("skipped", []), "warnings": res.get("warnings", [])}
                           if isinstance(res, dict) else {})
        return all_jobs, all_takes, {"clips": len(ids), "batches": results}
    if name in ("generate_audio", "subtitles"):
        cands = AUDIO_OPS if name == "generate_audio" else SUBTITLE_OPS
        op_name = next((o for o in cands if op_exists(o)), None)
        if op_name is None:
            return Outcome("skipped", {"reason": "not_available",
                                       "message": "Mô-đun âm thanh/phụ đề chưa có trong bản này; bỏ qua bước."})
        res = await invoke_op(r, op_name, {"sequence_id": sid, **params})
        j, t = child_ids(res)
        return j, t, {"op": op_name}
    if name in ("render_animatic", "render_preview", "render"):
        variant = {"render_animatic": "animatic", "render_preview": "draft", "render": "final"}[name]
        payload: dict[str, Any] = {"sequence_id": sid, "variant": variant}
        if name == "render_preview":
            payload["preview_seconds"] = float(params.get("seconds") or 15)
        res = await invoke_op(r, "render.start", payload)
        j, t = child_ids(res)
        return j, t, {"variant": variant}
    if name == "export":
        payload = {"sequence_ids": [sid], "platforms": list(params.get("platforms") or gparams.get("platforms") or [])}
        res = await invoke_op(r, "export.create", payload)
        j, t = child_ids(res)
        return j, t, {"export_ids": (res or {}).get("export_ids", []) if isinstance(res, dict) else []}
    raise ValidationFailed(f"Bước job lạ: {name}")


def _with(r: Run, fn):
    with r.services.db.session() as s:
        return fn(s)


def child_status(s: Session, job_ids: list[int], take_ids: list[int]) -> dict[str, Any]:
    jrows = list(s.scalars(select(Job).where(Job.id.in_(job_ids)))) if job_ids else []
    trows = list(s.scalars(select(Take).where(Take.id.in_(take_ids)))) if take_ids else []
    pending = [j.id for j in jrows if j.status not in jobs.TERMINAL and j.status not in BLOCKED_JOB]
    pending += [t.id for t in trows if t.status not in TERMINAL_TAKE]
    blocked = [{"job_id": j.id, "status": j.status, "error_code": j.error_code} for j in jrows
               if j.status in BLOCKED_JOB]
    failed = [{"job_id": j.id, "status": j.status, "error_code": j.error_code, "error": j.error_message}
              for j in jrows if j.status in ("failed", "cancelled")]
    failed += [{"take_id": t.id, "status": t.status} for t in trows if t.status in ("failed", "cancelled")]
    return {"pending": len(pending), "blocked": blocked, "failed": failed, "jobs": len(jrows), "takes": len(trows)}


async def run_job_step(r: Run, step: dict[str, Any]) -> Outcome:
    children = step.get("children") or {}
    if not children.get("spawned"):
        spawned = await spawn_children(r, step)
        if isinstance(spawned, Outcome):
            return spawned
        job_ids, take_ids, info = spawned
        step["children"] = {"spawned": True, "jobs": job_ids, "takes": take_ids, "info": info}
        r.save()  # lưu TRƯỚC khi chờ: nối lại thì chỉ chờ tiếp
        r.log("info", f"{W.label(step['name'])}: đã gửi {len(job_ids)} job, {len(take_ids)} take")
    children = step["children"]
    poll = await _thread(lambda: _with(r, lambda s: float(writer.setting(s, "pipeline.poll_s",
                                                                          entities.get(s, "project", r.project_id))
                                                          or 2.0)))
    while True:
        st = await _thread(lambda: _with(r, lambda s: child_status(s, children["jobs"], children["takes"])))
        if st["blocked"]:
            return Outcome("failed", info={"code": "child_blocked", "message": f"{W.label(step['name'])}: có job con "
                                           "cần xử lý (needs_attention/interrupted).", "children": st,
                                           "hint": "Xử lý job con (job.retry / job.clear_attention) rồi chạy lại "
                                           "pipeline.run."})
        if st["pending"] == 0:
            if st["failed"]:
                return Outcome("failed", info={"code": "child_failed", "message": f"{W.label(step['name'])}: "
                                               f"{len(st['failed'])} job/take con thất bại.", "children": st,
                                               "hint": "Xem job con, sửa rồi chạy lại pipeline.run (bước này chạy "
                                               "lại phần còn thiếu)."})
            return Outcome("done", {**(children.get("info") or {}), "jobs": st["jobs"], "takes": st["takes"]})
        await asyncio.sleep(max(0.01, poll))


async def run_gate(r: Run, step: dict[str, Any]) -> Outcome:
    gate = step["name"][5:]
    if gate == "pilot" and not r.st.get("pilot"):
        return Outcome("skipped", {"reason": "not_pilot"})
    gates = await _thread(lambda: _with(r, lambda s: read_gates(s, r.sequence_id)))
    g = gates.get(gate) or {}
    if g.get("status") == "approved":
        return Outcome("done", {"approved_by": g.get("by"), "at": g.get("at")})
    return Outcome("wait_review", info={"gate": gate})


async def run_step(r: Run, step: dict[str, Any]) -> Outcome:
    name = step["name"]
    if W.is_gate(name):
        return await run_gate(r, step)
    if name in W.LLM_STEPS:
        return await run_llm_step(r, step)
    if name in W.JOB_STEPS:
        return await run_job_step(r, step)
    if name == "auto_select":
        return Outcome("done", {"note": "take mới tự được chọn khi tạo xong (select=true)"})
    raise ValidationFailed(f"Bước lạ: {name}")


# --- Thả lô ----------------------------------------------------------------------------------------------------------


def _release_step(r: Run) -> str | None:
    rel = r.st.get("release") or {}
    if not rel or rel.get("done") or not rel.get("next"):
        return None
    return rel.get("after")


def maybe_release(r: Run, *, finished: bool = False) -> list[int]:
    """Thả các tập chờ (lô) khi bước `release.after` đã xong (hoặc quy trình kết thúc)."""
    after = _release_step(r)
    if after is None:
        return []
    step = next((x for x in r.st["steps"] if x["name"] == after), None)
    if not finished and step is not None and step["status"] not in DONE_STEP:
        return []
    rel = r.st["release"]
    nxt = list(rel["next"])
    targets = nxt if rel.get("mode") == "parallel" else nxt[:1]
    for sid in targets:
        def prep(sid=sid) -> None:
            with r.services.db.session() as s:
                fst = load_state(s, sid)
            if not fst or fst.get("status") != "pending_release":
                return
            fst["status"] = "queued"
            if rel.get("mode") != "parallel":
                rest = nxt[1:]
                fst["release"] = {"after": rel.get("follower_after") or rel["after"], "next": rest,
                                  "mode": "sequential", "done": False} if rest else None
            save_state(r.services, sid, fst, project_id=r.project_id)
            ensure_job(r.services, sid, delay_s=0)

        prep()
    rel["done"] = True
    r.log("info", f"Thả {len(targets)} tập kế tiếp của lô: {targets}")
    return targets


# --- Vòng chạy --------------------------------------------------------------------------------------------------------


async def advance(services: Any, sequence_id: int, *, job_id: int | None = None,
                  log: Callable[[str, str], None] | None = None,
                  progress: Callable[..., None] | None = None) -> dict[str, Any]:
    """Chạy tiếp quy trình của tập tới khi xong / dừng chờ / lỗi. Trả `{status, step?, waiting?}`."""

    def load() -> tuple[dict[str, Any], int]:
        with services.db.session() as s:
            seq = entities.get(s, "sequence", sequence_id)
            return load_state(s, sequence_id), seq.project_id

    st, project_id = await _thread(load)
    if not st:
        raise jobs.JobFailed("pipeline_missing", f"Tập #{sequence_id} chưa có quy trình nào được khởi chạy.",
                             hint="Gọi pipeline.run.")
    if st.get("status") in ("cancelled", "done"):
        return {"status": st["status"]}
    r = Run(services=services, sequence_id=sequence_id, project_id=project_id, st=st, job_id=job_id,
            log=log or (lambda level, msg: None), progress=progress or (lambda *a, **k: None))
    st["status"] = "running"
    st["waiting"] = None
    st["error"] = None
    if job_id is not None:
        st["job_id"] = job_id
    r.save()
    await _thread(maybe_release, r)
    total = len(st["steps"])
    while True:
        idx = next((i for i, x in enumerate(st["steps"]) if x["status"] not in DONE_STEP), None)
        if idx is None:
            st["status"] = "done"
            st["finished_at"] = now_iso()
            await _thread(maybe_release, r, finished=True)
            r.save()
            r.progress(100.0, stage="Xong quy trình")
            return {"status": "done"}
        step = st["steps"][idx]
        done_n = sum(1 for x in st["steps"] if x["status"] in DONE_STEP)
        r.progress(100.0 * done_n / total, stage=f"Bước {idx + 1}/{total}: {W.label(step['name'])}")
        if not step["enabled"]:
            step["status"] = "skipped"
            step["result"] = {"reason": "disabled"}
            r.save()
            continue
        step["status"] = "running"
        step["attempts"] = int(step.get("attempts") or 0) + 1
        step.setdefault("started_at", now_iso())
        r.save()
        try:
            out = await run_step(r, step)
        except jobs.TransientError:
            step["status"] = "pending"
            r.save()
            raise
        except asyncio.CancelledError:  # huỷ/tắt app: không ghi gì (pipeline.cancel đã ghi trạng thái của nó)
            raise
        except (AppError, jobs.JobFailed) as e:
            code = e.code
            msg = getattr(e, "detail", None) or getattr(e, "msg", str(e))
            out = Outcome("failed", info={"code": code, "message": msg, "hint": getattr(e, "hint", None)})
        if out.kind in DONE_STEP:
            step["status"] = out.kind
            step["result"] = out.result
            step["feedback"] = ""
            step["finished_at"] = now_iso()
            step["warnings"] = list(out.result.get("warnings") or []) if isinstance(out.result, dict) else []
            r.save()
            await _thread(maybe_release, r)
            continue
        if out.kind in ("wait_review", "wait_input"):
            status = "waiting_review" if out.kind == "wait_review" else "waiting_input"
            step["status"] = status
            st["status"] = status
            st["waiting"] = {"step": step["name"], "kind": out.kind[5:], "since": now_iso(), **out.info}
            stage = f"Chờ duyệt: {W.label(step['name'])}" if status == "waiting_review" else \
                f"Chờ nhập: {W.label(step['name'])}"
            if not await _thread(park, services, sequence_id, st, job_id, status, stage, project_id=project_id):
                # dữ liệu mới vừa tới (prompts.submit / gate.approve): kiểm lại bước thay vì dừng
                step["status"] = "pending"
                st["status"] = "running"
                st["waiting"] = None
                r.save()
                continue
            r.log("info", stage)
            return {"status": status, "step": step["name"], "waiting": st["waiting"]}
        # failed
        step["status"] = "failed"
        step["error"] = out.info
        st["status"] = "failed"
        st["error"] = {"step": step["name"], **out.info}
        r.save()
        raise jobs.JobFailed(out.info.get("code") or "pipeline_step_failed",
                             f"Bước {W.label(step['name'])} lỗi: {out.info.get('message')}",
                             hint=out.info.get("hint") or "Sửa lỗi rồi gọi pipeline.run để chạy tiếp.")


# --- Ops (đồng bộ, trong thread) --------------------------------------------------------------------------------------


def _summary(s: Session, seq: Sequence) -> dict[str, Any]:
    st = state.of(seq, "run")
    steps = st.get("steps") or []
    return {
        "sequence_id": seq.id, "number": seq.number, "title": seq.title, "workflow": st.get("workflow"),
        "status": st.get("status") or "idle", "job_id": st.get("job_id"), "current": current_step_name(st),
        "waiting": st.get("waiting"), "error": st.get("error"),
        "steps": [{"name": x["name"], "label": W.label(x["name"]), "enabled": x["enabled"], "status": x["status"],
                   "attempts": x.get("attempts", 0), "feedback": x.get("feedback") or "",
                   "warnings": x.get("warnings") or [], "error": x.get("error"),
                   "children": {k: v for k, v in (x.get("children") or {}).items() if k in ("jobs", "takes")}}
                  for x in steps],
        "gates": dict(seq.gates or {}),
        "progress": round(100.0 * sum(1 for x in steps if x["status"] in DONE_STEP) / len(steps), 1) if steps else 0,
        "release": st.get("release"), "updated_at": st.get("updated_at"),
    }


def start(ctx: OpContext, sequence_ids: list[int], workflow_key: str = W.DEFAULT_WORKFLOW,
          overrides: dict[str, Any] | None = None, *, restart: bool = False) -> dict[str, Any]:
    """Khởi chạy (hoặc tiếp tục) quy trình cho các tập. Tập đầu dẫn; các tập sau chờ được thả (lô)."""
    from sagareel.services import presets

    ids = list(dict.fromkeys(sequence_ids))
    if not ids:
        raise ValidationFailed("Cần ít nhất một tập.")
    ov = dict(overrides or {})
    presets.ensure_builtins(ctx.services)
    with ctx.services.db.session() as s:
        seqs = [entities.get(s, "sequence", i) for i in ids]
        projects = {q.project_id for q in seqs}
        if len(projects) != 1:
            raise ValidationFailed("Các tập phải thuộc cùng một dự án.")
        project_id = projects.pop()
        wf = W.load(s, workflow_key, project_id)
        steps = W.apply_overrides(wf, dict(ov.get("steps") or {}))
        existing = {q.id: state.of(q, "run") for q in seqs}
    settings_ = dict(ov.get("settings") or {})
    if settings_:
        bad = [k for k in settings_ if k not in ctx.services.resolver.schema]
        if bad:
            raise ValidationFailed(f"Khoá cài đặt lạ: {', '.join(bad)}.", code="unknown_setting")
        ctx.services.resolver.validate_values(settings_)
    params = dict(ov.get("params") or {})
    if ov.get("platforms"):
        params["platforms"] = list(ov["platforms"])
    pilot_on = any(x["step"] == "gate:pilot" and x["enabled"] for x in steps)
    batch = wf["batch"]
    if restart:  # chạy lại từ đầu: bỏ các quyết định duyệt cũ của quy trình này
        for sid in ids:
            for x in steps:
                if W.is_gate(x["step"]):
                    write_gate(ctx.services, sid, x["step"][5:], {"status": "pending", "at": now_iso(),
                                                                  "by": ctx.actor, "reset": True})
    resumed: list[int] = []
    fresh: list[int] = []
    for sid in ids:
        cur = existing[sid]
        if cur and cur.get("status") in ACTIVE and cur.get("status") != "pending_release" and not restart:
            if cur.get("workflow") != workflow_key:
                raise Conflict(f"Tập #{sid} đang chạy quy trình '{cur.get('workflow')}'.",
                               code="pipeline_running", hint="Huỷ (pipeline.cancel) hoặc truyền restart=true.")
            resumed.append(sid)
        else:
            fresh.append(sid)
    out_jobs = []
    if fresh:
        lead, followers = fresh[0], fresh[1:]
        # chạy lại tập dẫn (lỗi/huỷ/xong): các tập lô cũ còn chờ thả vẫn theo tập dẫn mới, không bị bỏ rơi
        prev_next = ((existing[lead] or {}).get("release") or {}).get("next") or []
        if prev_next:
            with ctx.services.db.session() as s:
                carried = [i for i in prev_next if i not in fresh
                           and load_state(s, i).get("status") == "pending_release"]
            followers = followers + carried
        lead_after = "gate:pilot" if pilot_on else batch["release_after"]
        for sid in fresh:
            is_lead = sid == lead
            if is_lead:
                release = {"after": lead_after, "next": followers, "mode": batch["mode"], "done": False,
                           "follower_after": batch["release_after"]} if followers else None
            else:
                release = None
            st = init_state(workflow_key, steps, settings=settings_, params=params,
                            genre_overrides=dict(ov.get("genre_overrides") or {}), pilot=is_lead and pilot_on,
                            batch_ids=[lead, *followers], release=release,
                            status="queued" if is_lead else "pending_release")
            save_state(ctx.services, sid, st, project_id=project_id)
        out_jobs.append(ensure_job(ctx.services, lead, delay_s=0))
    for sid in resumed:
        with ctx.services.db.session() as s:
            cur = load_state(s, sid)
        if cur.get("status") in ("waiting_input", "failed", "queued", "running"):
            if cur.get("status") == "waiting_input":
                cur["status"] = "queued"
                save_state(ctx.services, sid, cur, project_id=project_id)
            if cur.get("status") != "pending_release":
                out_jobs.append(ensure_job(ctx.services, sid, delay_s=0))
    with ctx.services.db.session() as s:
        summaries = [_summary(s, entities.get(s, "sequence", i)) for i in ids]
    return {"workflow": workflow_key, "jobs": out_jobs, "sequences": summaries, "resumed": resumed}


def plan(ctx: OpContext, sequence_ids: list[int], workflow_key: str, overrides: dict[str, Any] | None) -> dict:
    """`dry_run`: các bước sẽ chạy + ước tính số ảnh/video cần tạo (chưa tạo gì)."""
    from sagareel.services import presets

    presets.ensure_builtins(ctx.services)
    with ctx.services.db.session() as s:
        seqs = [entities.get(s, "sequence", i) for i in dict.fromkeys(sequence_ids)]
        project_id = seqs[0].project_id if seqs else None
        wf = W.load(s, workflow_key, project_id)
        steps = W.apply_overrides(wf, dict((overrides or {}).get("steps") or {}))
        enabled = [x["step"] for x in steps if x["enabled"]]
        per_clip_est, still_genre = _genre_clips(s, project_id, dict((overrides or {}).get("genre_overrides") or {}))
        unit_cost = {k: _unit_cost(s, k) for k in ("image", "video", "tts")}
        step_units: dict[str, dict[str, int]] = {x: {"images": 0, "videos": 0, "tts": 0, "renders": 0, "llm": 0}
                                                 for x in enabled}
        per = []
        totals: dict[str, Any] = {"images": 0, "videos": 0, "llm_calls": 0, "tts": 0, "renders": 0}
        estimated_any = False
        for q in seqs:
            rows = _clips(s, q.id)
            # Tập chưa chia clip: ước tính theo số clip/tập của thể loại (preset genre `clips`).
            estimated = not rows and "split_clips" in enabled and not satisfied(s, "split_clips", q.id)
            estimated_any = estimated_any or estimated
            n_est = per_clip_est if estimated else 0
            if "generate_images" in enabled:
                imgs = n_est or sum(1 for c in rows if needs_image(c) and c.image_take_id is None)
            else:
                imgs = 0
            if "generate_videos" in enabled:
                vids = (0 if still_genre else n_est) or sum(1 for c in rows if c.engine == "ai_video"
                                                            and c.video_take_id is None)
            else:
                vids = 0
            tts = 0
            if "generate_audio" in enabled:
                tts = n_est or int(s.scalar(select(func.count()).select_from(DialogueLine).join(
                    Clip, Clip.id == DialogueLine.clip_id).where(
                    Clip.sequence_id == q.id, Clip.deleted_at.is_(None), DialogueLine.deleted_at.is_(None),
                    DialogueLine.audio_take_id.is_(None))) or 0)
            llm_steps = [t for t in ("write_script", "split_clips", "image_prompts", "motion_prompts", "dialogue",
                                     "memory_summarize") if t in enabled and not satisfied(s, t, q.id)]
            renders = [t for t in ("render_animatic", "render_preview", "render") if t in enabled]
            for t in llm_steps:
                step_units[t]["llm"] += 1
            for t in renders:
                step_units[t]["renders"] += 1
            if imgs:
                step_units["generate_images"]["images"] += imgs
            if vids:
                step_units["generate_videos"]["videos"] += vids
            if tts:
                step_units["generate_audio"]["tts"] += tts
            per.append({"sequence_id": q.id, "clips": len(rows) or n_est, "clips_estimated": estimated,
                        "images": imgs, "videos": vids, "tts": tts, "renders": len(renders),
                        "llm_calls": len(llm_steps), "status": state.of(q, "run").get("status") or "idle"})
            totals["images"] += imgs
            totals["videos"] += vids
            totals["tts"] += tts
            totals["renders"] += len(renders)
            totals["llm_calls"] += len(llm_steps)
        step_out = []
        total_cost: float | None = 0.0
        for x in enabled:
            u = step_units[x]
            cost = None
            if u["images"] or u["videos"] or u["tts"]:
                parts = [(u["images"], unit_cost["image"]), (u["videos"], unit_cost["video"]),
                         (u["tts"], unit_cost["tts"])]
                cost = None if any(n and c is None for n, c in parts) else sum(n * (c or 0) for n, c in parts)
            elif not W.is_gate(x):
                cost = 0.0
            if cost is None and (u["images"] or u["videos"] or u["tts"]):
                total_cost = None
            elif total_cost is not None and cost:
                total_cost += cost
            step_out.append({"key": x, "label": W.label(x), "gate": W.is_gate(x),
                             "units": {k: u[k] for k in ("images", "videos", "tts", "renders")}, "llm_calls": u["llm"],
                             "cost": cost})
        totals["cost"] = total_cost
        totals["clips_estimated"] = estimated_any
    out = {"dry_run": True, "workflow": workflow_key, "steps": step_out, "step_keys": enabled, "sequences": per,
           "estimate": totals}
    if project_id is not None:  # cảnh báo prompt (tĩnh, không chặn): bước thiếu prompt sẽ dừng chờ nhập
        from sagareel.services import prompts

        with ctx.services.db.session() as s:
            pc = prompts.check_session(s, project_id)
        out["prompt_warnings"] = [w for w in pc["warnings"]
                                  if w["code"] != "prompt_missing" or any(st in enabled for st, ks in
                                                                          pc["steps_without_prompt"].items()
                                                                          if w["key"] in ks)]
    if estimated_any:
        out["note"] = (f"Tập chưa chia clip được ước tính theo thể loại (~{per_clip_est} clip/tập); số thật biết sau "
                       "bước Chia clip.")
    return out


def _genre_clips(s: Session, project_id: int | None, genre_overrides: dict[str, Any]) -> tuple[int, bool]:
    """(số clip/tập ước tính, thể loại chỉ dùng still_motion) theo số liệu thể loại của dự án (`project.genre`)."""
    from sagareel.db.models import Project

    p = s.get(Project, project_id) if project_id is not None else None
    genre = p.genre_key if p is not None else "series"
    data = {**(dict(p.genre or {}) if p is not None else {}), **genre_overrides}
    lo, hi = writer._span(data.get("clips"), (12, 16))
    return max(1, round((lo + hi) / 2)), genre in writer.STILL_GENRES


def _unit_cost(s: Session, kind: str) -> float | None:
    """Giá một lượt theo kết nối mặc định của loại (`options.cost_per_unit`); kết nối giả = 0; chưa biết = None."""
    from sagareel.db.models import ProviderConnection

    ref = writer.setting(s, f"{kind}.connection")
    q = select(ProviderConnection).where(ProviderConnection.kind == kind, ProviderConnection.deleted_at.is_(None))
    if ref:
        q = q.where(ProviderConnection.name == str(ref)) if not str(ref).isdigit() else \
            q.where(ProviderConnection.id == int(ref))
    conn = s.scalars(q.order_by(ProviderConnection.id).limit(1)).first()
    if conn is None:
        return None
    if conn.adapter.startswith("fake_"):
        return 0.0
    v = (conn.options or {}).get("cost_per_unit")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _get_run(s: Session, sequence_id: int) -> tuple[Sequence, dict[str, Any]]:
    seq = entities.get(s, "sequence", sequence_id)
    st = state.of(seq, "run")
    if not st:
        raise NotFound(f"Tập #{sequence_id} chưa chạy quy trình nào.", code="pipeline_missing",
                       hint="Gọi pipeline.run.")
    return seq, st


def _gate_index(st: dict[str, Any], gate: str) -> int:
    name = gate if gate.startswith("gate:") else f"gate:{gate}"
    idx = next((i for i, x in enumerate(st["steps"]) if x["name"] == name), None)
    if idx is None:
        raise NotFound(f"Quy trình '{st.get('workflow')}' không có điểm duyệt '{gate}'.", code="gate_unknown",
                       hint="Xem pipeline.status để biết các gate.")
    return idx


def approve(ctx: OpContext, sequence_id: int, gate: str, note: str = "") -> dict[str, Any]:
    gate = gate.removeprefix("gate:")
    with ctx.services.db.session() as s:
        seq, st = _get_run(s, sequence_id)
        project_id = seq.project_id
    idx = _gate_index(st, gate)
    write_gate(ctx.services, sequence_id, gate, {"status": "approved", "at": now_iso(), "by": ctx.actor,
                                                 "note": note})
    waiting = (st.get("waiting") or {}).get("gate") == gate and st.get("status") == "waiting_review"
    if waiting:
        step = st["steps"][idx]
        step["status"] = "done"
        step["result"] = {"approved_by": ctx.actor, "note": note}
        step["finished_at"] = now_iso()
        st["waiting"] = None
        st["status"] = "queued"
        save_state(ctx.services, sequence_id, st, project_id=project_id)
        ensure_job(ctx.services, sequence_id)
    else:  # duyệt trước: job đang chạy tới gate này thì kiểm lại thay vì dừng
        resume_if_waiting(ctx.services, sequence_id, statuses=())
    with ctx.services.db.session() as s:
        return {"gate": gate, "resumed": waiting, "pipeline": _summary(s, entities.get(s, "sequence", sequence_id))}


def reject(ctx: OpContext, sequence_id: int, gate: str, feedback: str) -> dict[str, Any]:
    gate = gate.removeprefix("gate:")
    if not (feedback or "").strip():
        raise ValidationFailed("Cần góp ý (feedback) khi từ chối.")
    with ctx.services.db.session() as s:
        seq, st = _get_run(s, sequence_id)
        project_id = seq.project_id
        prev = dict((seq.gates or {}).get(gate) or {})
    idx = _gate_index(st, gate)
    if not ((st.get("waiting") or {}).get("gate") == gate and st.get("status") == "waiting_review"):
        raise Conflict(f"Điểm duyệt '{gate}' không đang chờ duyệt.", code="gate_not_waiting",
                       hint="Chỉ từ chối được gate đang ở waiting_review (xem pipeline.status).")
    gstep = st["steps"][idx]
    rerun = list(gstep.get("rerun") or [])
    if not rerun:
        prev_step = next((x["name"] for x in reversed(st["steps"][:idx]) if x["enabled"] and not W.is_gate(x["name"])),
                         None)
        rerun = [prev_step] if prev_step else []
    if not rerun:
        raise Conflict("Không có bước nào trước gate để chạy lại.", code="gate_no_rerun")
    first = min(i for i, x in enumerate(st["steps"]) if x["name"] in rerun)
    for i in range(first, idx + 1):
        x = st["steps"][i]
        if W.is_gate(x["name"]) and i != idx:
            continue  # gate trước đó đã duyệt: giữ nguyên
        x["status"] = "pending"
        x["children"] = {}
        x["result"] = {}
        x["awaiting"] = False
        x.pop("error", None)
        if x["name"] in rerun:
            x["feedback"] = feedback
    write_gate(ctx.services, sequence_id, gate, {"status": "rejected", "at": now_iso(), "by": ctx.actor,
                                                 "feedback": feedback, "count": int(prev.get("count") or 0) + 1})
    st["waiting"] = None
    st["status"] = "queued"
    save_state(ctx.services, sequence_id, st, project_id=project_id)
    ensure_job(ctx.services, sequence_id)
    with ctx.services.db.session() as s:
        return {"gate": gate, "rerun": rerun, "pipeline": _summary(s, entities.get(s, "sequence", sequence_id))}


async def cancel(ctx: OpContext, sequence_id: int) -> dict[str, Any]:
    def mark() -> tuple[dict[str, Any], int]:
        with ctx.services.db.session() as s:
            seq, st = _get_run(s, sequence_id)
            return st, seq.project_id

    st, project_id = await _thread(mark)
    job_ids = [st["job_id"]] if st.get("job_id") else []
    for step in st["steps"]:
        job_ids += list((step.get("children") or {}).get("jobs") or [])
    cancelled = []
    worker = getattr(ctx.services, "worker", None)
    for jid in dict.fromkeys(job_ids):
        if worker is not None and worker.started:
            ok = await worker.cancel(jid)
        else:
            def idle(jid=jid) -> bool:
                with ctx.services.db.session() as s:
                    job = jobs.cancel_idle(s, jid)
                    data = jobs.job_dict(job) if job is not None else None
                if data is not None:
                    jobs.publish_updated(ctx.services, data)
                return data is not None

            ok = await _thread(idle)
        if ok:
            cancelled.append(jid)
    # ghi SAU khi job quy trình đã dừng (lần chạy đang dở không còn ghi đè trạng thái)
    st, _ = await _thread(mark)
    st["status"] = "cancelled"
    st["waiting"] = None
    await _thread(save_state, ctx.services, sequence_id, st, project_id=project_id)
    followers = []
    for fid in (st.get("release") or {}).get("next") or []:  # tập lô đang chờ tập dẫn này: huỷ theo
        def drop(fid=fid) -> bool:
            with ctx.services.db.session() as s:
                fst = load_state(s, fid)
            if fst.get("status") != "pending_release":
                return False
            fst["status"] = "cancelled"
            save_state(ctx.services, fid, fst, project_id=project_id)
            return True

        if await _thread(drop):
            followers.append(fid)
    return {"sequence_id": sequence_id, "status": "cancelled", "cancelled_jobs": cancelled,
            "cancelled_followers": followers}


def status(ctx: OpContext, *, sequence_ids: list[int] | None = None, project_id: int | None = None,
           cursor: int | None = None, limit: int = 200, active_only: bool = False,
           include_failed: bool = False) -> dict:
    """Trạng thái quy trình: theo `sequence_ids`, theo `project_id`, hoặc toàn cục (mọi dự án, phân trang theo id
    tập; `active_only` = chỉ tập có quy trình chưa xong, `include_failed` thêm cả quy trình lỗi). Gate đang chờ kèm `preview` (media dựng gần nhất)."""
    with ctx.services.db.session() as s:
        next_cursor = None
        if sequence_ids:
            rows = [entities.get(s, "sequence", i) for i in dict.fromkeys(sequence_ids)]
        elif project_id is not None:
            entities.get(s, "project", project_id)
            rows = list(s.scalars(select(Sequence).where(Sequence.project_id == project_id,
                                                         Sequence.deleted_at.is_(None))
                                  .order_by(Sequence.number, Sequence.id)))
        else:
            from sagareel.db.models import Project

            limit = max(1, min(int(limit), 500))
            q = (select(Sequence).join(Project, Project.id == Sequence.project_id)
                 .where(Sequence.deleted_at.is_(None), Project.deleted_at.is_(None)))
            if active_only:
                live = ("queued", "running", "waiting_review", "waiting_input", "pending_release")
                names = ", ".join(f"'{n}'" for n in (*live, "failed") if include_failed or n != "failed")
                q = q.where(text(f"json_extract(sequence.pipeline, '$.run.status') IN ({names})"))
            if cursor is not None:
                q = q.where(Sequence.id > cursor)
            rows = list(s.scalars(q.order_by(Sequence.id).limit(limit + 1)))
            if len(rows) > limit:
                rows = rows[:limit]
                next_cursor = rows[-1].id
        items = []
        for q in rows:
            d = _summary(s, q)
            d["project_id"] = q.project_id
            if d.get("status") == "waiting_review":
                d["preview"] = gate_preview(s, q.id)
            items.append(d)
        return {"items": items, "next_cursor": next_cursor}


def gate_preview(s: Session, sequence_id: int) -> dict[str, Any] | None:
    """Media xem trước cho gate đang chờ: bản dựng (animatic/final) mới nhất của tập, hoặc None."""
    from sagareel.db.models import Render

    r = s.scalars(select(Render).where(Render.sequence_id == sequence_id, Render.media_id.is_not(None),
                                       Render.status == "succeeded")
                  .order_by(Render.id.desc()).limit(1)).first()
    if r is None:
        return None
    return {"render_id": r.id, "variant": r.variant, "media_id": r.media_id,
            "url": f"/media/{r.media_id}"}


def sequence_status(ctx: OpContext, sequence_id: int) -> dict[str, Any]:
    """Tóm tắt cho agent (`sr_sequence_status`): quy trình + gate + clip/take + chi phí."""
    from sagareel.services import sequences as seq_svc

    with ctx.services.db.session() as s:
        seq = entities.get(s, "sequence", sequence_id)
        rows = _clips(s, sequence_id)
        summary = _summary(s, seq)
        active = s.scalar(select(func.count()).select_from(Job).where(
            Job.sequence_id == sequence_id, Job.status.in_(("queued", "running")))) or 0
    clips = {"total": len(rows), "with_image": sum(1 for c in rows if c.image_take_id),
             "with_video": sum(1 for c in rows if c.video_take_id),
             "image_prompt": sum(1 for c in rows if c.image_prompt.strip()),
             "motion_prompt": sum(1 for c in rows if c.motion_prompt.strip()),
             "approved": sum(1 for c in rows if c.review == "approved"),
             "flagged": sum(1 for c in rows if any(v for v in (c.flags or {}).values())),
             "seconds": round(sum(c.duration_ms for c in rows) / 1000, 1)}
    try:
        cost = seq_svc.cost_summary(ctx, sequence_id)
    except AppError as e:
        cost = {"error": e.code}
    return {"sequence_id": sequence_id, "number": seq.number, "title": seq.title,
            "has_script": bool((seq.script or "").strip()), "has_synopsis": bool((seq.synopsis or "").strip()),
            "pipeline": summary, "gates": summary["gates"], "clips": clips, "active_jobs": int(active), "cost": cost}
