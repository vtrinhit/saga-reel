import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import anyio
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from sagareel import ops as ops_pkg
from sagareel.app import create_app
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.db.models import Job, JobLog, Spend
from sagareel.db.session import Database
from sagareel.services import jobs
from sagareel.services import worker as worker_mod

# --- Handler test bổ sung (chỉ module này) --------------------------------------------------------------------------

EXTRA_TYPES = (
    "test.held_progress",
    "test.custom_fail",
    "test.custom_attention",
    "test.always_transient",
    "test.progress",
    "test.external",
    "test.gate",
    "test.logs",
    "test.cancel_cleanup",
    "test.self_cancel",
    "test.gate_transient",
    "test.submit_hang",
    "test.submit_then",
    "test.path_result",
    "test.stubborn",
    "test.sync_submit",
)


class ProviderRejected(jobs.JobFailed):
    """Giống lỗi provider sau này: lớp con của JobFailed mang mã riêng."""


class ProviderJobUnknown(jobs.NeedsAttention):
    pass


@pytest.fixture
def extra_handlers(test_job_handlers):
    probe = test_job_handlers

    @jobs.job_handler("test.custom_fail")
    async def custom_fail(run):
        raise ProviderRejected("prompt_rejected", "Provider từ chối prompt.", hint="Sửa prompt rồi chạy lại.")

    @jobs.job_handler("test.custom_attention", resumable=True)
    async def custom_attention(run):
        raise ProviderJobUnknown("provider_job_unknown", "Không rõ job phía provider.", hint="Kiểm tra rồi quyết định.")

    @jobs.job_handler("test.always_transient")
    async def always_transient(run):
        probe.order.append(run.attempt)
        raise jobs.TransientError("Mạng chập chờn (test)", retry_after=0.01)

    @jobs.job_handler("test.progress")
    async def progress_handler(run):
        for i in range(100):
            run.progress(i * 0.5, stage="render", eta_s=10)
        run.progress(77.0, stage="cuối")
        await asyncio.sleep(0.4)
        return {}

    @jobs.job_handler("test.external", resumable=True)
    async def external_handler(run):
        await run.save_external("ext-1", state="submitting")
        await probe.gate(run.job_id).wait()
        try:
            await run.save_external(state="submitted")
        except Exception as e:  # noqa: BLE001 — test ghi lại loại lỗi
            probe.errors[run.job_id] = e
            raise
        return {}

    @jobs.job_handler("test.gate")
    async def gate_handler(run):
        probe.runs[run.job_id] = run
        await probe.gate(run.job_id).wait()
        return {"ok": True}

    @jobs.job_handler("test.logs")
    async def logs_handler(run):
        for i in range(5):
            run.log("info", f"dòng {i}")
        return {}

    @jobs.job_handler("test.cancel_cleanup")
    async def cancel_cleanup(run):
        probe.runs[run.job_id] = run
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            probe.errors[run.job_id] = run.cancelled.is_set()
            raise
        return {}

    @jobs.job_handler("test.self_cancel", resumable=True)
    async def self_cancel(run):
        probe.order.append(run.job_id)
        raise asyncio.CancelledError()  # handler tự ném, không phải do huỷ/tắt app

    @jobs.job_handler("test.submit_hang", resumable=True)
    async def submit_hang(run):
        await run.save_external(state="submitting")  # như adapter: đánh dấu ngay trước POST tốn tiền
        probe.runs[run.job_id] = run
        await asyncio.sleep(30)
        return {}

    @jobs.job_handler("test.submit_then", resumable=True)
    async def submit_then(run):
        """Đánh dấu submitting rồi lỗi theo `params.raise` (như POST timeout / provider lỗi / bug)."""
        probe.order.append(run.job_id)
        await run.save_external(state="submitting")
        kind = run.params["raise"]
        if kind == "transient":
            raise jobs.TransientError("POST timeout", retry_after=0)
        if kind == "failed":
            raise jobs.JobFailed("provider_error", "Provider lỗi.")
        raise ValueError("bug")

    @jobs.job_handler("test.sync_submit")
    async def sync_submit(run):
        """Provider đồng bộ: đánh dấu submitting, nhận kết quả ngay, không có external_id."""
        await run.save_external(state="submitting", note="đồng bộ")
        return {"url": "https://example.invalid/x.png"}

    @jobs.job_handler("test.path_result")
    async def path_result(run):
        from pathlib import Path

        return {"p": Path("x.mp4")}

    @jobs.job_handler("test.stubborn")
    async def stubborn(run):
        probe.runs[run.job_id] = run
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await probe.gate(run.job_id).wait()  # nuốt huỷ, chỉ dừng khi test mở cổng
        return {}

    @jobs.job_handler("test.held_progress", resumable=True)
    async def held_progress(run):
        """Provider nhận job (có id) rồi báo tiến độ: payload `job.updated` lúc chạy phải thấy provider_holds."""
        await run.save_external("ext-9", state="submitted")
        run.progress(10.0, stage="poll")
        await asyncio.sleep(0.4)
        return {}

    @jobs.job_handler("test.gate_transient")
    async def gate_transient(run):
        probe.runs[run.job_id] = run
        await probe.gate(run.job_id).wait()
        raise jobs.TransientError("tạm lỗi", retry_after=60)

    try:
        yield probe
    finally:
        for name in EXTRA_TYPES:
            jobs.unregister_handler(name)


# --- Helper -------------------------------------------------------------------------------------------------------


def enqueue_job(services, type, params, *, lane="L", **kw) -> int:
    return jobs.submit(services, type, params, lane=lane, **kw)["id"]


def insert_job(services, type, *, status, lane="L", **cols) -> int:
    with services.db.session() as s:
        cols.setdefault("params", {})
        job = Job(type=type, status=status, lane=lane, **cols)
        s.add(job)
        s.flush()
        return job.id


def load(services, job_id) -> Job:
    with services.db.session() as s:
        return s.get(Job, job_id)


def status(services, job_id) -> str:
    return load(services, job_id).status


def error_code(services, job_id) -> str | None:
    return load(services, job_id).error_code


def logs(services, job_id) -> str:
    with services.db.session() as s:
        rows = s.scalars(select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.id)).all()
        return "\n".join(f"{r.level}: {r.message}" for r in rows)


def count(services, *, status) -> int:
    with services.db.session() as s:
        return s.scalar(select(func.count()).select_from(Job).where(Job.status == status))


def _busy(services) -> bool:
    with services.db.session() as s:
        n = s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running"))))
    return n > 0


