import asyncio
import threading
from datetime import datetime, timedelta, timezone

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from sagareel import ops as ops_pkg
from sagareel.app import create_app
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.db.models import Job, JobLog
from sagareel.db.session import Database
from sagareel.services import jobs
from sagareel.services import worker as worker_mod

# --- Handler test bổ sung (chỉ module này) --------------------------------------------------------------------------

EXTRA_TYPES = (
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
        await run.save_external("ext-1", phase="submitting")
        await probe.gate(run.job_id).wait()
        try:
            await run.save_external(phase="submitted")
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
        job = Job(type=type, status=status, lane=lane, params={}, **cols)
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
                   external_state={"phase": "submitted"}, attempt=1)
    b = insert_job(services, "test.sleep", status="running")
    c = insert_job(services, "test.sleep", status="queued")
    services.worker.recover()
    assert status(services, a) == "queued" and status(services, b) == "interrupted"
    ja, jb = load(services, a), load(services, b)
    assert ja.external_id == "x-1" and ja.external_state == {"phase": "submitted"} and ja.attempt == 1
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
    a = insert_job(svc, "test.resumable", status="running", external_id="x-9", external_state={"phase": "polling"})
    svc.worker.recover()
    await run_until_idle(svc)
    job = load(svc, a)
    assert job.status == "succeeded" and job.result == {"external_id": "x-9", "external": {"phase": "polling"}}


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
        assert load(svc, a).external_state == {"phase": "submitting"}
        svc.worker_probe.gate(a).set()
        await wait_until(lambda: status(svc, a) == "succeeded")
    finally:
        await svc.worker.stop()
    assert load(svc, a).external_state == {"phase": "submitted"}


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
    assert load(svc, a).external_state == {"phase": "submitting"}


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
    att = insert_job(svc, "test.sleep", status="needs_attention", external_state={"phase": "submitting"})
    for jid in (q, att):
        with pytest.raises(AppError) as ei:
            await ops.invoke("job.retry", {"ids": [jid]}, op_ctx)
        assert ei.value.code == "conflict"
    assert load(svc, att).external_state == {"phase": "submitting"}


async def test_job_retry_never_wipes_submitting_marker(svc, op_ctx):
    marker = {"state": "submitting"}
    for st in ("failed", "cancelled", "interrupted", "needs_attention"):
        jid = insert_job(svc, "test.sleep", status=st, external_state=marker)
        with pytest.raises(AppError) as ei:
            await ops.invoke("job.retry", {"ids": [jid]}, op_ctx)
        assert ei.value.code == "conflict" and "job.clear_attention" in ei.value.hint
        assert load(svc, jid).external_state == marker and status(svc, jid) == st
    # đường duy nhất: clear_attention(resubmit) — kể cả job đã dừng còn mang dấu
    cancelled = insert_job(svc, "test.sleep", status="cancelled", external_state=marker, attempt=2)
    out = await ops.invoke("job.clear_attention", {"id": cancelled, "action": "resubmit"}, op_ctx)
    assert out["status"] == "queued" and out["external_state"] == {} and out["attempt"] == 0
    # job đã dừng không mang dấu thì clear_attention vẫn từ chối
    plain = insert_job(svc, "test.sleep", status="failed")
    with pytest.raises(AppError):
        await ops.invoke("job.clear_attention", {"id": plain, "action": "resubmit"}, op_ctx)


async def test_job_clear_attention(svc, op_ctx):
    a = insert_job(svc, "test.resumable", status="needs_attention", error_code="provider_job_unknown",
                   external_state={"phase": "submitting"})
    b = insert_job(svc, "test.resumable", status="needs_attention", external_state={"phase": "submitting"})
    await ops.invoke("job.clear_attention", {"id": a, "action": "resubmit"}, op_ctx)
    await ops.invoke("job.clear_attention", {"id": b, "action": "fail"}, op_ctx)
    ja, jb = load(svc, a), load(svc, b)
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


def test_job_wait_times_out(settings, test_job_handlers):
    with TestClient(create_app(settings)) as client:
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


def test_lifespan_recovers_then_starts_worker(settings, test_job_handlers):
    db = Database(settings.db_path)
    db.migrate()
    with db.session() as s:
        stuck = Job(type="test.sleep", status="running", lane="L", params={})
        resumable = Job(type="test.resumable", status="running", lane="L", params={})
        s.add_all([stuck, resumable])
    db.dispose()
    with TestClient(create_app(settings)) as client:
        services = client.app.state.services
        assert services.worker.started
        r = client.post("/api/ops/job.wait", json={"ids": [stuck.id, resumable.id], "timeout_s": 5})
        items = {j["id"]: j["status"] for j in r.json()["items"]}
        assert items == {stuck.id: "interrupted", resumable.id: "succeeded"}
    assert not services.worker.started
