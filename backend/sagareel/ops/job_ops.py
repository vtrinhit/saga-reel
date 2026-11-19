"""Thao tác nhóm `job`: xem, huỷ, chạy lại, ưu tiên, tạm dừng toàn bộ, chờ, xử lý job cần chú ý.

Dòng job là sổ sách của máy → ghi bằng `db.session(immediate=True)` ngắn, không UnitOfWork/revision. Sau khi commit
phát `job.updated` và đánh thức worker.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import anyio
from pydantic import BaseModel, Field

from sagareel.core.context import OpContext
from sagareel.core.ops import op
from sagareel.services import jobs

_IDS = Field(min_length=1, max_length=500)
WAIT_POLL_S = 0.25


def _notify_all(ctx: OpContext, datas: list[dict]) -> None:
    for data in datas:
        jobs.notify(ctx.services, data)


def _paused(ctx: OpContext) -> bool:
    worker = ctx.services.worker
    return bool(worker.paused) if worker is not None else False


# --- Đọc ----------------------------------------------------------------------------------------------------------


class JobListParams(BaseModel):
    status: str | None = None
    type: str | None = None
    lane: str | None = None
    project_id: int | None = None
    cursor: int | None = Field(default=None, description="`next_cursor` của trang trước")
    limit: int = Field(default=50, ge=1, le=500)


@op(
    "job.list",
    summary="Danh sách job (mới nhất trước), lọc theo trạng thái/loại/lane/project, phân trang bằng cursor",
    params=JobListParams,
    read_only=True,
    idempotent=True,
    mcp="tool",
)
def job_list(ctx: OpContext, p: JobListParams) -> dict:
    with ctx.services.db.session() as s:
        rows, next_cursor = jobs.list_jobs(
            s, status=p.status, type=p.type, lane=p.lane, project_id=p.project_id, cursor=p.cursor, limit=p.limit
        )
        items = [jobs.job_dict(j) for j in rows]
    return {"items": items, "next_cursor": next_cursor, "paused": _paused(ctx)}


class JobIdParams(BaseModel):
    id: int


@op("job.get", summary="Chi tiết một job", params=JobIdParams, read_only=True, idempotent=True, mcp="tool")
def job_get(ctx: OpContext, p: JobIdParams) -> dict:
    with ctx.services.db.session() as s:
        return jobs.job_dict(jobs.get_job(s, p.id))


class JobLogsParams(BaseModel):
    id: int
    after_id: int | None = Field(default=None, description="Chỉ lấy dòng có id lớn hơn (đọc tiếp)")
    limit: int = Field(default=200, ge=1, le=1000)


@op("job.logs", summary="Nhật ký của một job (cũ trước)", params=JobLogsParams, read_only=True, idempotent=True,
    mcp="tool")
def job_logs(ctx: OpContext, p: JobLogsParams) -> dict:
    with ctx.services.db.session() as s:
        items = [jobs.log_dict(r) for r in jobs.job_logs(s, p.id, after_id=p.after_id, limit=p.limit)]
    return {"items": items}


class JobWaitParams(BaseModel):
    ids: list[int] = _IDS
    timeout_s: int = Field(default=60, ge=0, le=600)


@op(
    "job.wait",
    summary="Chờ các job dừng tiến triển (không còn queued/running) hoặc hết giờ; trả trạng thái và timed_out",
    params=JobWaitParams,
    read_only=True,
    idempotent=True,
    long_running=True,
    mcp="tool",
)
async def job_wait(ctx: OpContext, p: JobWaitParams) -> dict:
    """Không giữ phiên DB khi chờ: mỗi nhịp đọc trong một phiên ngắn (thread). Dừng khi mọi job rời `queued`/`running`
    (kết thúc, cần xử lý, bị gián đoạn, chờ người dùng…)."""
    ids = list(dict.fromkeys(p.ids))

    def snapshot() -> list[dict]:
        with ctx.services.db.session() as s:
            return [jobs.job_dict(jobs.get_job(s, i)) for i in ids]

    loop = asyncio.get_running_loop()
    deadline = loop.time() + p.timeout_s
    while True:
        items = await anyio.to_thread.run_sync(snapshot)
        if all(j["status"] not in jobs.ACTIVE for j in items):
            return {"items": items, "timed_out": False}
        remaining = deadline - loop.time()
        if remaining <= 0:
            return {"items": items, "timed_out": True}
        await asyncio.sleep(min(WAIT_POLL_S, remaining))


# --- Ghi ----------------------------------------------------------------------------------------------------------


class JobIdsParams(BaseModel):
    ids: list[int] = _IDS


@op("job.cancel", summary="Huỷ job (đang chạy thì dừng handler); job đã kết thúc được bỏ qua", params=JobIdsParams,
    idempotent=True)
async def job_cancel(ctx: OpContext, p: JobIdsParams) -> dict:
    ids = list(dict.fromkeys(p.ids))

    def check() -> None:
        with ctx.services.db.session() as s:
            for i in ids:
                jobs.get_job(s, i)

    await anyio.to_thread.run_sync(check)
    worker = ctx.services.worker
    cancelled, skipped = [], []
    for i in ids:
        if worker is not None:
            ok = await worker.cancel(i)
        else:
            data = await anyio.to_thread.run_sync(_cancel_idle, ctx, i)
            ok = data is not None
            if ok:
                jobs.notify(ctx.services, data)
        (cancelled if ok else skipped).append(i)
    return {"cancelled": cancelled, "skipped": skipped}


def _cancel_idle(ctx: OpContext, job_id: int) -> dict | None:
    with ctx.services.db.session() as s:
        job = jobs.cancel_idle(s, job_id)
        return jobs.job_dict(job) if job is not None else None


class JobRetryParams(BaseModel):
    ids: list[int] = _IDS
    params_patch: dict[str, Any] | None = Field(
        default=None, description="Gộp vào params trước khi chạy lại (vd. sửa prompt)"
    )


@op(
    "job.retry",
    summary="Chạy lại job đã xong/thất bại/huỷ/gián đoạn (về queued, attempt=0, gộp params_patch)",
    params=JobRetryParams,
    costly=True,
)
def job_retry(ctx: OpContext, p: JobRetryParams) -> dict:
    with ctx.services.db.session(immediate=True) as s:
        items = [jobs.job_dict(jobs.retry_job(s, i, p.params_patch)) for i in dict.fromkeys(p.ids)]
    _notify_all(ctx, items)
    return {"items": items}


class JobPrioritizeParams(BaseModel):
    ids: list[int] = _IDS
    priority: int


@op("job.prioritize", summary="Đổi độ ưu tiên (lớn chạy trước) của các job chưa kết thúc", params=JobPrioritizeParams,
    idempotent=True)
def job_prioritize(ctx: OpContext, p: JobPrioritizeParams) -> dict:
    with ctx.services.db.session(immediate=True) as s:
        items = [jobs.job_dict(j) for j in jobs.prioritize(s, list(dict.fromkeys(p.ids)), p.priority)]
    _notify_all(ctx, items)
    return {"items": items}


class EmptyParams(BaseModel):
    pass


@op("job.pause_all", summary="Tạm dừng lấy job mới (job đang chạy vẫn chạy xong); giữ qua khởi động lại",
    params=EmptyParams, idempotent=True)
def job_pause_all(ctx: OpContext, p: EmptyParams) -> dict:
    with ctx.services.db.session(immediate=True) as s:
        jobs.set_paused(s, True)
    if ctx.services.worker is not None:
        ctx.services.worker.pause_all()
    return {"paused": True}


@op("job.resume_all", summary="Tiếp tục lấy job sau khi tạm dừng toàn bộ", params=EmptyParams, idempotent=True)
def job_resume_all(ctx: OpContext, p: EmptyParams) -> dict:
    with ctx.services.db.session(immediate=True) as s:
        jobs.set_paused(s, False)
    if ctx.services.worker is not None:
        ctx.services.worker.resume_all()
    return {"paused": False}


class JobClearAttentionParams(BaseModel):
    id: int
    action: Literal["resubmit", "fail"]


@op(
    "job.clear_attention",
    summary="Xử lý job cần chú ý: xoá đánh dấu phía provider rồi gửi lại (resubmit) hoặc cho thất bại (fail)",
    params=JobClearAttentionParams,
    costly=True,
)
def job_clear_attention(ctx: OpContext, p: JobClearAttentionParams) -> dict:
    with ctx.services.db.session(immediate=True) as s:
        data = jobs.job_dict(jobs.clear_attention(s, p.id, p.action))
    jobs.notify(ctx.services, data)
    return data