async def wait_until(pred, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not await anyio.to_thread.run_sync(pred):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Hết giờ chờ điều kiện")
        await asyncio.sleep(0.01)


async def run_until_idle(services, timeout=10.0):
    w = services.worker
    await w.start()
    try:
        await wait_until(lambda: not _busy(services) and w.running_count() == 0, timeout)
    finally:
        await w.stop()
    assert w.running_count() == 0


async def run_until_idle_tracking_peak(services, lane):
    await run_until_idle(services)
    return services.worker_probe.peak[lane]


@pytest.fixture
def svc(services, extra_handlers):
    services.worker_probe = extra_handlers
    return services


def ctx_for(services, role="owner"):
    return OpContext(services=services, actor="user:owner", role=role)


@pytest.fixture
def capture(svc):
    events: list[tuple[str, dict]] = []
    orig = svc.bus.publish

    def publish(type, data, project_id=None):
        events.append((type, dict(data)))
        orig(type, data, project_id=project_id)

    svc.bus.publish = publish
    return events


# --- Lane, ưu tiên ------------------------------------------------------------------------------------------------


async def test_lane_limit_respected(svc):
    services = svc
    for _ in range(50):
        enqueue_job(services, "test.sleep", {"s": 0.05}, lane="L")
    services.lane_limits["L"] = 3
    peak = await run_until_idle_tracking_peak(services, lane="L")
    assert peak == 3 and count(services, status="succeeded") == 50


async def test_default_lane_limit_is_two(svc):
    for _ in range(6):
        enqueue_job(svc, "test.sleep", {"s": 0.05}, lane="ffmpeg")
    assert svc.lane_limit("ffmpeg") == 2
    assert await run_until_idle_tracking_peak(svc, lane="ffmpeg") == 2


async def test_full_lane_does_not_block_other_lanes(svc):
    svc.lane_limits["A"] = 1
    a = [enqueue_job(svc, "test.gate", {}, lane="A") for _ in range(3)]
    b = enqueue_job(svc, "test.sleep", {"s": 0}, lane="B")
    await svc.worker.start()
    try:
        await wait_until(lambda: status(svc, b) == "succeeded")
        assert [status(svc, j) for j in a] == ["running", "queued", "queued"]
    finally:
        for j in a:
            svc.worker_probe.gate(j).set()
        await svc.worker.stop()


async def test_lane_limit_error_does_not_block_other_lanes(svc, caplog):
    bad = enqueue_job(svc, "test.sleep", {"s": 0}, lane="bad")
    ok = enqueue_job(svc, "test.sleep", {"s": 0}, lane="good")

    def lane_limit(lane):
        if lane == "bad":
            raise RuntimeError("cấu hình hỏng")
        return 2

    svc.worker.lane_limit = lane_limit
    await svc.worker.start()
    try:
        await wait_until(lambda: status(svc, ok) == "succeeded")
    finally:
        await svc.worker.stop()
    assert status(svc, bad) == "queued"
    assert "lane 'bad'" in caplog.text


async def test_priority_desc_then_id_asc(svc):
    svc.lane_limits["L"] = 1
    low = enqueue_job(svc, "test.sleep", {"s": 0})
    high = enqueue_job(svc, "test.sleep", {"s": 0}, priority=5)
    low2 = enqueue_job(svc, "test.sleep", {"s": 0})
    await run_until_idle(svc)
    assert svc.worker_probe.order == [high, low, low2]


# --- Retry / lỗi --------------------------------------------------------------------------------------------------


async def test_transient_retries_then_succeeds(svc):
    a = enqueue_job(svc, "test.flaky", {})
    await run_until_idle(svc)
    job = load(svc, a)
    assert job.status == "succeeded" and job.attempt == 1 and job.result == {"attempt": 1}
    assert job.progress == 100


async def test_transient_exhausts_retries(svc):
    a = enqueue_job(svc, "test.always_transient", {}, max_attempts=3)
    await run_until_idle(svc)
    job = load(svc, a)
    assert job.status == "failed" and job.error_code == "retries_exhausted"
    assert "Mạng chập chờn" in job.error_message
    assert svc.worker_probe.order == [0, 1, 2]  # đúng 3 lần chạy


def test_retry_delay_formula(monkeypatch):
    monkeypatch.setattr(worker_mod.random, "uniform", lambda a, b: (a, b) and 1.0)
    assert worker_mod.retry_delay(0, None) == 5
    assert worker_mod.retry_delay(3, None) == 40
    assert worker_mod.retry_delay(10, None) == 300
    assert worker_mod.retry_delay(2, 7.5) == 7.5
    assert worker_mod.retry_delay(2, 0) == 0  # retry_after=0 được tôn trọng, không rơi về backoff
    seen = []
    monkeypatch.setattr(worker_mod.random, "uniform", lambda a, b: seen.append((a, b)) or b)
    assert worker_mod.retry_delay(1, None) == pytest.approx(12.0)
    assert seen == [(0.8, 1.2)]


async def test_transient_sets_next_attempt_at_with_backoff(svc, monkeypatch):
    monkeypatch.setattr(worker_mod.random, "uniform", lambda a, b: 1.0)
    a = enqueue_job(svc, "test.always_transient", {})
    # retry_after=0.01 của handler → thay bằng None để kiểm tra backoff mũ
    orig = jobs.HANDLERS["test.always_transient"]

    async def no_retry_after(run):
        raise jobs.TransientError("tạm lỗi")

    jobs.HANDLERS["test.always_transient"] = jobs.HandlerSpec("test.always_transient", no_retry_after, False)
    try:
        before = datetime.now(timezone.utc)
        await svc.worker.start()
        try:
            await wait_until(lambda: load(svc, a).attempt == 1)
        finally:
            await svc.worker.stop()
    finally:
        jobs.HANDLERS["test.always_transient"] = orig
    job = load(svc, a)
    assert job.status == "queued"
    assert before + timedelta(seconds=4) < job.next_attempt_at < before + timedelta(seconds=7)


async def test_unexpected_exception_fails_job_and_worker_survives(svc):
    services = svc
    a = enqueue_job(services, "test.boom", {})
    b = enqueue_job(services, "test.sleep", {"s": 0})
    await run_until_idle(services)
    assert status(services, a) == "failed" and error_code(services, a) == "internal_error"
    assert "ValueError" in logs(services, a) and "error:" in logs(services, a)
    assert status(services, b) == "succeeded"


async def test_job_failed_and_needs_attention_use_instance_code_and_hint(svc):
    a = enqueue_job(svc, "test.custom_fail", {})
    b = enqueue_job(svc, "test.custom_attention", {})
    await run_until_idle(svc)
    ja, jb = load(svc, a), load(svc, b)
    assert (ja.status, ja.error_code, ja.error_message, ja.hint) == (
        "failed", "prompt_rejected", "Provider từ chối prompt.", "Sửa prompt rồi chạy lại.")
    assert (jb.status, jb.error_code, jb.hint) == ("needs_attention", "provider_job_unknown", "Kiểm tra rồi quyết định.")
    assert jb.finished_at is None and ja.finished_at is not None


async def test_unknown_job_type_fails(svc):
    a = enqueue_job(svc, "khong.co_handler", {})
    await run_until_idle(svc)
    assert status(svc, a) == "failed" and error_code(svc, a) == "unknown_job_type"


# --- Khôi phục sau khởi động lại ----------------------------------------------------------------------------------


def test_recover_marks_running(services):
    a = insert_job(services, "test.resumable", status="running", external_id="x-1",
                   external_state={"state": "submitted"}, attempt=1)
    b = insert_job(services, "test.sleep", status="running")
    c = insert_job(services, "test.sleep", status="queued")
    services.worker.recover()
    assert status(services, a) == "queued" and status(services, b) == "interrupted"
    ja, jb = load(services, a), load(services, b)
    assert ja.external_id == "x-1" and ja.external_state == {"state": "submitted"} and ja.attempt == 1
    assert jb.error_code == "interrupted" and jb.error_message
    assert status(services, c) == "queued"


def test_recover_submitting_marker_goes_to_needs_attention(svc):
    marker = {"state": "submitting"}
    resumable = insert_job(svc, "test.resumable", status="running", external_state=marker)
    plain = insert_job(svc, "test.sleep", status="running", external_state=marker)
    unregistered = insert_job(svc, "khong.co_handler", status="running", external_state=marker)
    has_id = insert_job(svc, "test.resumable", status="running", external_id="p-1", external_state=marker)
    svc.worker.recover()
    for jid in (resumable, plain, unregistered):
        job = load(svc, jid)
        assert (job.status, job.error_code) == ("needs_attention", "job_unknown")
        assert job.external_state == marker and "job.clear_attention" in job.hint
    assert status(svc, has_id) == "queued"  # đã có external_id → nối lại bình thường
    assert "submitting" in logs(svc, plain)


async def test_stop_while_submitting_goes_to_needs_attention(svc):
    a = enqueue_job(svc, "test.submit_hang", {})
    await svc.worker.start()
    try:
        await wait_until(lambda: a in svc.worker_probe.runs)
    finally:
        await svc.worker.stop()
    job = load(svc, a)
    assert (job.status, job.error_code) == ("needs_attention", "job_unknown")
    assert job.external_state == {"state": "submitting"}


async def test_resumed_job_sees_external_state(svc):
    a = insert_job(svc, "test.resumable", status="running", external_id="x-9", external_state={"state": "polling"})
    svc.worker.recover()
    await run_until_idle(svc)
    job = load(svc, a)
    assert job.status == "succeeded" and job.result == {"external_id": "x-9", "external": {"state": "polling"}}


async def test_stop_while_running_requeues_resumable_and_interrupts_others(svc):
    a = enqueue_job(svc, "test.resumable", {"s": 30})
    b = enqueue_job(svc, "test.sleep", {"s": 30})
    await svc.worker.start()
    try:
        # Chờ handler thật sự chạy (không chỉ DB đã "running": job giành được mà chưa chạy thì stop trả về queued)
        await wait_until(lambda: {a, b} <= set(svc.worker_probe.runs))
    finally:
        await svc.worker.stop()
    assert status(svc, a) == "queued" and status(svc, b) == "interrupted"
    assert svc.worker.running_count() == 0


async def test_jobs_claimed_while_stopping_go_back_to_queue(svc):
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    w = svc.worker
    orig_claim = w._claim

    claimed = threading.Event()

    def claim_then_stop(counts):
        out = orig_claim(counts)
        w._stopping = True  # stop() được gọi trong lúc thread đang giành job
        if out:
            claimed.set()
        return out

    w._claim = claim_then_stop
    await w.start()
    assert await anyio.to_thread.run_sync(claimed.wait, 5)
    await w.stop()
    assert status(svc, a) == "queued" and a not in svc.worker_probe.runs and w.running_count() == 0


async def test_stop_without_start_is_safe(svc):
    await svc.worker.stop()
    svc.worker.wake()  # chưa chạy: không làm gì


# --- client_ref / save_external -------------------------------------------------------------------------------------


def test_enqueue_same_client_ref_returns_same_job(services):
    a = enqueue_job(services, "test.sleep", {"s": 0}, client_ref="sagareel:take-1")
    b = enqueue_job(services, "test.sleep", {"s": 1}, client_ref="sagareel:take-1")
    assert a == b and count(services, status="queued") == 1
    with services.db.session() as s:
        s.get(Job, a).status = "failed"
    c = enqueue_job(services, "test.sleep", {"s": 0}, client_ref="sagareel:take-1")
    assert c != a
    with services.db.session() as s:
        j = jobs.enqueue(s, "test.sleep", {}, lane="L", client_ref="sagareel:take-1")
        assert j.id == c


async def test_save_external_is_durable_before_returning(svc):
    a = enqueue_job(svc, "test.external", {})
    await svc.worker.start()
    try:
        # Phiên mới (độc lập với worker) thấy ngay "submitting" + external_id
        await wait_until(lambda: load(svc, a).external_id == "ext-1")
        assert load(svc, a).external_state == {"state": "submitting"}
        svc.worker_probe.gate(a).set()
        await wait_until(lambda: status(svc, a) == "succeeded")
    finally:
        await svc.worker.stop()
    assert load(svc, a).external_state == {"state": "submitted"}


async def test_save_external_raises_when_job_no_longer_running(svc):
    a = enqueue_job(svc, "test.external", {})
    await svc.worker.start()
    try:
        await wait_until(lambda: load(svc, a).external_id == "ext-1")
        with svc.db.session() as s:  # job bị đặt lại bên ngoài worker
            s.get(Job, a).status = "failed"
        svc.worker_probe.gate(a).set()
        await wait_until(lambda: a in svc.worker_probe.errors)
    finally:
        await svc.worker.stop()
    assert isinstance(svc.worker_probe.errors[a], jobs.JobStateLost)
    assert load(svc, a).external_state == {"state": "submitting"}


# --- Huỷ ----------------------------------------------------------------------------------------------------------


async def test_cancel_running_sets_cancelled_and_sets_event(svc):
    a = enqueue_job(svc, "test.cancel_cleanup", {})
    await svc.worker.start()
    try:
        await wait_until(lambda: a in svc.worker_probe.runs)
        run = svc.worker_probe.runs[a]
        await svc.worker.cancel(a)
        assert run.cancelled.is_set()
        assert svc.worker_probe.errors[a] is True  # handler thấy cờ huỷ khi nhận CancelledError
        assert status(svc, a) == "cancelled" and load(svc, a).finished_at is not None
        assert svc.worker.running_count() == 0
    finally:
        await svc.worker.stop()
    assert status(svc, a) == "cancelled"


async def test_cancel_queued_and_terminal(svc):
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    done = insert_job(svc, "test.sleep", status="succeeded")
    assert await svc.worker.cancel(a) is True
    assert await svc.worker.cancel(done) is False
    assert status(svc, a) == "cancelled" and status(svc, done) == "succeeded"


async def test_cancel_twice_concurrently(svc):
    a = enqueue_job(svc, "test.cancel_cleanup", {})
    await svc.worker.start()
    try:
        await wait_until(lambda: a in svc.worker_probe.runs)
        r1, r2 = await asyncio.gather(svc.worker.cancel(a), svc.worker.cancel(a))
        assert status(svc, a) == "cancelled" and (r1 or r2)
        assert svc.worker.running_count() == 0
    finally:
        await svc.worker.stop()
    assert status(svc, a) == "cancelled"


async def test_cancel_while_final_write_requeues_still_cancels(svc):
    probe = svc.worker_probe
    a = enqueue_job(svc, "test.gate_transient", {})
    w = svc.worker
    release = threading.Event()
    orig_write = w._write_final

    def slow_write(*args):
        release.wait(5)
        return orig_write(*args)

    w._write_final = slow_write
    await w.start()
    try:
        await wait_until(lambda: a in probe.runs)
        run = probe.runs[a]
        probe.gate(a).set()
        while not run.task.done():  # handler đã ném TransientError, giám sát đang ghi "queued"
            await asyncio.sleep(0.01)
        pending = asyncio.create_task(w.cancel(a))
        await asyncio.sleep(0.05)
        release.set()
        assert await pending is True
    finally:
        release.set()
        await w.stop()
    assert status(svc, a) == "cancelled"


async def test_handler_raising_cancelled_error_itself_fails_instead_of_requeue(svc):
    a = enqueue_job(svc, "test.self_cancel", {})
    await run_until_idle(svc)
    assert status(svc, a) == "failed" and error_code(svc, a) == "internal_error"
    assert svc.worker_probe.order == [a]  # chạy đúng một lần, không vòng lặp queued


def _db_busy():
    return OperationalError("UPDATE job", {}, Exception("database is locked"))


async def test_final_write_is_retried_after_db_error(svc, monkeypatch):
    monkeypatch.setattr(worker_mod, "FINAL_WRITE_DELAYS", (0.01, 0.01, 0.01))
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    w = svc.worker
    orig_write = w._write_final
    calls = []

    def flaky_write(*args):
        calls.append(1)
        if len(calls) <= 2:
            raise _db_busy()
        return orig_write(*args)

    w._write_final = flaky_write
    await run_until_idle(svc)
    assert len(calls) == 3 and status(svc, a) == "succeeded"


async def test_final_write_gives_up_and_worker_survives(svc, monkeypatch, caplog):
    monkeypatch.setattr(worker_mod, "FINAL_WRITE_DELAYS", (0.01, 0.01))
    a = enqueue_job(svc, "test.sleep", {"s": 0}, lane="A")
    w = svc.worker
    orig_write = w._write_final
    calls = []

    def broken_for_a(job_id, values, logs_):
        if job_id == a:
            calls.append(1)
            raise _db_busy()
        return orig_write(job_id, values, logs_)

    w._write_final = broken_for_a
    await w.start()
    try:
        await wait_until(lambda: len(calls) == 3 and a not in w._runs)
        b = enqueue_job(svc, "test.sleep", {"s": 0}, lane="A")
        await wait_until(lambda: status(svc, b) == "succeeded")  # lane được giải phóng, worker vẫn chạy
    finally:
        await w.stop()
    assert status(svc, a) == "running"  # để lần khởi động sau recover() xử lý
    assert f"trạng thái cuối của job #{a}" in caplog.text


# --- Fix round 1 --------------------------------------------------------------------------------------------------

MARKER = {"state": "submitting"}


@pytest.mark.parametrize("kind", ["transient", "failed", "boom"])
async def test_failure_while_submitting_goes_to_needs_attention_not_retry(svc, kind):
    a = enqueue_job(svc, "test.submit_then", {"raise": kind}, max_attempts=5)
    await run_until_idle(svc)
    job = load(svc, a)
    assert (job.status, job.error_code) == ("needs_attention", "job_unknown")
    assert job.external_state == MARKER and job.next_attempt_at is None
    assert svc.worker_probe.order == [a]  # chạy đúng một lần, không retry mù


async def test_queued_job_with_marker_is_never_started(svc):
    a = insert_job(svc, "test.sleep", status="queued", external_state=MARKER)
    ok = insert_job(svc, "test.sleep", status="queued", external_state={"state": "submitting"}, external_id="p-1")
    await run_until_idle(svc)
    assert a not in svc.worker_probe.order and ok in svc.worker_probe.order
    job = load(svc, a)
    assert (job.status, job.error_code) == ("needs_attention", "job_unknown") and job.external_state == MARKER
    assert status(svc, ok) == "succeeded"


def test_recover_flags_queued_job_with_marker(svc):
    marked = insert_job(svc, "test.sleep", status="queued", external_state=MARKER)
    plain = insert_job(svc, "test.sleep", status="queued")
    svc.worker.recover()
    assert (status(svc, marked), error_code(svc, marked)) == ("needs_attention", "job_unknown")
    assert status(svc, plain) == "queued"


async def test_non_json_result_fails_job_instead_of_stranding_it(svc):
    a = enqueue_job(svc, "test.path_result", {})
    await run_until_idle(svc)
    job = load(svc, a)
    assert (job.status, job.error_code) == ("failed", "internal_error") and "JSON" in job.error_message
    assert job.result == {}


async def test_non_db_busy_write_error_is_not_retried_and_fails_job(svc, monkeypatch):
    monkeypatch.setattr(worker_mod, "FINAL_WRITE_DELAYS", (0.01, 0.01))
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    w = svc.worker
    orig_write = w._write_final
    calls = []

    def bad_first(job_id, values, logs_):
        calls.append(values["status"])
        if len(calls) == 1:
            raise ValueError("không ghi được")
        return orig_write(job_id, values, logs_)

    w._write_final = bad_first
    await run_until_idle(svc)
    assert calls == ["succeeded", "failed"]  # không thử lại cùng dữ liệu hỏng; ghi failed một lần
    assert (status(svc, a), error_code(svc, a)) == ("failed", "internal_error")


async def test_cancel_fallback_is_atomic_with_dispatch(svc):
    """Retry `retry_after=0` + huỷ đúng lúc ghi trạng thái cuối: dispatcher không được giành lại job xen giữa."""
    probe = svc.worker_probe
    w = svc.worker
    a = enqueue_job(svc, "test.gate_transient", {}, max_attempts=100)
    orig_gate = jobs.HANDLERS["test.gate_transient"]

    async def gate_zero(run):
        probe.order.append(run.job_id)
        probe.runs[run.job_id] = run
        if run.attempt == 0:
            await probe.gate(run.job_id).wait()
            raise jobs.TransientError("tạm lỗi", retry_after=0)
        await asyncio.sleep(30)  # lần chạy lại (nếu bị giành lại) chạy lâu → phải bị huỷ
        return {}

    jobs.HANDLERS["test.gate_transient"] = jobs.HandlerSpec("test.gate_transient", gate_zero, False)
    release = threading.Event()
    orig_write, orig_cancel_idle = w._write_final, w._cancel_idle

    def slow_write(*args):
        release.wait(5)
        return orig_write(*args)

    def slow_cancel_idle(job_id):
        time.sleep(0.2)  # cửa sổ đủ rộng để dispatcher (nếu không bị khoá) giành lại job
        return orig_cancel_idle(job_id)

    w._write_final, w._cancel_idle = slow_write, slow_cancel_idle
    try:
        await w.start()
        await wait_until(lambda: a in probe.runs)
        run = probe.runs[a]
        probe.gate(a).set()
        while not run.task.done():
            await asyncio.sleep(0.01)
        pending = asyncio.create_task(w.cancel(a))
        await asyncio.sleep(0.05)
        release.set()
        assert await pending is True
        await asyncio.sleep(0.2)
        assert status(svc, a) == "cancelled" and w.running_count() == 0
    finally:
        release.set()
        jobs.HANDLERS["test.gate_transient"] = orig_gate
        await w.stop()
    assert status(svc, a) == "cancelled"


async def test_launch_failure_returns_job_to_queue(svc, monkeypatch, caplog):
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    real = worker_mod.JobRun
    boom = []

    def flaky_jobrun(worker, data):
        if not boom:
            boom.append(1)
            raise RuntimeError("không dựng được JobRun")
        return real(worker, data)

    monkeypatch.setattr(worker_mod, "JobRun", flaky_jobrun)
    await run_until_idle(svc)
    assert boom and status(svc, a) == "succeeded"  # không thành dòng running mồ côi
    assert f"Không khởi chạy được job #{a}" in caplog.text


async def test_workdir_failure_fails_only_that_job(svc):
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    b = enqueue_job(svc, "test.sleep", {"s": 0})
    jobs_dir = svc.settings.cache_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / str(a)).write_text("not a directory", encoding="utf-8")  # mkdir workdir của a sẽ lỗi
    await run_until_idle(svc)
    job = load(svc, a)
    assert (job.status, job.error_code) == ("failed", "workdir_unavailable") and job.hint
    assert a not in svc.worker_probe.order
    assert status(svc, b) == "succeeded"  # job khác vẫn được giành và chạy


