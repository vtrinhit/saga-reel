"""Hàng đợi job bền trong SQLite (spec §5.5): trạng thái, lỗi điều khiển worker, registry handler, thao tác trên DB.

Dòng `job`/`job_log` là dữ liệu sổ sách của máy, không phải dữ liệu người dùng: ghi bằng transaction ngắn của
`db.session()` (không UnitOfWork, không revision). Hàm ở đây là đồng bộ và nhận `Session`; code async gọi chúng
qua `anyio.to_thread.run_sync`.

Chống tạo trùng job tốn tiền:
- `enqueue(..., client_ref=...)` trả lại job **chưa kết thúc** cùng `client_ref` thay vì tạo mới (chỉ an toàn khi
  gọi trong phiên giữ khoá ghi — `db.session(immediate=True)` hoặc session của UnitOfWork; `submit` làm sẵn).
- Retry tạm thời, khôi phục sau khởi động lại và tắt app đều **giữ** `external_id`/`external_state`; `retry_job` (op
  `job.retry`) cũng giữ khi đã có `external_id`, trừ khi `force_new=True` (tạo job trả phí mới).
- Dấu `submitting` chưa có `external_id` (không rõ provider đã nhận POST chưa): worker không bao giờ chạy lại job đó
  (mọi kết quả không thành công, job `queued` mang dấu, khôi phục/tắt app → `needs_attention`/`job_unknown`, giữ dấu).
  Chỉ `clear_attention` (`resubmit` hoặc `fail`) mới xoá dấu; `retry_job` từ chối job mang dấu và job `needs_attention`.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError, Conflict, NotFound
from sagareel.db.base import utcnow
from sagareel.db.models import Job, JobLog
from sagareel.services import system_state

if TYPE_CHECKING:
    from sagareel.container import Services
    from sagareel.services.worker import JobRun

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE = frozenset({"queued", "running"})  # còn tự tiến triển mà không cần ai làm gì
STATUSES = TERMINAL | ACTIVE | {"waiting_review", "waiting_input", "paused", "interrupted", "needs_attention"}
# Trạng thái được "chạy lại" (đặt lại thành queued). needs_attention đi qua `clear_attention`.
RETRYABLE = frozenset({"succeeded", "failed", "cancelled", "interrupted"})

PAUSED_KEY = "jobs_paused"

# Dấu "đang gửi tới provider": adapter ghi `save_external(state="submitting")` ngay trước POST tốn tiền.
SUBMIT_MARKER_KEY, SUBMIT_MARKER = "state", "submitting"
JOB_UNKNOWN_HINT = (
    "Kiểm tra phía provider xem job đã được tạo chưa, rồi dùng job.clear_attention: "
    "“resubmit” để xoá đánh dấu và gửi lại, hoặc “fail” để bỏ."
)


# Gợi ý cho lỗi xảy ra SAU KHI provider đã nhận job (có external_id): `job.retry` thường chỉ nối lại đúng job đó.
FORCE_NEW_HINT = (
    "Provider đã nhận job này: “Chạy lại” thường chỉ kiểm tra lại đúng job đó (không tốn thêm); muốn tạo lại hoặc đổi "
    "prompt/tham số thì dùng job.retry với force_new=true (tạo job trả phí mới)."
)


def job_unknown_values(detail: str) -> dict[str, Any]:
    """Giá trị cột đưa job mang dấu `submitting` sang `needs_attention` (giữ nguyên dấu và `external_state`)."""
    return {
        "status": "needs_attention",
        "error_code": "job_unknown",
        "error_message": f"{detail} Không rõ provider đã nhận job hay chưa.",
        "hint": JOB_UNKNOWN_HINT,
        "next_attempt_at": None,
    }


def submit_marked() -> Any:
    """Biểu thức SQL: dòng job mang dấu `submitting` mà chưa có `external_id`.

    Luôn TRUE/FALSE, không bao giờ NULL (coalesce) — để `~submit_marked()` không loại nhầm dòng không có khoá `state`.
    """
    return Job.external_id.is_(None) & (
        func.coalesce(func.json_extract(Job.external_state, f"$.{SUBMIT_MARKER_KEY}"), "") == SUBMIT_MARKER
    )


def drop_submit_marker(external_state: dict | None) -> dict:
    """`external_state` bỏ dấu `submitting` (gửi đã có kết quả rõ ràng)."""
    state = dict(external_state or {})
    if state.get(SUBMIT_MARKER_KEY) == SUBMIT_MARKER:
        del state[SUBMIT_MARKER_KEY]
    return state


GEN_KEY = "gen"  # thế hệ gửi của job: chỉ tăng (force_new, clear_attention resubmit), giữ qua mọi lần đặt lại
CLIENT_REF_KEY = "client_ref"  # ref chống trùng adapter đã gửi (MultiKit); giữ khi resubmit job chỉ mang dấu
# `job.retry` nối lại một job NGƯỜI DÙNG đã huỷ: adapter thấy job phía provider đã bị huỷ thì báo đúng nguyên nhân
# (huỷ trước đó, gợi ý force_new) thay vì "bị huỷ khi SagaReel không chạy".
USER_CANCELLED_KEY = "user_cancelled"


def provider_holds(external_id: str | None, external_state: dict | None) -> bool:
    """Provider đã giữ (và đã/sẽ tính tiền) job: có `external_id`, hoặc `external_state.state` khác None/`submitting`
    (vd. `submitted`; adapter đồng bộ như openai_images ghi `completed` + `result_file` mà không có id)."""
    return bool(external_id) or (external_state or {}).get(SUBMIT_MARKER_KEY) not in (None, SUBMIT_MARKER)


def provider_holds_job(job: Job) -> bool:
    return provider_holds(job.external_id, job.external_state)


# Job đã dừng (không phải succeeded) còn mang dấu submitting — vd. bị huỷ đúng lúc đang gửi.
_MARKED_IDLE = frozenset({"failed", "cancelled", "interrupted"})


def needs_decision(status: str, external_id: str | None, external_state: dict | None) -> bool:
    """Job đã dừng (thất bại/huỷ/gián đoạn) còn dấu `submitting` chưa có `external_id`: `job.retry` từ chối, chỉ
    `job.clear_attention` (resubmit/fail) xử lý được. Job `needs_attention` đã tự nói lên điều đó qua `status`."""
    return status in _MARKED_IDLE and has_submit_marker(external_id, external_state)


def derived_flags(status: str, external_id: str | None, external_state: dict | None) -> dict[str, bool]:
    """Hai cờ suy ra cho UI/agent (có trong `job_dict` và mọi sự kiện `job.updated`)."""
    return {
        "provider_holds": provider_holds(external_id, external_state),
        "needs_decision": needs_decision(status, external_id, external_state),
    }


def generation(external_state: dict | None) -> int:
    try:
        return max(0, int((external_state or {}).get(GEN_KEY) or 0))
    except (TypeError, ValueError):
        return 0


def _gen_state(gen: int) -> dict:
    return {GEN_KEY: gen} if gen else {}


def has_submit_marker(external_id: str | None, external_state: dict | None) -> bool:
    """Đã đánh dấu `submitting` mà chưa có `external_id` → không biết provider đã nhận job hay chưa."""
    return not external_id and (external_state or {}).get(SUBMIT_MARKER_KEY) == SUBMIT_MARKER


# --- Lỗi điều khiển worker ------------------------------------------------------------------------------------------


class TransientError(Exception):
    """Lỗi tạm thời (429/502/503/timeout/mạng): worker đưa job về `queued` và thử lại sau backoff.

    `retry_after` (giây) tôn trọng `Retry-After`/`retryAt` của provider; `0` nghĩa là thử lại ngay.
    """

    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.msg = msg
        self.retry_after = retry_after


class JobFailed(Exception):
    """Job thất bại có chủ đích: worker ghi `failed` với đúng `code`/`msg`/`hint` của instance (lớp con dùng mã riêng)."""

    def __init__(self, code: str, msg: str, hint: str | None = None) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.hint = hint


class NeedsAttention(Exception):
    """Cần người dùng/agent quyết định (vd. đã đánh dấu `submitting` nhưng không có `external_id`) → `needs_attention`."""

    def __init__(self, code: str, msg: str, hint: str | None = None) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.hint = hint


class JobStateLost(Exception):
    """`save_external` không ghi được vì job không còn `running` (đã bị huỷ/đặt lại bên ngoài).

    Handler **không** được đi tiếp (vd. gửi POST tốn tiền) khi gặp lỗi này.
    """


# --- Registry handler ---------------------------------------------------------------------------------------------

Handler = Callable[["JobRun"], Awaitable[dict | None]]


@dataclass(frozen=True)
class HandlerSpec:
    type: str
    fn: Handler
    resumable: bool


HANDLERS: dict[str, HandlerSpec] = {}


def job_handler(type: str, *, resumable: bool = False) -> Callable[[Handler], Handler]:  # noqa: A002 — tên theo interface
    """Đăng ký `async def h(run: JobRun) -> dict` cho loại job `type`.

    `resumable=True`: handler nối lại được sau khi app khởi động lại (dựa vào `run.external_id`/`run.external`
    hoặc `client_ref` ổn định) → job `running` bị bỏ dở được đưa về `queued` thay vì `interrupted`.
    """

    def decorator(fn: Handler) -> Handler:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"Handler job {type!r} phải là async def")
        if type in HANDLERS:
            raise ValueError(f"Handler job {type!r} đã được đăng ký")
        HANDLERS[type] = HandlerSpec(type=type, fn=fn, resumable=resumable)
        return fn

    return decorator


def unregister_handler(type: str) -> None:  # noqa: A002
    HANDLERS.pop(type, None)


def get_handler(type: str) -> HandlerSpec | None:  # noqa: A002
    return HANDLERS.get(type)


# --- Serialize ----------------------------------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def job_dict(job: Job) -> dict[str, Any]:
    """Hình JSON của một job (op trả về, payload sự kiện `job.updated`).

    Thêm hai cờ suy ra (`derived_flags`): `provider_holds` (provider đã giữ job → `job.retry` nối lại, muốn tạo lại
    phải `force_new`) và `needs_decision` (job đã dừng còn dấu submitting → chỉ `job.clear_attention`).
    """
    return {
        **derived_flags(job.status, job.external_id, job.external_state),
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "priority": job.priority,
        "lane": job.lane,
        "project_id": job.project_id,
        "sequence_id": job.sequence_id,
        "clip_id": job.clip_id,
        "parent_id": job.parent_id,
        "params": dict(job.params or {}),
        "progress": job.progress,
        "stage": job.stage,
        "eta_at": _iso(job.eta_at),
        "client_ref": job.client_ref,
        "external_id": job.external_id,
        "external_state": dict(job.external_state or {}),
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "next_attempt_at": _iso(job.next_attempt_at),
        "error_code": job.error_code,
        "error_message": job.error_message,
        "hint": job.hint,
        "result": dict(job.result or {}),
        "cost_estimate": dict(job.cost_estimate or {}),
        "cost_actual": dict(job.cost_actual or {}),
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "updated_at": _iso(job.updated_at),
    }


def log_dict(row: JobLog) -> dict[str, Any]:
    return {"id": row.id, "job_id": row.job_id, "level": row.level, "message": row.message, "at": _iso(row.created_at)}


# --- Tạo job ------------------------------------------------------------------------------------------------------


def enqueue(
    session: Session,
    type: str,  # noqa: A002
    params: dict,
    *,
    lane: str,
    priority: int = 0,
    project_id: int | None = None,
    sequence_id: int | None = None,
    clip_id: int | None = None,
    parent_id: int | None = None,
    client_ref: str | None = None,
    max_attempts: int = 3,
    cost_estimate: dict | None = None,
) -> Job:
    """Thêm job `queued`. Có job chưa kết thúc cùng `client_ref` → trả job đó (idempotent).

    Kiểm tra-rồi-chèn chỉ idempotent khi phiên giữ khoá ghi (`db.session(immediate=True)` / UnitOfWork). Người gọi
    commit; sau commit nên phát `job.updated` và `worker.wake()` (xem `submit`). Không gọi thì worker vẫn thấy job ở
    nhịp tick kế tiếp.
    """
    if client_ref is not None:
        existing = session.scalars(
            select(Job).where(Job.client_ref == client_ref, Job.status.not_in(TERMINAL)).order_by(Job.id).limit(1)
        ).first()
        if existing is not None:
            return existing
    job = Job(
        type=type,
        status="queued",
        priority=priority,
        lane=lane,
        project_id=project_id,
        sequence_id=sequence_id,
        clip_id=clip_id,
        parent_id=parent_id,
        params=dict(params),
        client_ref=client_ref,
        max_attempts=max_attempts,
        cost_estimate=dict(cost_estimate or {}),
    )
    session.add(job)
    session.flush()
    return job


def notify(services: Services, data: dict) -> None:
    """Phát `job.updated` cho một job đã commit và đánh thức worker (gọi được từ thread bất kỳ)."""
    services.bus.publish("job.updated", data, project_id=data.get("project_id"))
    worker = getattr(services, "worker", None)
    if worker is not None:
        worker.wake()


def submit(services: Services, type: str, params: dict, **kw: Any) -> dict:  # noqa: A002
    """`enqueue` trong phiên khoá ghi riêng, commit, phát sự kiện, đánh thức worker. Trả `job_dict`. Đồng bộ."""
    with services.db.session(immediate=True) as s:
        data = job_dict(enqueue(s, type, params, **kw))
    notify(services, data)
    return data


# --- Đọc ----------------------------------------------------------------------------------------------------------


def not_found(job_id: int) -> NotFound:
    return NotFound(f"Không tìm thấy job #{job_id}.", hint="Gọi job.list để xem các job hiện có.")


def get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise not_found(job_id)
    return job


def list_jobs(
    session: Session,
    *,
    status: str | None = None,
    type: str | None = None,  # noqa: A002
    lane: str | None = None,
    project_id: int | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> tuple[list[Job], int | None]:
    """Job mới nhất trước (`id` giảm dần). `cursor` = `next_cursor` của trang trước (lấy các job có id nhỏ hơn)."""
    q = select(Job)
    if status is not None:
        q = q.where(Job.status == status)
    if type is not None:
        q = q.where(Job.type == type)
    if lane is not None:
        q = q.where(Job.lane == lane)
    if project_id is not None:
        q = q.where(Job.project_id == project_id)
    if cursor is not None:
        q = q.where(Job.id < cursor)
    rows = list(session.scalars(q.order_by(Job.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (rows[-1].id if more and rows else None)


def job_logs(session: Session, job_id: int, *, after_id: int | None = None, limit: int = 200) -> list[JobLog]:
    get_job(session, job_id)
    q = select(JobLog).where(JobLog.job_id == job_id)
    if after_id is not None:
        q = q.where(JobLog.id > after_id)
    return list(session.scalars(q.order_by(JobLog.id).limit(limit)))


def add_log(session: Session, job_id: int, level: str, message: str) -> JobLog:
    row = JobLog(job_id=job_id, level=level, message=message)
    session.add(row)
    session.flush()
    return row


# --- Thao tác của người dùng/agent ----------------------------------------------------------------------------------


# Khoá `params` không bao giờ sửa được bằng `params_patch`: đổi kết nối = job khác (lane, ngân sách, provider khác).
_FROZEN_PARAMS = frozenset({"connection_id"})


def _check_patch(job: Job, params_patch: dict | None) -> None:
    """`params_patch` chỉ được sửa tham số job đã có (trừ `connection_id`) → ngược lại 422 `params_patch_invalid`."""
    if not params_patch:
        return
    known = set(job.params or {})
    bad = sorted(k for k in params_patch if k in _FROZEN_PARAMS or k not in known)
    if bad:
        frozen = [k for k in bad if k in _FROZEN_PARAMS]
        raise AppError(
            "params_patch_invalid",
            f"Không sửa được tham số {', '.join(bad)} của job #{job.id}"
            + (" (đổi kết nối)." if frozen else " (job không có tham số này)."),
            hint="params_patch chỉ sửa tham số job đã có (vd. prompt). Muốn đổi kết nối hay thêm tham số khác thì tạo "
                 "job mới.",
            status=422,
            extra={"job_id": job.id, "keys": bad, "allowed": sorted(known - _FROZEN_PARAMS)},
        )


def _reset_for_run(job: Job) -> None:
    job.status = "queued"
    job.next_attempt_at = None
    job.error_code = None
    job.error_message = None
    job.hint = None
    job.progress = 0.0
    job.stage = ""
    job.eta_at = None
    job.started_at = None
    job.finished_at = None


def retry_job(
    session: Session, job_id: int, params_patch: dict | None = None, *, force_new: bool = False
) -> Job:
    """Chạy lại một job đã xong/thất bại/huỷ/bị gián đoạn: đặt lại thành `queued`, `attempt=0`, gộp `params_patch`.

    Dùng lại chính dòng job (id giữ nguyên cho UI); kết quả cũ bị xoá. Provider đã nhận job (`external_id`): mặc định
    **giữ** `external_id`/`external_state` để lần chạy tới nối lại (poll) đúng job đó — không trả tiền lần hai;
    `force_new=True` xoá chúng → tạo job trả phí mới. Chưa có `external_id`: trạng thái provider cũ bị xoá như trước.
    Nối lại kèm `params_patch` → 422 `params_patch_requires_new_job`. Lần gửi mới sau lần đã trả tiền (`force_new`, job
    đã xong) tách dòng `spend` cũ khỏi job (`budget.detach`) để chúng vẫn được tính.
    """
    job = get_job(session, job_id)
    if job.status not in RETRYABLE:
        raise Conflict(
            f"Job #{job_id} đang ở trạng thái '{job.status}', không chạy lại được.",
            hint="Chỉ chạy lại job đã xong, thất bại, đã huỷ hoặc bị gián đoạn; job cần xử lý dùng job.clear_attention.",
        )
    # Gửi dở, không rõ kết quả → không được gửi lại mù. Job `succeeded` thì việc gửi đã có kết quả rõ (dấu sót lại
    # từ handler đồng bộ không lưu external_id) → chạy lại được.
    if job.status != "succeeded" and has_submit_marker(job.external_id, job.external_state):
        raise Conflict(
            f"Job #{job_id} có thể đã được gửi tới provider (đánh dấu submitting, chưa có external_id).",
            hint=JOB_UNKNOWN_HINT,
            extra={"job_id": job_id},
        )
    _check_patch(job, params_patch)
    if job.client_ref is not None:
        twin = session.scalars(
            select(Job.id).where(Job.client_ref == job.client_ref, Job.status.not_in(TERMINAL), Job.id != job.id)
        ).first()
        if twin is not None:
            raise Conflict(
                f"Đã có job #{twin} đang hoạt động cho cùng nội dung (client_ref).",
                hint=f"Theo dõi hoặc huỷ job #{twin} trước khi chạy lại.",
            )
    holds = provider_holds_job(job)
    resume = holds and not force_new
    new_paid = holds and force_new
    if resume and job.status == "succeeded":
        raise Conflict(
            f"Job #{job_id} đã xong và provider đã giữ kết quả; chạy lại thường chỉ lấy lại đúng kết quả cũ.",
            hint="Muốn tạo lại thì chạy lại với force_new=true (tạo job trả phí mới).",
            extra={"job_id": job_id},
            code="already_succeeded",
        )
    if resume and params_patch:
        raise AppError(
            "params_patch_requires_new_job",
            f"Job #{job_id} đã được provider nhận; chạy lại sẽ nối lại đúng job đó nên tham số sửa đổi không có tác dụng.",
            hint="Muốn đổi prompt/tham số thì chạy lại với force_new=true (tạo job trả phí mới); bỏ params_patch để "
                 "chỉ nối lại job cũ (không tốn thêm).",
            status=422,
            extra={"job_id": job_id, "external_id": job.external_id},
        )
    # Lần gửi mới (không nối lại) sau một lần đã/có thể đã trả tiền: tách dòng chi tiêu cũ khỏi job để giữ chỗ/trả chỗ
    # của lần mới không bao giờ xoá được chúng (vẫn được tính vào ngân sách).
    if new_paid or (job.status == "succeeded" and not resume):
        _budget().detach(session, job.id)
    gen = generation(job.external_state)
    was_cancelled = job.status == "cancelled"
    _reset_for_run(job)
    job.attempt = 0
    job.result = {}
    job.cost_actual = {}
    if resume:
        state = {k: v for k, v in (job.external_state or {}).items() if k != USER_CANCELLED_KEY}
        if was_cancelled:
            state[USER_CANCELLED_KEY] = True
        job.external_state = state  # dict mới: cột JSON không theo dõi sửa tại chỗ
    else:
        job.external_id = None
        # Giữ thế hệ gửi qua mọi lần đặt lại; force_new = thế hệ mới (clientRef mới phía provider).
        job.external_state = _gen_state(gen + 1 if force_new else gen)
    if params_patch:
        job.params = {**(job.params or {}), **params_patch}
    session.flush()
    how = (" — nối lại job đã gửi tới provider (không gửi lại)" if resume
           else " — tạo job trả phí mới phía provider" if new_paid else "")
    add_log(session, job.id, "info",
            "Chạy lại theo yêu cầu" + (" (có sửa tham số)" if params_patch else "") + how + ".")
    return job


def prioritize(session: Session, job_ids: list[int], priority: int) -> list[Job]:
    """Đổi ưu tiên các job chưa kết thúc; job đã kết thúc bị bỏ qua. Id không tồn tại → NotFound."""
    changed = []
    for job_id in job_ids:
        job = get_job(session, job_id)
        if job.status in TERMINAL:
            continue
        job.priority = priority
        changed.append(job)
    session.flush()
    return changed


def clear_attention(
    session: Session, job_id: int, action: Literal["resubmit", "fail"], params_patch: dict | None = None
) -> Job:
    """Người dùng/agent quyết định cho job `needs_attention`: xoá dấu provider rồi gửi lại, hoặc cho thất bại.

    Đây là đường **duy nhất** xoá dấu `submitting`; nên cũng nhận job đã dừng (thất bại/huỷ/gián đoạn — không bao giờ
    `succeeded`) còn mang dấu đó — `job.retry` từ chối các job này. `resubmit` đặt `attempt=0` và gộp `params_patch`;
    `fail` ghi `error_code="cleared_by_user"`.
    """
    job = get_job(session, job_id)
    if job.status != "needs_attention" and not needs_decision(job.status, job.external_id, job.external_state):
        raise Conflict(
            f"Job #{job_id} không ở trạng thái cần xử lý (hiện là '{job.status}').",
            hint="Chỉ dùng cho job needs_attention hoặc job đã dừng (không thành công) còn đánh dấu submitting.",
        )
    if action == "resubmit":
        _check_patch(job, params_patch)
    # Dòng chi tiêu có thể đã trả tiền thì giữ trong sổ (tách khỏi job): provider đã nhận (external_id), hoặc người
    # dùng cho thất bại (giữ chỗ mập mờ — thận trọng). Chỉ `resubmit` một job chỉ mang dấu `submitting` (người dùng xác
    # nhận provider chưa tạo) mới để lần gửi mới thay chỗ giữ đó.
    marker_only = has_submit_marker(job.external_id, job.external_state)
    if not (marker_only and action == "resubmit"):
        _budget().detach(session, job.id)
    gen = generation(job.external_state)
    # resubmit job provider đang giữ (vd. bị huỷ phía provider) = lần gửi mới → thế hệ mới (clientRef mới, provider không
    # gộp về job cũ). Job chỉ mang dấu `submitting`: giữ thế hệ → cùng clientRef, nếu provider thật ra đã tạo job thì nó
    # tự chống trùng. `fail` giữ thế hệ.
    bump = action == "resubmit" and provider_holds_job(job)
    new_state = _gen_state(gen + 1 if bump else gen)
    stored_ref = (job.external_state or {}).get(CLIENT_REF_KEY)
    if marker_only and action == "resubmit" and stored_ref:
        # Gửi lại ĐÚNG ref của lần POST mập mờ (kể cả ref một-lần `:uuid` adapter tự đổi): provider chống trùng theo ref.
        new_state[CLIENT_REF_KEY] = stored_ref
    job.external_id = None
    job.external_state = new_state
    if action == "resubmit":
        _reset_for_run(job)
        job.attempt = 0
        if params_patch:
            job.params = {**(job.params or {}), **params_patch}
        add_log(session, job.id, "warning", "Đã xoá đánh dấu phía provider và gửi lại theo quyết định của người dùng.")
    else:
        job.status = "failed"
        job.error_code = "cleared_by_user"
        job.error_message = "Người dùng đã xoá đánh dấu phía provider và cho job thất bại."
        job.hint = None
        job.next_attempt_at = None
        job.finished_at = utcnow()
        add_log(session, job.id, "warning", "Đánh dấu thất bại theo quyết định của người dùng.")
    session.flush()
    return job


def flag_marked_queued(session: Session) -> list[Job]:
    """Job `queued` mang dấu `submitting` chưa có `external_id` → `needs_attention` (không bao giờ được chạy lại mù).

    Worker gọi trước mỗi lần giành job và khi khôi phục; người gọi commit rồi phát `job.updated`.
    """
    rows = list(session.scalars(select(Job).where(Job.status == "queued", submit_marked()).order_by(Job.id)))
    for job in rows:
        for k, v in job_unknown_values("Job đang chờ chạy lại nhưng còn đánh dấu đang gửi tới provider.").items():
            setattr(job, k, v)
        add_log(session, job.id, "warning", "Job còn đánh dấu submitting (chưa có external_id); không chạy lại.")
    session.flush()
    return rows


def cancel_idle(session: Session, job_id: int) -> Job | None:
    """Huỷ job chưa kết thúc mà **không** đang chạy (compare-and-set). Trả job nếu đã đổi, None nếu không."""
    res = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.not_in(TERMINAL | {"running"}))
        .values(status="cancelled", finished_at=utcnow(), updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        return None
    add_log(session, job_id, "info", "Đã huỷ.")
    job = session.get(Job, job_id)
    session.refresh(job)
    if not job.external_id and not (job.external_state or {}).get(SUBMIT_MARKER_KEY):
        # Người dùng huỷ job provider chưa từng nhận: trả chỗ ngân sách còn sót (vd. từ lần tắt app trước khi gửi).
        _budget().release(session, job_id)
    return job


def _budget() -> Any:
    from sagareel.services import budget  # import muộn: budget → settings không được kéo vào lúc nạp module jobs

    return budget


# --- Tạm dừng toàn bộ (bền qua khởi động lại) ---------------------------------------------------------------------


def is_paused(session: Session) -> bool:
    return bool(system_state.get(session, PAUSED_KEY, False))


def set_paused(session: Session, paused: bool) -> None:
    system_state.set(session, PAUSED_KEY, paused)
