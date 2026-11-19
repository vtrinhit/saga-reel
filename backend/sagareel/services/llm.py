"""Định tuyến LLM theo công việc (spec §7.3) và chạy một công việc LLM có chuỗi dự phòng.

- Công việc (`llm_task`) và template (`template`) là dữ liệu có lịch sử; bản mặc định nằm ở
  `defaults/llm_tasks.yaml` + `defaults/templates/<key>.md` (seed vào DB ở Task 15).
- `routes(session, task_key, override)` → danh sách `Route` theo thứ tự thử: route chính = override của lần chạy
  (nếu có kết nối) → `llm_task.route` → kết nối LLM đang bật đầu tiên; sau đó `fallbacks`. Công việc cần ảnh bỏ các
  kết nối không có vision. Không còn route nào → `AppError("llm_not_configured")` (409) kèm gợi ý hai cách viết khác.
- `run_task(...)` đọc cấu hình **mỗi lần gọi** (đổi model có hiệu lực ngay), render template, thử từng route: lỗi
  mọi lỗi phía provider (`TransientError`, `ProviderUnavailable`, `CapabilityError`, `ProviderJobError`/4xx như model
  không có) hoặc JSON hỏng (task `json_mode`) → route kế. Lỗi đầu vào của app (biến template thiếu, template hỏng,
  ảnh không đọc được) dừng ngay.
- Job `llm.run` (tạo bởi op `llm.run`) phát `llm.delta {job_id, text}` đã gộp (≤ 10 sự kiện/giây) và trả
  `{text, data, route, usage, model, attempts}`. Mỗi lần gọi LLM ghi một dòng `llm_call` vào nhật ký job.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core import templating
from sagareel.core.errors import AppError, NotFound
from sagareel.db.models import LlmTask, ProviderConnection, Template
from sagareel.providers import registry
from sagareel.providers.base import CapabilityError, Connection, LLMRequest, ProviderUnavailable
from sagareel.services import jobs, media
from sagareel.services.jobs import JobFailed, TransientError, job_handler
from sagareel.services.providers import to_connection

if TYPE_CHECKING:
    from sagareel.container import Services
    from sagareel.services.worker import JobRun

DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "defaults"
TASKS_PATH = DEFAULTS_DIR / "llm_tasks.yaml"
TEMPLATES_DIR = DEFAULTS_DIR / "templates"
TEMPLATE_KIND = "llm"
JOB_TYPE = "llm.run"
DELTA_EVENT = "llm.delta"
DELTA_INTERVAL_S = 0.1  # ≤ 10 sự kiện llm.delta mỗi giây cho một job

NOT_CONFIGURED_HINT = "Tự viết, nhập file, hoặc để agent ngoài viết qua MCP (sr_context_pack → sr_prompts_submit)"
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TASK_FIELDS = ("key", "label", "description", "template_key", "needs_vision", "json_mode")
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)\r?\n?```$", re.DOTALL)
FALLBACK_ERRORS = (TransientError, ProviderUnavailable, CapabilityError)
# Lỗi do dữ liệu vào của chính app (không phải lỗi provider): thử route khác cũng vô ích → dừng chuỗi dự phòng.
INPUT_ERRORS = frozenset({"llm_image_unreadable"})


# --- Bản mặc định ---------------------------------------------------------------------------------------------------


def default_tasks() -> list[dict[str, Any]]:
    """14 công việc mặc định từ `llm_tasks.yaml` (bản sao mới mỗi lần gọi)."""
    raw = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8")) or []
    tasks = []
    for item in raw:
        missing = [f for f in _TASK_FIELDS if f not in item]
        if missing:
            raise ValueError(f"llm_tasks.yaml: công việc {item.get('key')!r} thiếu {missing}")
        tasks.append({f: item[f] for f in _TASK_FIELDS})
    return tasks


def builtin_template(key: str) -> str | None:
    """Nội dung template cài sẵn `templates/<key>.md` (xuống dòng chuẩn hoá `\\n`), `None` nếu không có."""
    if not KEY_RE.match(key or ""):
        return None
    path = TEMPLATES_DIR / f"{key}.md"
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --- Kiểu dữ liệu ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    connection: str  # tên kết nối
    model: str | None = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"connection": self.connection, "model": self.model, "params": dict(self.params)}


@dataclass
class LLMOutcome:
    text: str
    data: dict | list | None
    route: Route
    attempts: list[dict]
    usage: dict
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "data": self.data, "route": self.route.to_dict(), "usage": dict(self.usage),
                "model": self.model, "attempts": list(self.attempts)}


@dataclass
class Plan:
    """Mọi thứ đọc từ DB cho một lần chạy (đọc trong thread, dùng ngoài transaction)."""

    task_key: str
    template_key: str
    json_mode: bool
    needs_vision: bool
    messages: list[dict]
    candidates: list[tuple[Route, Connection]]


# --- Đọc DB ---------------------------------------------------------------------------------------------------------


def get_task(session: Session, key: str) -> LlmTask:
    row = session.scalars(select(LlmTask).where(LlmTask.key == key, LlmTask.deleted_at.is_(None))).first()
    if row is None:
        raise NotFound(f"Không có công việc LLM '{key}'.", code="llm_task_not_found",
                       hint="Gọi llm.task_list để xem các công việc LLM.")
    return row


def get_template(session: Session, key: str) -> Template:
    row = session.scalars(select(Template).where(Template.key == key, Template.deleted_at.is_(None))).first()
    if row is None:
        raise NotFound(f"Không có template '{key}'.", code="template_not_found",
                       hint="Gọi template.list để xem các template.")
    return row


def normalize_route(raw: Any) -> dict[str, Any]:
    """Route lưu trong DB/override → `{connection: str|None, model: str|None, params: dict}` (dữ liệu hỏng → rỗng)."""
    if not isinstance(raw, dict):
        return {"connection": None, "model": None, "params": {}}
    conn = raw.get("connection")
    model = raw.get("model")
    params = raw.get("params")
    return {
        "connection": str(conn) if conn not in (None, "") else None,
        "model": str(model) if model not in (None, "") else None,
        "params": dict(params) if isinstance(params, dict) else {},
    }


def find_connection(session: Session, name_or_id: str | int) -> ProviderConnection | None:
    """Kết nối chưa xoá theo tên (hoặc id dạng số), không có → None."""
    row = session.scalars(select(ProviderConnection).where(
        ProviderConnection.name == str(name_or_id), ProviderConnection.deleted_at.is_(None))).first()
    if row is None and str(name_or_id).isdigit():
        row = session.get(ProviderConnection, int(name_or_id))
    if row is None or row.deleted_at is not None:
        return None
    return row


def supports_vision(row: ProviderConnection) -> bool:
    """`options.vision` của kết nối, mặc định theo `default_capabilities` của adapter (không khai báo → coi là có)."""
    try:
        caps = registry.get(row.adapter).default_capabilities
    except AppError:
        caps = {}
    value = (row.options or {}).get("vision", caps.get("vision", True))
    return value is not False


def not_configured(task_key: str, reasons: list[str], need_vision: bool) -> AppError:
    what = "kết nối LLM đọc được ảnh (vision)" if need_vision else "kết nối LLM nào dùng được"
    detail = f"Chưa có {what} cho công việc '{task_key}'."
    if reasons:
        detail += " " + " ".join(reasons)
    return AppError("llm_not_configured", detail, hint=NOT_CONFIGURED_HINT, status=409,
                    extra={"task": task_key, "skipped": reasons, "alternatives": ["manual", "agent"]})


def _first_enabled(session: Session, need_vision: bool) -> ProviderConnection | None:
    rows = session.scalars(select(ProviderConnection).where(
        ProviderConnection.kind == "llm", ProviderConnection.enabled.is_(True),
        ProviderConnection.deleted_at.is_(None)).order_by(ProviderConnection.id)).all()
    return next((r for r in rows if not need_vision or supports_vision(r)), None)


def _candidates(
    session: Session, task: LlmTask, override: dict | None, need_vision: bool,
) -> list[tuple[Route, ProviderConnection]]:
    reasons: list[str] = []
    primary = normalize_route(task.route)
    explicit = False  # route chính do override của lần chạy chỉ định kết nối
    chain: list[tuple[dict, str]] = []  # (route, nguồn)
    if override:
        ov = normalize_route(override)
        if ov["connection"] is not None:
            row = find_connection(session, ov["connection"])
            if row is None:
                raise NotFound(f"Không tìm thấy kết nối provider '{ov['connection']}'.", code="connection_not_found",
                               hint="Gọi provider.list kind=llm để xem các kết nối LLM.")
            if row.kind != "llm":
                raise AppError("capability_mismatch", f"Kết nối '{row.name}' là loại {row.kind}, không phải LLM.",
                               hint="Chọn một kết nối loại llm.", status=422)
            primary = ov
            explicit = True
        else:  # override chỉ đổi model/tham số của route chính
            primary = {"connection": primary["connection"],
                       "model": ov["model"] if ov["model"] is not None else primary["model"],
                       "params": {**primary["params"], **ov["params"]}}
    if primary["connection"] is not None:
        chain.append((primary, "route chính"))
    else:
        first = _first_enabled(session, need_vision)
        if first is not None:
            chain.append(({**primary, "connection": first.name}, "kết nối mặc định"))
    chain += [(normalize_route(f), "dự phòng") for f in (task.fallbacks or [])]

    out: list[tuple[Route, ProviderConnection]] = []
    seen: set[tuple[str, str | None]] = set()
    for spec, source in chain:
        if spec["connection"] is None:
            continue
        row = find_connection(session, spec["connection"])
        if row is None:
            reasons.append(f"Kết nối '{spec['connection']}' ({source}) không còn.")
            continue
        if row.kind != "llm":
            reasons.append(f"Kết nối '{row.name}' ({source}) không phải LLM.")
            continue
        if not row.enabled:
            reasons.append(f"Kết nối '{row.name}' ({source}) đang tắt.")
            continue
        if need_vision and not supports_vision(row):
            reasons.append(f"Kết nối '{row.name}' ({source}) không đọc được ảnh.")
            continue
        route = Route(connection=row.name, model=spec["model"], params=spec["params"])
        ident = (route.connection, route.model)  # thử lại cùng kết nối + model không giúp gì cho dự phòng
        if ident in seen:
            continue
        seen.add(ident)
        out.append((route, row))
    if not out and not explicit and primary["connection"] is not None:
        # Kết nối đã chọn cho công việc đang tắt/đã xoá/không đọc được ảnh và không còn dự phòng nào dùng được:
        # dùng kết nối LLM đang bật đầu tiên (model mặc định của nó) thay vì chặn người dùng.
        first = _first_enabled(session, need_vision)
        if first is not None:
            out.append((Route(connection=first.name, model=None, params=primary["params"]), first))
    if not out:
        raise not_configured(task.key, reasons, need_vision)
    return out


def routes(session: Session, task_key: str, override: dict | None = None, *, images: bool = False) -> list[Route]:
    """Các route sẽ thử cho `task_key`, theo thứ tự. Không có → `llm_not_configured`."""
    task = get_task(session, task_key)
    return [r for r, _ in _candidates(session, task, override, task.needs_vision or images)]


def plan(session: Session, task_key: str, variables: dict, override: dict | None = None, *,
         images: bool = False) -> Plan:
    """Đọc công việc + template + route, render tin nhắn. Lỗi cấu hình/biến thiếu ném `AppError` ngay."""
    task = get_task(session, task_key)
    need_vision = bool(task.needs_vision or images)
    candidates = _candidates(session, task, override, need_vision)
    template = get_template(session, task.template_key)
    messages = templating.render_messages(template.body, variables)
    return Plan(task_key=task.key, template_key=task.template_key, json_mode=bool(task.json_mode),
                needs_vision=need_vision, messages=messages,
                candidates=[(route, to_connection(row)) for route, row in candidates])


# --- JSON ---------------------------------------------------------------------------------------------------------


def parse_json(text: str) -> dict | list:
    """JSON từ câu trả lời LLM: bỏ code fence ```json …```; nếu còn chữ thừa thì lấy đoạn `{…}`/`[…]` ngoài cùng."""
    s = text.strip()
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    try:
        value = json.loads(s)
    except ValueError:
        starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
        end = max(s.rfind("}"), s.rfind("]"))
        if not starts or end <= min(starts):
            raise ValueError("Không tìm thấy JSON trong câu trả lời") from None
        value = json.loads(s[min(starts):end + 1])
    if not isinstance(value, dict | list):
        raise ValueError("JSON không phải object/array")
    return value


# --- Chạy -----------------------------------------------------------------------------------------------------------


def _plan_sync(services: Services, task_key: str, variables: dict, override: dict | None, images: bool) -> Plan:
    with services.db.session() as s:
        return plan(s, task_key, variables, override, images=images)


def _attempt(route: Route, *, ok: bool, started: float, model: str | None = None, usage: dict | None = None,
             error_code: str | None = None, error: str | None = None) -> dict:
    out: dict[str, Any] = {"connection": route.connection, "model": model if model is not None else route.model,
                           "ok": ok, "ms": int((time.monotonic() - started) * 1000)}
    if usage is not None:
        out["usage"] = dict(usage)
    if error_code is not None:
        out["error_code"] = error_code
        out["error"] = error
    return out


def _error_code(e: Exception) -> str:
    if isinstance(e, TransientError):
        return "transient"
    return getattr(e, "code", type(e).__name__)


def _call_line(task_key: str, a: dict) -> str:
    base = f"llm_call task={task_key} connection={a['connection']} model={a.get('model') or '-'} ms={a['ms']}"
    if a["ok"]:
        usage = a.get("usage") or {}
        tokens = " ".join(f"{k}={v}" for k, v in usage.items() if isinstance(v, int | float))
        return f"{base} ok {tokens}".rstrip()
    return f"{base} lỗi {a.get('error_code')}: {a.get('error')}"


async def run_task(
    services: Services,
    task_key: str,
    variables: dict,
    *,
    images: Sequence[Path] = (),
    override: dict | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_attempt: Callable[[int, Route], None] | None = None,
    log: Callable[[str, str], None] | None = None,
) -> LLMOutcome:
    """Chạy công việc LLM `task_key` với `variables`; thử lần lượt các route. Cấu hình đọc lại ở mỗi lần gọi.

    `on_attempt(i, route)` được gọi trước mỗi lần thử (i > 0 = chuyển sang dự phòng: chữ đã stream của lần trước bỏ
    đi). `log(level, msg)` nhận một dòng `llm_call` cho mỗi lần gọi. Mọi route thất bại → `TransientError` (nếu có
    lỗi tạm thời, job thử lại sau) hoặc `JobFailed`.
    """
    images = list(images)
    p = await anyio.to_thread.run_sync(_plan_sync, services, task_key, dict(variables), override, bool(images))
    attempts: list[dict] = []

    def record(a: dict) -> None:
        attempts.append(a)
        if log is not None:
            log("info" if a["ok"] else "warning", _call_line(p.task_key, a))

    for i, (route, conn) in enumerate(p.candidates):
        if on_attempt is not None:
            on_attempt(i, route)
        started = time.monotonic()
        try:
            adapter = registry.get(conn.adapter)(conn)
            req = LLMRequest(messages=[dict(m) for m in p.messages], model=route.model,
                             params={**route.params, "template_key": p.template_key}, json_mode=p.json_mode,
                             images=list(images))
            result = await adapter.chat(req, on_delta)
        except FALLBACK_ERRORS as e:
            record(_attempt(route, ok=False, started=started, error_code=_error_code(e), error=e.msg))
            continue
        except JobFailed as e:  # provider từ chối (4xx, model không có…) → thử dự phòng; lỗi đầu vào của app → dừng
            record(_attempt(route, ok=False, started=started, error_code=e.code, error=e.msg))
            if e.code in INPUT_ERRORS:
                raise
            continue
        except AppError as e:  # vd. adapter không còn được đăng ký
            if e.code != "unknown_adapter":
                raise
            record(_attempt(route, ok=False, started=started, error_code=e.code, error=e.detail))
            continue
        data = None
        if p.json_mode:
            try:
                data = parse_json(result.text)
            except ValueError as e:
                record(_attempt(route, ok=False, started=started, model=result.model, usage=result.usage,
                                error_code="llm_invalid_json", error=f"Câu trả lời không phải JSON hợp lệ: {e}"))
                continue
        record(_attempt(route, ok=True, started=started, model=result.model, usage=result.usage))
        return LLMOutcome(text=result.text, data=data, route=route, attempts=attempts, usage=dict(result.usage),
                          model=result.model)

    summary = "; ".join(f"{a['connection']}: {a.get('error')}" for a in attempts)
    if any(a.get("error_code") == "transient" for a in attempts):
        raise TransientError(f"Mọi kết nối LLM cho '{p.task_key}' đều lỗi ({summary}).")
    if attempts and all(a.get("error_code") == "llm_invalid_json" for a in attempts):
        raise JobFailed("llm_invalid_json", f"LLM không trả JSON hợp lệ cho '{p.task_key}' ({summary}).",
                        hint="Thử model khác, hoặc sửa template để nhắc rõ định dạng JSON.")
    raise JobFailed("llm_all_routes_failed", f"Mọi kết nối LLM cho '{p.task_key}' đều thất bại ({summary}).",
                    hint="Kiểm tra kết nối LLM (provider.check) hoặc thêm kết nối dự phòng cho công việc.")


# --- Job llm.run ----------------------------------------------------------------------------------------------------


class DeltaThrottle:
    """Gộp các đoạn chữ, gọi `emit(text)` tối đa một lần mỗi `interval` giây (trên event loop hiện tại)."""

    def __init__(self, emit: Callable[[str], None], interval: float = DELTA_INTERVAL_S) -> None:
        self._emit = emit
        self.interval = interval
        self._buf: list[str] = []
        self._last = -math.inf
        self._handle: asyncio.TimerHandle | None = None
        self._loop = asyncio.get_running_loop()

    def push(self, text: str) -> None:
        if not text:
            return
        self._buf.append(text)
        if self._handle is not None:
            return
        delay = self._last + self.interval - time.monotonic()
        if delay <= 0:
            self.flush()
        else:
            self._handle = self._loop.call_later(delay, self._on_timer)

    def _on_timer(self) -> None:
        self._handle = None
        self.flush()

    def flush(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf.clear()
        self._last = time.monotonic()
        self._emit(text)


def _job_inputs(services: Services, job_id: int, media_ids: list[int]) -> tuple[int | None, list[Path]]:
    with services.db.session() as s:
        project_id = jobs.get_job(s, job_id).project_id
        paths = []
        for mid in media_ids:
            m = media.get(s, mid)
            if m.kind != "image":
                raise CapabilityError(f"Media #{mid} là {m.kind}, LLM chỉ nhận ảnh.")
            paths.append(media.file_path(services.settings, m))
        return project_id, paths


@job_handler(JOB_TYPE)
async def llm_run_job(run: JobRun) -> dict:
    p = run.params
    services = run.services
    project_id, images = await anyio.to_thread.run_sync(
        _job_inputs, services, run.job_id, list(p.get("images_media_ids") or []))

    def emit(text: str, **extra: Any) -> None:
        services.bus.publish(DELTA_EVENT, {"job_id": run.job_id, "task": p["task"], "text": text, **extra},
                             project_id=project_id)

    throttle = DeltaThrottle(emit)
    received = 0
    if run.attempt > 0:  # worker chạy lại job (lỗi tạm thời): chữ đã stream ở lần trước bỏ đi
        emit("", reset=True, attempt=0, job_attempt=run.attempt)

    def on_delta(chunk: str) -> None:
        nonlocal received
        received += len(chunk)
        throttle.push(chunk)
        run.progress(None, stage=f"Đang nhận chữ ({received} ký tự)")

    def on_attempt(i: int, route: Route) -> None:
        nonlocal received
        if i > 0:
            throttle.flush()
            received = 0
            emit("", reset=True, attempt=i)  # chữ của lần thử trước bị bỏ
            run.log("warning", f"Chuyển sang kết nối dự phòng '{route.connection}' (lần thử {i + 1}).")
        run.progress(None, stage=f"Đang gọi LLM ({route.connection})")

    try:
        outcome = await run_task(services, p["task"], dict(p.get("variables") or {}), images=images,
                                 override=p.get("override"), on_delta=on_delta, on_attempt=on_attempt, log=run.log)
    finally:
        throttle.flush()
    return json.loads(json.dumps(outcome.to_dict(), default=str))


def submit_run(services: Services, task_key: str, variables: dict, *, override: dict | None,
               images_media_ids: list[int], project_id: int | None) -> dict:
    """Kiểm tra cấu hình/biến/ảnh rồi tạo job `llm.run` (đồng bộ, gọi trong thread). Trả `job_dict`."""
    with services.db.session() as s:
        for mid in images_media_ids:
            m = media.get(s, mid)
            if m.kind != "image":
                raise AppError("media_kind_mismatch", f"Media #{mid} là {m.kind}, LLM chỉ nhận ảnh.", status=422)
        p = plan(s, task_key, variables, override, images=bool(images_media_ids))
    route, conn = p.candidates[0]
    params = {"task": task_key, "variables": variables, "override": override, "images_media_ids": images_media_ids}
    estimate = {"kind": "llm", "units": 1, "task": task_key, "route": route.to_dict(), "connection_id": conn.id,
                "routes": len(p.candidates)}
    return jobs.submit(services, JOB_TYPE, params, lane=f"conn:{conn.id}", project_id=project_id,
                       cost_estimate=estimate)


__all__ = [
    "DELTA_EVENT", "JOB_TYPE", "NOT_CONFIGURED_HINT", "DeltaThrottle", "LLMOutcome", "Plan", "Route", "body_hash",
    "builtin_template", "default_tasks", "find_connection", "get_task", "get_template", "normalize_route",
    "parse_json", "plan", "routes", "run_task", "submit_run", "supports_vision",
]