async def test_launch_failure_after_registration_is_undone(svc, monkeypatch):
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    real = worker_mod.JobRun._start_writer
    boom = []

    def flaky_start_writer(self):
        if not boom:
            boom.append(1)
            raise RuntimeError("không khởi động được writer")
        return real(self)

    monkeypatch.setattr(worker_mod.JobRun, "_start_writer", flaky_start_writer)
    w = svc.worker
    await w.start()
    try:
        await wait_until(lambda: status(svc, a) == "succeeded")
        await asyncio.sleep(0.05)
        # đăng ký của lần khởi chạy hỏng đã được gỡ: không rò `_lane_counts`/`_runs`
        assert boom and w.running_count() == 0 and a not in w._runs
        assert await w.cancel(a) is False  # job đã xong; không vấp run.task None
    finally:
        await w.stop()


async def test_claim_skips_marked_job_even_without_flag_step(svc, monkeypatch):
    monkeypatch.setattr(jobs, "flag_marked_queued", lambda s: [])
    marked = insert_job(svc, "test.sleep", status="queued", external_state=MARKER)
    plain = insert_job(svc, "test.sleep", status="queued", external_state={"other": 1})
    empty = insert_job(svc, "test.sleep", status="queued")
    await svc.worker.start()
    try:
        await wait_until(lambda: status(svc, plain) == "succeeded" and status(svc, empty) == "succeeded")
        await asyncio.sleep(0.15)
    finally:
        await svc.worker.stop()
    assert status(svc, marked) == "queued" and marked not in svc.worker_probe.order


async def test_success_drops_marker_and_succeeded_job_can_be_retried(svc, op_ctx):
    a = enqueue_job(svc, "test.sync_submit", {})
    await run_until_idle(svc)
    job = load(svc, a)
    assert job.status == "succeeded" and job.external_state == {"note": "đồng bộ"}
    out = await ops.invoke("job.retry", {"ids": [a]}, op_ctx)
    assert out["items"][0]["status"] == "queued"
    # dòng cũ (trước bản sửa) còn sót dấu trên job succeeded: vẫn chạy lại được
    legacy = insert_job(svc, "test.sleep", status="succeeded", external_state=MARKER)
    out = await ops.invoke("job.retry", {"ids": [legacy]}, op_ctx)
    assert out["items"][0]["status"] == "queued" and out["items"][0]["external_state"] == {}


async def test_cancel_wait_is_bounded(svc, monkeypatch):
    monkeypatch.setattr(worker_mod, "CANCEL_WAIT_S", 0.1)
    a = enqueue_job(svc, "test.stubborn", {})
    await svc.worker.start()
    try:
        await wait_until(lambda: a in svc.worker_probe.runs)
        t0 = time.monotonic()
        assert await svc.worker.cancel(a) is True
        assert time.monotonic() - t0 < 1
        assert status(svc, a) == "running"  # handler chưa dừng
        svc.worker_probe.gate(a).set()
        await wait_until(lambda: status(svc, a) == "cancelled")
    finally:
        svc.worker_probe.gate(a).set()
        await svc.worker.stop()


async def test_retry_checks_status_first_and_allows_marker_with_external_id(svc, op_ctx):
    q = insert_job(svc, "test.sleep", status="queued", external_state=MARKER)
    with pytest.raises(AppError) as ei:
        await ops.invoke("job.retry", {"ids": [q]}, op_ctx)
    assert "trạng thái 'queued'" in ei.value.detail
    known = insert_job(svc, "test.sleep", status="failed", external_id="p-9", external_state=MARKER)
    out = await ops.invoke("job.retry", {"ids": [known]}, op_ctx)
    # Provider đã nhận (có external_id): mặc định nối lại job cũ; force_new mới tạo job trả phí mới.
    assert out["items"][0]["status"] == "queued" and out["items"][0]["external_id"] == "p-9"
    with svc.db.session() as s:
        s.get(Job, known).status = "failed"
    out = await ops.invoke("job.retry", {"ids": [known], "force_new": True}, op_ctx)
    assert out["items"][0]["status"] == "queued" and out["items"][0]["external_id"] is None


async def test_clear_attention_resubmit_patch_and_fail_rules(svc, op_ctx):
    a = insert_job(svc, "test.sleep", status="needs_attention", external_state=MARKER, params={"prompt": "cũ"})
    out = await ops.invoke("job.clear_attention",
                           {"id": a, "action": "resubmit", "params_patch": {"prompt": "mới"}}, op_ctx)
    assert out["status"] == "queued" and out["params"] == {"prompt": "mới"}
    b = insert_job(svc, "test.sleep", status="needs_attention", external_state=MARKER,
                   error_code="job_unknown", hint="gợi ý cũ")
    out = await ops.invoke("job.clear_attention", {"id": b, "action": "fail"}, op_ctx)
    assert (out["status"], out["error_code"], out["hint"]) == ("failed", "cleared_by_user", None)
    done = insert_job(svc, "test.sleep", status="succeeded", external_state=MARKER)
    for action in ("fail", "resubmit"):
        with pytest.raises(AppError) as ei:
            await ops.invoke("job.clear_attention", {"id": done, "action": action}, op_ctx)
        assert ei.value.code == "conflict"
    assert status(svc, done) == "succeeded"


# --- Sự kiện, tiến độ, log ------------------------------------------------------------------------------------------


async def test_progress_events_are_coalesced(svc, capture):
    a = enqueue_job(svc, "test.progress", {})
    await run_until_idle(svc)
    running = [d for t, d in capture if t == "job.updated" and d["id"] == a and d["status"] == "running"]
    # claim + ≤ 4 lần/giây trong 0.4 s
    assert 1 <= len(running) <= 5
    progress_events = [d for d in running if d["stage"]]
    assert progress_events[-1]["progress"] == 77.0 and progress_events[-1]["stage"] == "cuối"
    last = [d for t, d in capture if t == "job.updated" and d["id"] == a][-1]
    assert last["status"] == "succeeded" and last["progress"] == 100


async def test_lifecycle_events_and_logs(svc, capture):
    a = enqueue_job(svc, "test.logs", {}, project_id=7)
    await run_until_idle(svc)
    statuses = [d["status"] for t, d in capture if t == "job.updated" and d["id"] == a]
    assert statuses[0] == "queued" and "running" in statuses and statuses[-1] == "succeeded"
    log_events = [d for t, d in capture if t == "job.log" and d["job_id"] == a]
    assert [d["message"] for d in log_events] == [f"dòng {i}" for i in range(5)]
    assert all(isinstance(d["id"], int) for d in log_events)
    assert logs(svc, a).splitlines() == [f"info: dòng {i}" for i in range(5)]


# --- Ops ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def op_ctx(svc):
    ops_pkg.load_all()
    return ctx_for(svc)


async def test_job_ops_registered_with_flags():
    ops_pkg.load_all()
    names = {"job.list", "job.get", "job.logs", "job.cancel", "job.retry", "job.prioritize", "job.pause_all",
             "job.resume_all", "job.wait", "job.clear_attention"}
    assert names <= set(ops.REGISTRY)
    spec = ops.REGISTRY["job.list"]
    assert spec.read_only and spec.viewer and spec.mcp == "tool"
    assert ops.REGISTRY["job.wait"].is_async and ops.REGISTRY["job.cancel"].is_async


async def test_job_list_filters_and_cursor(svc, op_ctx):
    ids = [enqueue_job(svc, "test.sleep", {"s": 0}, lane="A", project_id=1) for _ in range(5)]
    other = enqueue_job(svc, "test.boom", {}, lane="B", project_id=2)
    page = await ops.invoke("job.list", {"lane": "A", "limit": 3}, op_ctx)
    assert [j["id"] for j in page["items"]] == ids[::-1][:3] and page["next_cursor"] == ids[2]
    page2 = await ops.invoke("job.list", {"lane": "A", "limit": 3, "cursor": page["next_cursor"]}, op_ctx)
    assert [j["id"] for j in page2["items"]] == ids[1::-1] and page2["next_cursor"] is None
    by_type = await ops.invoke("job.list", {"type": "test.boom"}, op_ctx)
    assert [j["id"] for j in by_type["items"]] == [other]
    by_proj = await ops.invoke("job.list", {"project_id": 2, "status": "queued"}, op_ctx)
    assert [j["id"] for j in by_proj["items"]] == [other]
    viewer = await ops.invoke("job.list", {}, ctx_for(svc, role="viewer"))
    assert len(viewer["items"]) == 6
    got = await ops.invoke("job.get", {"id": other}, op_ctx)
    assert got["type"] == "test.boom" and got["lane"] == "B" and got["status"] == "queued"
    with pytest.raises(AppError) as ei:
        await ops.invoke("job.get", {"id": 9999}, op_ctx)
    assert ei.value.code == "not_found"


async def test_job_logs_op_pages_after_id(svc, op_ctx):
    a = enqueue_job(svc, "test.logs", {})
    await run_until_idle(svc)
    first = await ops.invoke("job.logs", {"id": a, "limit": 2}, op_ctx)
    assert [r["message"] for r in first["items"]] == ["dòng 0", "dòng 1"]
    rest = await ops.invoke("job.logs", {"id": a, "after_id": first["items"][-1]["id"]}, op_ctx)
    assert [r["message"] for r in rest["items"]] == ["dòng 2", "dòng 3", "dòng 4"]


async def test_job_retry_resets_and_merges_params(svc, op_ctx):
    a = enqueue_job(svc, "test.boom", {"prompt": "cũ", "seed": 1})
    await run_until_idle(svc)
    assert status(svc, a) == "failed"
    out = await ops.invoke("job.retry", {"ids": [a], "params_patch": {"prompt": "mới"}}, op_ctx)
    assert [j["id"] for j in out["items"]] == [a]
    job = load(svc, a)
    assert job.status == "queued" and job.attempt == 0 and job.error_code is None and job.finished_at is None
    assert job.params == {"prompt": "mới", "seed": 1}


async def test_job_retry_refuses_active_and_attention(svc, op_ctx):
    q = enqueue_job(svc, "test.sleep", {"s": 0})
    att = insert_job(svc, "test.sleep", status="needs_attention", external_state={"state": "submitting"})
    for jid in (q, att):
        with pytest.raises(AppError) as ei:
            await ops.invoke("job.retry", {"ids": [jid]}, op_ctx)
        assert ei.value.code == "conflict"
    assert load(svc, att).external_state == {"state": "submitting"}


async def test_job_retry_never_wipes_submitting_marker(svc, op_ctx):
    marker = {"state": "submitting"}
    for st in ("failed", "cancelled", "interrupted", "needs_attention"):
        jid = insert_job(svc, "test.sleep", status=st, external_state=marker)
        for extra in ({}, {"force_new": True}):  # force_new cũng không được xoá dấu
            with pytest.raises(AppError) as ei:
                await ops.invoke("job.retry", {"ids": [jid], **extra}, op_ctx)
            assert ei.value.code == "conflict" and "job.clear_attention" in ei.value.hint
            assert load(svc, jid).external_state == marker and status(svc, jid) == st
    # đường duy nhất: clear_attention(resubmit) — kể cả job đã dừng còn mang dấu
    cancelled = insert_job(svc, "test.sleep", status="cancelled", external_state=marker, attempt=2)
    out = await ops.invoke("job.clear_attention", {"id": cancelled, "action": "resubmit"}, op_ctx)
    # chỉ mang dấu: giữ thế hệ (gen 0 = {}) → cùng clientRef, provider tự chống trùng nếu đã tạo job
    assert out["status"] == "queued" and out["external_state"] == {} and out["attempt"] == 0
    # job đã dừng không mang dấu thì clear_attention vẫn từ chối
    plain = insert_job(svc, "test.sleep", status="failed")
    with pytest.raises(AppError):
        await ops.invoke("job.clear_attention", {"id": plain, "action": "resubmit"}, op_ctx)


@pytest.mark.parametrize("st", sorted(jobs.RETRYABLE))
async def test_job_retry_keeps_accepted_provider_job_unless_force_new(svc, op_ctx, st):
    """Provider đã nhận job (`external_id`): chạy lại giữ `external_*` để nối lại đúng job đó (không trả tiền lần
    hai); `force_new` mới xoá để tạo job trả phí mới."""
    ext = {"state": "submitted", "client_ref": "r-1"}
    kept = insert_job(svc, "test.sleep", status=st, external_id="p-1", external_state=ext, attempt=2)
    if st == "succeeded":  # đã xong mà nối lại = tải lại kết quả cũ → từ chối, chỉ force_new
        with pytest.raises(AppError) as ei:
            await ops.invoke("job.retry", {"ids": [kept]}, op_ctx)
        assert (ei.value.code, ei.value.status) == ("already_succeeded", 409) and "force_new" in ei.value.hint
        assert status(svc, kept) == "succeeded" and load(svc, kept).external_state == ext
    else:
        out = (await ops.invoke("job.retry", {"ids": [kept]}, op_ctx))["items"][0]
        # nối lại job NGƯỜI DÙNG đã huỷ: ghi cờ để adapter báo đúng ("đã huỷ trước đó", gợi ý force_new)
        resumed = {**ext, "user_cancelled": True} if st == "cancelled" else ext
        assert (out["status"], out["attempt"], out["external_id"], out["external_state"]) == (
            "queued", 0, "p-1", resumed)
        if st == "cancelled":  # nối lại thêm lần nữa (vd. sau failed/provider_cancelled) → cờ vẫn còn
            with svc.db.session() as s:
                s.get(Job, kept).status = "failed"
            out = (await ops.invoke("job.retry", {"ids": [kept]}, op_ctx))["items"][0]
            assert out["external_state"] == resumed
            with svc.db.session() as s:
                s.get(Job, kept).status = "failed"
            out = (await ops.invoke("job.retry", {"ids": [kept], "force_new": True}, op_ctx))["items"][0]
            assert out["external_state"] == {"gen": 1}  # lần gửi mới: không còn cờ
    fresh = insert_job(svc, "test.sleep", status=st, external_id="p-2", external_state=ext)
    out = (await ops.invoke("job.retry", {"ids": [fresh], "force_new": True}, op_ctx))["items"][0]
    assert (out["status"], out["external_id"], out["external_state"]) == ("queued", None, {"gen": 1})
    assert "trả phí mới" in logs(svc, fresh)
    # Provider không giữ job: như cũ — trạng thái provider cũ bị xoá (dấu đã xoá, client_ref…), chỉ giữ thế hệ `gen`
    plain = insert_job(svc, "test.sleep", status=st, external_state={"state": None, "client_ref": "r-3", "gen": 2})
    out = (await ops.invoke("job.retry", {"ids": [plain]}, op_ctx))["items"][0]
    assert (out["external_id"], out["external_state"]) == (None, {"gen": 2})


OPENAI_DONE = {"state": "completed", "result_file": "out/a.png"}  # adapter đồng bộ: đã tạo (đã trả tiền), không id


@pytest.mark.parametrize("st", ["failed", "cancelled", "interrupted"])
async def test_retry_of_job_held_by_state_without_external_id(svc, op_ctx, st):
    """`state` khác None/submitting (vd. openai_images `completed`) = provider giữ job dù không có external_id."""
    kept = insert_job(svc, "test.sleep", status=st, external_state=OPENAI_DONE, params={"prompt": "cũ"})
    _add_spend(svc, kept)
    out = (await ops.invoke("job.retry", {"ids": [kept]}, op_ctx))["items"][0]
    resumed = {**OPENAI_DONE, "user_cancelled": True} if st == "cancelled" else OPENAI_DONE
    assert (out["status"], out["external_state"]) == ("queued", resumed)  # nối lại: dùng file đã tạo
    assert _spend_rows(svc) == [(kept, 1.0)]
    with svc.db.session() as s:
        s.get(Job, kept).status = st
    with pytest.raises(AppError) as ei:
        await ops.invoke("job.retry", {"ids": [kept], "params_patch": {"prompt": "mới"}}, op_ctx)
    assert ei.value.code == "params_patch_requires_new_job"
    out = (await ops.invoke("job.retry", {"ids": [kept], "force_new": True}, op_ctx))["items"][0]
    assert out["external_state"] == {"gen": 1}
    assert _spend_rows(svc) == [(kept, 1.0)]  # sổ chi tiêu không bao giờ bị sửa/xoá khi chạy lại


async def test_retry_succeeded_job_held_by_state_needs_force_new(svc, op_ctx):
    done = insert_job(svc, "test.sleep", status="succeeded", external_state=OPENAI_DONE)
    with pytest.raises(AppError) as ei:
        await ops.invoke("job.retry", {"ids": [done]}, op_ctx)
    assert ei.value.code == "already_succeeded"
    out = (await ops.invoke("job.retry", {"ids": [done], "force_new": True}, op_ctx))["items"][0]
    assert (out["status"], out["external_state"]) == ("queued", {"gen": 1})


@pytest.mark.parametrize("action", ["resubmit", "fail"])
async def test_clear_attention_of_job_held_by_state_keeps_spend(svc, op_ctx, action):
    a = insert_job(svc, "test.sleep", status="needs_attention", external_state=OPENAI_DONE)
    _add_spend(svc, a)
    out = await ops.invoke("job.clear_attention", {"id": a, "action": action}, op_ctx)
    assert _spend_rows(svc) == [(a, 1.0)]
    assert out["external_state"] == ({"gen": 1} if action == "resubmit" else {})


async def test_generation_is_monotonic_across_resets(svc, op_ctx):
    """`gen` chỉ tăng và không bao giờ mất khi đặt lại: `force_new` luôn tăng; `clear_attention(resubmit)` chỉ tăng
    khi provider giữ job — job chỉ mang dấu `submitting` giữ nguyên `gen` (cùng clientRef → provider tự chống trùng nếu
    thật ra đã tạo job)."""
    a = insert_job(svc, "test.sleep", status="failed", external_id="p-1", external_state={"state": "submitted"})
    for expected in (1, 2):
        out = (await ops.invoke("job.retry", {"ids": [a], "force_new": True}, op_ctx))["items"][0]
        assert out["external_state"] == {"gen": expected}
        with svc.db.session() as s:
            s.get(Job, a).status = "failed"
    out = (await ops.invoke("job.retry", {"ids": [a], "force_new": True}, op_ctx))["items"][0]  # không giữ job
    assert out["external_state"] == {"gen": 3}
    with svc.db.session() as s:
        j = s.get(Job, a)
        j.status, j.external_state = "needs_attention", {"gen": 3, "state": "submitting"}
    out = await ops.invoke("job.clear_attention", {"id": a, "action": "resubmit"}, op_ctx)
    assert out["external_state"] == {"gen": 3}  # chỉ mang dấu: giữ thế hệ
    with svc.db.session() as s:
        j = s.get(Job, a)
        j.status, j.external_state = "needs_attention", {"gen": 3, "state": "submitting", "client_ref": "r:g3:ab12",
                                                         "fingerprint": "f"}
    out = await ops.invoke("job.clear_attention", {"id": a, "action": "resubmit"}, op_ctx)
    assert out["external_state"] == {"gen": 3, "client_ref": "r:g3:ab12"}  # gửi lại đúng ref của lần POST mập mờ
    with svc.db.session() as s:
        j = s.get(Job, a)
        j.status, j.external_id, j.external_state = "needs_attention", "p-7", {"gen": 3, "state": "submitted"}
    out = await ops.invoke("job.clear_attention", {"id": a, "action": "resubmit"}, op_ctx)
    assert out["external_state"] == {"gen": 4}  # provider giữ job (vd. remote_cancelled): thế hệ mới
    with svc.db.session() as s:
        j = s.get(Job, a)
        j.status, j.external_id, j.external_state = "needs_attention", "p-8", {"gen": 4, "state": "submitted"}
    out = await ops.invoke("job.clear_attention", {"id": a, "action": "fail"}, op_ctx)
    assert out["external_state"] == {"gen": 4}


async def test_job_retry_params_patch_on_accepted_job_requires_force_new(svc, op_ctx):
    """Provider đã nhận job: sửa tham số mà nối lại job cũ thì vô nghĩa → 422, phải chọn force_new (job mới)."""
    a = insert_job(svc, "test.sleep", status="failed", external_id="p-1", external_state={"state": "submitted"},
                   params={"prompt": "cũ"})
    with pytest.raises(AppError) as ei:
        await ops.invoke("job.retry", {"ids": [a], "params_patch": {"prompt": "mới"}}, op_ctx)
    assert (ei.value.code, ei.value.status) == ("params_patch_requires_new_job", 422)
    assert "force_new" in ei.value.hint and "trả phí mới" in ei.value.hint
    job = load(svc, a)
    assert (job.status, job.params, job.external_id) == ("failed", {"prompt": "cũ"}, "p-1")
    out = (await ops.invoke("job.retry", {"ids": [a], "params_patch": {"prompt": "mới"}, "force_new": True},
                            op_ctx))["items"][0]
    assert (out["status"], out["params"], out["external_id"]) == ("queued", {"prompt": "mới"}, None)


def _flags(d: dict) -> tuple[bool, bool]:
    return d["provider_holds"], d["needs_decision"]


async def test_job_dict_exposes_provider_holds_and_needs_decision(svc, op_ctx):
    """UI theo đúng luật backend: `provider_holds` (= provider_holds_job) và `needs_decision` (job đã dừng, không thành
    công, còn dấu submitting chưa có external_id → chỉ job.clear_attention xử lý được) có trong job.get/list."""
    # openai_images-style: API đồng bộ đã tạo xong (state completed, không id) → provider giữ job
    openai_done = insert_job(svc, "test.sleep", status="succeeded", external_state=OPENAI_DONE)
    openai_failed = insert_job(svc, "test.sleep", status="failed", external_state=OPENAI_DONE)
    # bị huỷ đúng lúc đang POST: còn dấu submitting, không id → cần quyết định (clear_attention)
    cancelled_mid_post = insert_job(svc, "test.sleep", status="cancelled", external_state=MARKER)
    attention = insert_job(svc, "test.sleep", status="needs_attention", external_state=MARKER)
    # provider đã nhận (có id) → giữ job; force_new tạo job mới → không còn giữ
    held = insert_job(svc, "test.sleep", status="failed", external_id="p-1", external_state={"state": "submitted"})
    plain = insert_job(svc, "test.sleep", status="failed")
    legacy_done = insert_job(svc, "test.sleep", status="succeeded", external_state=MARKER)  # dấu sót, đã xong
    expected = {
        openai_done: (True, False), openai_failed: (True, False), cancelled_mid_post: (False, True),
        attention: (False, False), held: (True, False), plain: (False, False), legacy_done: (False, False),
    }
    for jid, flags in expected.items():
        assert _flags(await ops.invoke("job.get", {"id": jid}, op_ctx)) == flags, jid
    listed = {j["id"]: _flags(j) for j in (await ops.invoke("job.list", {}, op_ctx))["items"]}
    assert listed == expected
    out = (await ops.invoke("job.retry", {"ids": [held], "force_new": True}, op_ctx))["items"][0]
    assert _flags(out) == (False, False)
    out = await ops.invoke("job.clear_attention", {"id": cancelled_mid_post, "action": "resubmit"}, op_ctx)
    assert _flags(out) == (False, False)


async def test_running_job_events_carry_fresh_provider_holds(svc, capture):
    a = enqueue_job(svc, "test.held_progress", {})
    await run_until_idle(svc)
    events = [d for t, d in capture if t == "job.updated" and d["id"] == a]
    assert all("provider_holds" in d and "needs_decision" in d for d in events)
    assert events[0]["provider_holds"] is False  # vừa xếp hàng
    polled = [d for d in events if d["status"] == "running" and d["stage"] == "poll"]
    assert polled and all(d["provider_holds"] is True and d["needs_decision"] is False for d in polled)
    assert events[-1]["status"] == "succeeded" and events[-1]["provider_holds"] is True


@pytest.mark.parametrize("how", ["retry", "retry_force_new", "resubmit"])
@pytest.mark.parametrize("patch", [{"connection_id": 2}, {"aspec": "16:9"}, {"prompt": "mới", "connection_id": 2}])
async def test_params_patch_rejects_connection_and_unknown_keys(svc, op_ctx, how, patch):
    """`params_patch` chỉ sửa tham số job đã có; đổi kết nối (`connection_id`) hay thêm khoá lạ → 422 `params_patch_invalid`
    (đổi kết nối = tạo job mới), job giữ nguyên."""
    params = {"prompt": "cũ", "connection_id": 1}
    if how == "resubmit":
        a = insert_job(svc, "test.sleep", status="needs_attention", external_state=MARKER, params=params)
        call = ops.invoke("job.clear_attention", {"id": a, "action": "resubmit", "params_patch": patch}, op_ctx)
    else:
        a = insert_job(svc, "test.sleep", status="failed", params=params)
        extra = {"force_new": True} if how == "retry_force_new" else {}
        call = ops.invoke("job.retry", {"ids": [a], "params_patch": patch, **extra}, op_ctx)
    with pytest.raises(AppError) as ei:
        await call
    assert (ei.value.code, ei.value.status) == ("params_patch_invalid", 422)
    assert "job mới" in ei.value.hint
    job = load(svc, a)
    assert job.params == params and job.status in ("failed", "needs_attention")
    assert job.external_state == (MARKER if how == "resubmit" else {})


async def test_params_patch_with_known_keys_still_allowed(svc, op_ctx):
    a = insert_job(svc, "test.sleep", status="failed", params={"prompt": "cũ", "aspect": "9:16", "connection_id": 1})
    out = (await ops.invoke("job.retry", {"ids": [a], "params_patch": {"prompt": "mới", "aspect": "16:9"}},
                            op_ctx))["items"][0]
    assert out["params"] == {"prompt": "mới", "aspect": "16:9", "connection_id": 1}


def _spend_rows(svc) -> list[tuple[int | None, float]]:
    with svc.db.session() as s:
        return [(r.job_id, r.units) for r in s.scalars(select(Spend).order_by(Spend.id))]


def _add_spend(svc, job_id) -> None:
    with svc.db.session() as s:
        s.add(Spend(kind="image", units=1.0, job_id=job_id, connection_id=None))


@pytest.mark.parametrize("cols, extra", [
    ({"status": "succeeded"}, {}),
    ({"status": "succeeded", "external_id": "p-1"}, {"force_new": True}),
    ({"status": "failed", "external_id": "p-1"}, {"force_new": True}),
    ({"status": "failed", "external_id": "p-1"}, {}),
    ({"status": "interrupted"}, {}),
])
async def test_job_retry_never_touches_spend(svc, op_ctx, cols, extra):
    """Sổ chi tiêu chỉ để thống kê: chạy lại (nối lại hay gửi mới) không bao giờ sửa/xoá dòng đã ghi."""
    a = insert_job(svc, "test.sleep", external_state={"state": "submitted"} if cols.get("external_id") else {},
                   **cols)
    _add_spend(svc, a)
    await ops.invoke("job.retry", {"ids": [a], **extra}, op_ctx)
    assert _spend_rows(svc) == [(a, 1.0)]


@pytest.mark.parametrize("cols, action", [
    ({"external_id": "p-1", "external_state": {"state": "submitted"}}, "resubmit"),
    ({"external_id": "p-1", "external_state": {"state": "submitted"}}, "fail"),
    ({"external_state": {"state": "submitting"}}, "fail"),
    ({"external_state": {"state": "submitting"}}, "resubmit"),
])
async def test_clear_attention_never_touches_spend(svc, op_ctx, cols, action):
    a = insert_job(svc, "test.sleep", status="needs_attention", **cols)
    _add_spend(svc, a)
    await ops.invoke("job.clear_attention", {"id": a, "action": action}, op_ctx)
    assert _spend_rows(svc) == [(a, 1.0)]


def test_job_retry_force_new_param_documented():
    ops_pkg.load_all()
    field = ops.REGISTRY["job.retry"].params.model_fields["force_new"]
    assert field.default is False and "trả phí mới" in field.description


async def test_job_clear_attention(svc, op_ctx):
    a = insert_job(svc, "test.resumable", status="needs_attention", error_code="provider_job_unknown",
                   external_state={"state": "submitting"})
    b = insert_job(svc, "test.resumable", status="needs_attention", external_state={"state": "submitting"})
    await ops.invoke("job.clear_attention", {"id": a, "action": "resubmit"}, op_ctx)
    await ops.invoke("job.clear_attention", {"id": b, "action": "fail"}, op_ctx)
    ja, jb = load(svc, a), load(svc, b)
    # resubmit job chỉ mang dấu: giữ thế hệ (cùng clientRef → provider tự chống trùng)
    assert ja.status == "queued" and ja.external_state == {} and ja.external_id is None and ja.error_code is None
    assert jb.status == "failed" and jb.external_state == {} and jb.finished_at is not None
    with pytest.raises(AppError) as ei:
        await ops.invoke("job.clear_attention", {"id": a, "action": "fail"}, op_ctx)
    assert ei.value.code == "conflict"


async def test_job_prioritize_and_cancel_ops(svc, op_ctx):
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    b = insert_job(svc, "test.sleep", status="succeeded")
    out = await ops.invoke("job.prioritize", {"ids": [a, b], "priority": 9}, op_ctx)
    assert [j["id"] for j in out["items"]] == [a]
    assert load(svc, a).priority == 9 and load(svc, b).priority == 0
    res = await ops.invoke("job.cancel", {"ids": [a, b]}, op_ctx)
    assert res == {"cancelled": [a], "skipped": [b]}


async def test_pause_all_blocks_dispatch_and_persists(svc, op_ctx):
    await ops.invoke("job.pause_all", {}, op_ctx)
    a = enqueue_job(svc, "test.sleep", {"s": 0})
    await svc.worker.start()
    try:
        await asyncio.sleep(0.2)
        assert status(svc, a) == "queued"
        # Khởi động lại: worker mới (chưa chạy) nạp lại trạng thái tạm dừng
        fresh = worker_mod.Worker(svc, lane_limit=svc.lane_limit)
        assert fresh.paused is False
        await anyio.to_thread.run_sync(fresh.recover)
        assert fresh.paused is True
        await ops.invoke("job.resume_all", {}, op_ctx)
        await wait_until(lambda: status(svc, a) == "succeeded")
    finally:
        await svc.worker.stop()


async def test_job_wait_returns_when_done(svc, op_ctx):
    a = enqueue_job(svc, "test.sleep", {"s": 0.05})
    await svc.worker.start()
    try:
        out = await ops.invoke("job.wait", {"ids": [a], "timeout_s": 5}, op_ctx)
    finally:
        await svc.worker.stop()
    assert out["timed_out"] is False and out["items"][0]["status"] == "succeeded"


def test_job_wait_timeout_bounds(svc, op_ctx):
    spec = ops.REGISTRY["job.wait"]
    with pytest.raises(AppError):
        ops.parse_params(spec, {"ids": [1], "timeout_s": 601})
    assert ops.parse_params(spec, {"ids": [1]}).timeout_s == 60


# --- App / lifespan -----------------------------------------------------------------------------------------------


def test_production_registry_has_no_test_handlers():
    assert not [t for t in jobs.HANDLERS if t.startswith("test.")]


def test_job_wait_times_out(settings, test_job_handlers, make_local_client):
    with make_local_client(create_app(settings)) as client:
        services = client.app.state.services
        a = enqueue_job(services, "test.sleep", {"s": 30})
        r = client.post("/api/ops/job.wait", json={"ids": [a], "timeout_s": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["timed_out"] is True and body["items"][0]["status"] == "running"
    # tắt app: job không nối lại được → interrupted
    db = Database(settings.db_path)
    try:
        with db.session() as s:
            assert s.get(Job, a).status == "interrupted"
    finally:
        db.dispose()


def test_lifespan_recovers_then_starts_worker(settings, test_job_handlers, make_local_client):
    db = Database(settings.db_path)
    db.migrate()
    with db.session() as s:
        stuck = Job(type="test.sleep", status="running", lane="L", params={})
        resumable = Job(type="test.resumable", status="running", lane="L", params={})
        s.add_all([stuck, resumable])
    db.dispose()
    with make_local_client(create_app(settings)) as client:
        services = client.app.state.services
        assert services.worker.started
        r = client.post("/api/ops/job.wait", json={"ids": [stuck.id, resumable.id], "timeout_s": 5})
        items = {j["id"]: j["status"] for j in r.json()["items"]}
        assert items == {stuck.id: "interrupted", resumable.id: "succeeded"}
    assert not services.worker.started


async def test_datetime_in_result_is_serialized_iso(svc):
    from datetime import datetime, timezone

    from sagareel.services import jobs as jobs_mod

    async def dt_handler(run):
        return {"at": datetime(2026, 11, 20, 1, 2, 3, tzinfo=timezone.utc)}

    jobs_mod.job_handler("test.dt_result")(dt_handler)
    try:
        a = enqueue_job(svc, "test.dt_result", {})
        await run_until_idle(svc)
        job = load(svc, a)
        assert job.status == "succeeded" and job.result == {"at": "2026-11-20T01:02:03+00:00"}
    finally:
        jobs_mod.unregister_handler("test.dt_result") if hasattr(jobs_mod, "unregister_handler") else jobs_mod.HANDLERS.pop("test.dt_result", None)
