"""Worker asyncio trong tiến trình: chạy job theo lane, retry/backoff, nối lại sau khởi động lại, huỷ (spec §5.5).

Cấu trúc:
- Vòng điều phối (`_main`) mỗi `tick` giây hoặc khi được `wake()`: chọn job `queued` đã tới hạn theo
  `priority desc, id asc` **riêng từng lane** (lane đầy không chặn lane khác), giới hạn `lane_limit(lane)`, và
  "giành" job bằng UPDATE có điều kiện `status='queued'` (compare-and-set).
- Mỗi job đang chạy có hai task: task handler (bị huỷ khi `cancel`/`stop`) và task giám sát (không bao giờ bị huỷ)
  chờ handler xong rồi ghi trạng thái cuối. Huỷ hai lần hay tắt app giữa lúc huỷ không làm đứt lần ghi cuối.
- Mọi truy cập DB chạy qua `anyio.to_thread.run_sync`, mỗi lần một transaction ngắn; không giữ transaction qua
  `await` handler. Ghi log/tiến độ/`save_external` của một job đi qua một hàng đợi ghi tuần tự riêng (đúng thứ tự);
  trạng thái cuối chỉ ghi sau khi hàng đợi đã cạn. Mọi lần ghi khi đang chạy đều có điều kiện `status='running'`.
- Tắt app (`stop`): huỷ handler; job `resumable` về `queued` (nối lại lần sau), job khác thành `interrupted` — giống
  hệt `recover()` khi app chết đột ngột. Retry/khôi phục/tắt đều giữ `external_id`/`external_state`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import traceback
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import anyio
from sqlalchemy import select, update

from sagareel.core.errors import AppError
from sagareel.db.base import utcnow
from sagareel.db.models import Job
from sagareel.services import jobs
from sagareel.services.jobs import JobFailed, JobStateLost, NeedsAttention, TransientError

if TYPE_CHECKING:
    from pathlib import Path

    from sagareel.container import Services

logger = logging.getLogger("sagareel.worker")

PROGRESS_INTERVAL = 0.25  # tối đa 4 sự kiện tiến độ/giây/job
STOP_GRACE_S = 10.0
_LEVELS = ("debug", "info", "warning", "error")


def retry_delay(attempt: int, retry_after: float | None) -> float:
    """Giây chờ trước lần thử kế: `retry_after` nếu provider cho, ngược lại `min(300, 5·2^attempt)·(0.8..1.2)`."""
    if retry_after is not None:
        return max(0.0, float(retry_after))
    return min(300.0, 5.0 * 2**attempt) * random.uniform(0.8, 1.2)


def _interrupt_values(job_type: str) -> tuple[dict[str, Any], str, str]:
    """Giá trị cột cho job đang chạy mà app tắt/chết: (values, level log, message log)."""
    spec = jobs.get_handler(job_type)
    if spec is not None and spec.resumable:
        return (
            {"status": "queued", "next_attempt_at": None},
            "info",
            "Ứng dụng dừng khi job đang chạy; job sẽ được nối lại.",
        )
    return (
        {
            "status": "interrupted",
            "error_code": "interrupted",
            "error_message": "Job bị gián đoạn vì ứng dụng dừng khi đang chạy.",
            "hint": "Bấm “Chạy lại” để chạy lại job.",
        },
        "warning",
        "Job bị gián đoạn vì ứng dụng dừng khi đang chạy.",
    )


class JobRun:
    """Ngữ cảnh một lần chạy job, truyền cho handler."""

    def __init__(self, worker: Worker, data: dict[str, Any]) -> None:
        self.job_id: int = data["id"]
        self.type: str = data["type"]
        self.lane: str = data["lane"]
        self.params: dict = dict(data["params"])
        self.client_ref: str | None = data["client_ref"]
        self.external_id: str | None = data["external_id"]
        self.external: dict = dict(data["external_state"])
        self.attempt: int = data["attempt"]
        self.workdir: Path = worker.services.settings.cache_dir / "jobs" / str(self.job_id)
        self.cancelled = asyncio.Event()
        self.services: Services = worker.services

        self._worker = worker
        self._loop = asyncio.get_running_loop()
        self._data = data  # ảnh chụp cho payload `job.updated`
        self._writes: asyncio.Queue = asyncio.Queue()
        self._writer: asyncio.Task | None = None
        self._progress_handle: asyncio.TimerHandle | None = None
        self._progress_dirty = False
        self._last_progress = 0.0
        self._closed = False
        self.cancel_requested = False
        self.task: asyncio.Task | None = None  # task handler
        self.supervisor: asyncio.Task | None = None

    # --- API cho handler ----------------------------------------------------------------------------------------

    def _off_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is not self._loop
        except RuntimeError:
            return True

    def log(self, level: str, msg: str) -> None:
        """Ghi một dòng `job_log` (không chặn) và phát `job.log`. Gọi được từ thread khác."""
        if self._off_loop():
            self._loop.call_soon_threadsafe(self.log, level, msg)
            return
        if self._closed:
            return
        self._writes.put_nowait(("log", level if level in _LEVELS else "info", str(msg)))

    def progress(self, pct: float | None = None, stage: str | None = None, eta_s: float | None = None) -> None:
        """Cập nhật tiến độ (0–100), giai đoạn, ETA. Gộp: ghi DB + phát `job.updated` tối đa 4 lần/giây."""
        if self._off_loop():
            self._loop.call_soon_threadsafe(self.progress, pct, stage, eta_s)
            return
        if self._closed:
            return
        if pct is not None:
            self._data["progress"] = float(pct)
        if stage is not None:
            self._data["stage"] = stage
        if eta_s is not None:
            self._data["eta_at"] = (utcnow() + timedelta(seconds=float(eta_s))).isoformat()
        self._progress_dirty = True
        if self._progress_handle is None:
            delay = max(0.0, self._last_progress + PROGRESS_INTERVAL - time.monotonic())
            self._progress_handle = self._loop.call_later(delay, self._emit_progress)

    async def save_external(self, external_id: str | None = None, **state: Any) -> None:
        """Ghi `external_id` (nếu truyền) và gộp `state` vào `external_state` — đã commit khi hàm trả về.

        Job không còn `running` (bị huỷ/đặt lại) → ném `JobStateLost`; handler không được đi tiếp.
        """
        if self._closed:  # lần chạy đã kết thúc: không còn ai ghi
            raise JobStateLost(f"Job #{self.job_id}: lần chạy đã kết thúc; không lưu được trạng thái.")
        fut = self._loop.create_future()
        self._writes.put_nowait(("external", external_id, state, fut))
        await fut

    # --- Nội bộ -------------------------------------------------------------------------------------------------

    def _emit_progress(self) -> None:
        self._progress_handle = None
        if self._closed or not self._progress_dirty:
            return
        self._progress_dirty = False
        self._last_progress = time.monotonic()
        self._writes.put_nowait(("progress", self._data["progress"], self._data["stage"], self._data["eta_at"]))

    def _start_writer(self) -> None:
        self._writer = asyncio.create_task(self._writer_loop(), name=f"job-{self.job_id}-writer")

    async def _writer_loop(self) -> None:
        while True:
            item = await self._writes.get()
            try:
                await self._process(item)
            except Exception as e:
                logger.exception("Job #%s: không ghi được %s", self.job_id, item[0])
                if item[0] == "external" and not item[3].done():
                    item[3].set_exception(e)
            finally:
                self._writes.task_done()

    async def _process(self, item: tuple) -> None:
        db = self.services.db
        kind = item[0]
        if kind == "log":
            _, level, msg = item

            def write_log() -> dict:
                with db.session() as s:
                    return jobs.log_dict(jobs.add_log(s, self.job_id, level, msg))

            row = await anyio.to_thread.run_sync(write_log)
            self.services.bus.publish("job.log", row, project_id=self._data.get("project_id"))
        elif kind == "progress":
            _, pct, stage, eta_at = item
            values = {"progress": pct, "stage": stage, "eta_at": _parse_dt(eta_at), "updated_at": utcnow()}
            if await anyio.to_thread.run_sync(self._worker._update_running, self.job_id, values):
                self._worker._publish(dict(self._data))
        elif kind == "external":
            _, external_id, state, fut = item
            merged = {**self.external, **state}
            values: dict[str, Any] = {"external_state": merged, "updated_at": utcnow()}
            if external_id is not None:
                values["external_id"] = external_id
            ok = await anyio.to_thread.run_sync(self._worker._update_running, self.job_id, values)
            if not ok:
                if not fut.done():
                    fut.set_exception(JobStateLost(f"Job #{self.job_id} không còn chạy; không lưu được trạng thái."))
                return
            self.external = merged
            self._data["external_state"] = dict(merged)
            if external_id is not None:
                self.external_id = external_id
                self._data["external_id"] = external_id
            if not fut.done():
                fut.set_result(None)

    async def _close(self) -> None:
        """Ghi nốt mọi thứ đang chờ rồi dừng hàng đợi ghi (trước khi ghi trạng thái cuối)."""
        if self._progress_handle is not None:
            self._progress_handle.cancel()
            self._progress_handle = None
        if self._progress_dirty:
            self._progress_dirty = False
            self._writes.put_nowait(("progress", self._data["progress"], self._data["stage"], self._data["eta_at"]))
        self._closed = True
        if self._writer is not None:
            await self._writes.join()
            self._writer.cancel()
            await asyncio.gather(self._writer, return_exceptions=True)


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Worker:
    def __init__(self, services: Services, *, lane_limit: Callable[[str], int], tick: float = 0.25) -> None:
        self.services = services
        self.lane_limit = lane_limit
        self.tick = tick
        self.paused = False
        self._runs: dict[int, JobRun] = {}
        self._lane_counts: Counter[str] = Counter()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_ev: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._main_task: asyncio.Task | None = None
        self._stopping = False

    # --- Vòng đời -----------------------------------------------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._main_task is not None and not self._main_task.done()

    def running_count(self, lane: str | None = None) -> int:
        """Số handler đang chạy (của một lane, hoặc tổng)."""
        return self._lane_counts[lane] if lane is not None else sum(self._lane_counts.values())

    async def start(self) -> None:
        if self.started:
            return
        self._loop = asyncio.get_running_loop()
        self._wake_ev = asyncio.Event()
        self._lock = asyncio.Lock()
        self._stopping = False
        self._main_task = asyncio.create_task(self._main(), name="job-worker")

    async def stop(self) -> None:
        """Dừng điều phối (chờ vòng hiện tại xong), huỷ handler đang chạy và chờ chúng ghi trạng thái cuối."""
        if self._main_task is None:
            return
        self._stopping = True
        self.wake()
        try:
            await self._main_task
        finally:
            self._main_task = None
        runs = list(self._runs.values())
        for run in runs:
            if run.task is not None and not run.task.done():
                run.task.cancel()
        supervisors = [r.supervisor for r in runs if r.supervisor is not None]
        if supervisors:
            _, pending = await asyncio.wait(supervisors, timeout=STOP_GRACE_S)
            if pending:
                logger.warning("%d job chưa dừng kịp sau %.0f s khi tắt worker", len(pending), STOP_GRACE_S)

    def wake(self) -> None:
        """Đánh thức vòng điều phối ngay (an toàn từ thread bất kỳ; worker chưa chạy thì bỏ qua)."""
        loop, ev = self._loop, self._wake_ev
        if loop is None or ev is None:
            return
        try:
            if asyncio.get_running_loop() is loop:
                ev.set()
                return
        except RuntimeError:
            pass
        try:
            loop.call_soon_threadsafe(ev.set)
        except RuntimeError:  # loop đã đóng
            pass

    def pause_all(self) -> None:
        """Ngừng lấy job mới (job đang chạy vẫn chạy tiếp). Op `job.pause_all` lưu cờ này vào DB."""
        self.paused = True

    def resume_all(self) -> None:
        self.paused = False
        self.wake()

    def recover(self) -> None:
        """Gọi một lần khi khởi động, trước `start()` (đồng bộ — chạy trong thread).

        Job `running` còn sót từ lần chạy trước: handler `resumable` → `queued` (giữ `external_*`, không tăng
        `attempt`); còn lại → `interrupted` (`error_code="interrupted"`). Nạp lại cờ tạm dừng toàn bộ.
        """
        with self.services.db.session(immediate=True) as s:
            self.paused = jobs.is_paused(s)
            rows = list(s.scalars(select(Job).where(Job.status == "running").order_by(Job.id)))
            for job in rows:
                values, level, msg = _interrupt_values(job.type)
                for k, v in values.items():
                    setattr(job, k, v)
                jobs.add_log(s, job.id, level, msg)
            s.flush()
            datas = [jobs.job_dict(j) for j in rows]
        for data in datas:
            self._publish(data)

    # --- Huỷ ----------------------------------------------------------------------------------------------------

    async def cancel(self, job_id: int) -> bool:
        """Huỷ job: đang chạy → đặt `run.cancelled`, huỷ task handler, chờ ghi `cancelled`; chưa chạy → `cancelled`.

        Trả True nếu job đã/đang bị huỷ bởi yêu cầu này, False nếu job đã kết thúc (hoặc không tồn tại).
        """
        run: JobRun | None = None
        if self._lock is not None:
            async with self._lock:  # loại trừ cửa sổ "DB đã running nhưng task chưa đăng ký"
                run = self._runs.get(job_id)
                if run is None:
                    data = await anyio.to_thread.run_sync(self._cancel_idle, job_id)
        else:
            data = await anyio.to_thread.run_sync(self._cancel_idle, job_id)
        if run is None:
            if data is None:
                return False
            self._publish(data)
            return True
        if not run.task.done() and not run.cancel_requested:
            run.cancel_requested = True
            run.cancelled.set()
            run.task.cancel()
        await asyncio.wait([run.supervisor])
        return run.cancel_requested

    def _cancel_idle(self, job_id: int) -> dict | None:
        with self.services.db.session() as s:
            job = jobs.cancel_idle(s, job_id)
            return jobs.job_dict(job) if job is not None else None

    # --- Điều phối ----------------------------------------------------------------------------------------------

    async def _main(self) -> None:
        assert self._wake_ev is not None
        while not self._stopping:
            try:
                await self._dispatch()
            except Exception:
                logger.exception("Vòng điều phối job lỗi; thử lại ở nhịp sau")
            if self._stopping:
                break
            try:
                await asyncio.wait_for(self._wake_ev.wait(), self.tick)
            except TimeoutError:
                pass
            self._wake_ev.clear()

    async def _dispatch(self) -> None:
        if self.paused:
            return
        assert self._lock is not None
        async with self._lock:
            counts = dict(self._lane_counts)
            claimed = await anyio.to_thread.run_sync(self._claim, counts)
            if self._stopping and claimed:  # đang tắt: trả job vừa giành về hàng đợi, không chạy
                await anyio.to_thread.run_sync(self._unclaim, [d["id"] for d in claimed])
                return
            for data in claimed:
                self._launch(data)

    def _claim(self, counts: dict[str, int]) -> list[dict]:
        """(Thread) Giành job tới hạn cho từng lane còn chỗ. `lane_limit` gọi ngoài transaction ghi."""
        db = self.services.db
        now = utcnow()
        due = (Job.status == "queued") & (Job.next_attempt_at.is_(None) | (Job.next_attempt_at <= now))
        with db.session() as s:
            lanes = list(s.scalars(select(Job.lane).where(due).distinct()))
        free = {}
        for lane in lanes:
            try:
                n = self.lane_limit(lane) - counts.get(lane, 0)
            except Exception:  # một lane lỗi cấu hình không được chặn các lane khác
                logger.exception("Không đọc được giới hạn song song của lane %r; bỏ qua lane ở nhịp này", lane)
                continue
            if n > 0:
                free[lane] = n
        if not free:
            return []
        claimed: list[dict] = []
        with db.session() as s:
            for lane, n in free.items():
                ids = list(s.scalars(
                    select(Job.id).where(due, Job.lane == lane).order_by(Job.priority.desc(), Job.id).limit(n)
                ))
                for job_id in ids:
                    res = s.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.status == "queued")
                        .values(status="running", started_at=now, finished_at=None, next_attempt_at=None,
                                updated_at=now)
                        .execution_options(synchronize_session=False)
                    )
                    if res.rowcount == 1:
                        job = s.get(Job, job_id)
                        s.refresh(job)
                        claimed.append(jobs.job_dict(job))
        for data in claimed:
            (self.services.settings.cache_dir / "jobs" / str(data["id"])).mkdir(parents=True, exist_ok=True)
        return claimed

    def _unclaim(self, ids: list[int]) -> None:
        with self.services.db.session() as s:
            s.execute(
                update(Job)
                .where(Job.id.in_(ids), Job.status == "running")
                .values(status="queued", updated_at=utcnow())
                .execution_options(synchronize_session=False)
            )

    def _launch(self, data: dict) -> None:
        run = JobRun(self, data)
        self._runs[run.job_id] = run
        self._lane_counts[run.lane] += 1
        self._publish(data)
        run._start_writer()
        run.task = asyncio.create_task(self._call_handler(run), name=f"job-{run.job_id}")
        run.supervisor = asyncio.create_task(self._supervise(run), name=f"job-{run.job_id}-supervisor")

    @staticmethod
    async def _call_handler(run: JobRun) -> Any:
        spec = jobs.get_handler(run.type)
        if spec is None:
            raise JobFailed(
                "unknown_job_type",
                f"Không có handler cho loại job '{run.type}'.",
                hint="Loại job này không còn được hỗ trợ hoặc module xử lý chưa được nạp.",
            )
        return await spec.fn(run)

    async def _supervise(self, run: JobRun) -> None:
        """Không bao giờ bị huỷ: chờ handler, rồi ghi trạng thái cuối."""
        assert run.task is not None
        try:
            try:
                outcome: tuple = ("ok", await run.task)
            except asyncio.CancelledError:
                outcome = ("cancelled",) if run.cancel_requested else ("shutdown",)
            except BaseException as e:  # noqa: BLE001 — mọi lỗi handler đều thành trạng thái job
                outcome = ("error", e)
            finally:
                self._lane_counts[run.lane] -= 1
                if self._lane_counts[run.lane] <= 0:
                    del self._lane_counts[run.lane]
                self.wake()
            if run.cancel_requested:
                outcome = ("cancelled",)
            await run._close()
            values, logs = self._final_values(run, outcome)
            data = await anyio.to_thread.run_sync(self._write_final, run.job_id, values, logs)
            if data is not None:
                self._publish(data)
        except Exception:
            logger.exception("Không ghi được trạng thái cuối của job #%s", run.job_id)
        finally:
            self._runs.pop(run.job_id, None)

    def _final_values(self, run: JobRun, outcome: tuple) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        now = utcnow()
        kind = outcome[0]
        if kind == "cancelled":
            return {"status": "cancelled", "finished_at": now}, [("info", "Đã huỷ.")]
        if kind == "shutdown":
            values, level, msg = _interrupt_values(run.type)
            return values, [(level, msg)]
        if kind == "ok":
            result = outcome[1]
            if result is None:
                result = {}
            if isinstance(result, dict):
                return {
                    "status": "succeeded", "result": result, "progress": 100.0, "eta_at": None,
                    "error_code": None, "error_message": None, "hint": None, "finished_at": now,
                }, []
            outcome = ("error", TypeError(f"Handler job phải trả dict, nhận {type(result).__name__}"))
        e = outcome[1]
        if isinstance(e, TransientError):
            attempt = run.attempt + 1
            if attempt >= self._max_attempts(run):
                return {
                    "status": "failed", "attempt": attempt, "error_code": "retries_exhausted",
                    "error_message": e.msg, "hint": "Đã thử lại hết số lần cho phép; kiểm tra provider rồi chạy lại.",
                    "finished_at": now,
                }, [("error", f"Lỗi tạm thời, đã hết lượt thử lại: {e.msg}")]
            delay = retry_delay(run.attempt, e.retry_after)
            return {
                "status": "queued", "attempt": attempt, "next_attempt_at": now + timedelta(seconds=delay),
                "error_message": e.msg,
            }, [("warning", f"Lỗi tạm thời: {e.msg}. Thử lại lần {attempt} sau {delay:.1f} s.")]
        if isinstance(e, NeedsAttention):
            return {
                "status": "needs_attention", "error_code": e.code, "error_message": e.msg, "hint": e.hint,
            }, [("warning", f"Cần xử lý ({e.code}): {e.msg}")]
        if isinstance(e, JobFailed):
            return {
                "status": "failed", "error_code": e.code, "error_message": e.msg, "hint": e.hint, "finished_at": now,
            }, [("error", f"Thất bại ({e.code}): {e.msg}")]
        if isinstance(e, AppError):
            return {
                "status": "failed", "error_code": e.code, "error_message": e.detail, "hint": e.hint,
                "finished_at": now,
            }, [("error", f"Thất bại ({e.code}): {e.detail}")]
        tb = "".join(traceback.format_exception(e))
        logger.error("Job #%s (%s) lỗi không lường trước:\n%s", run.job_id, run.type, tb)
        return {
            "status": "failed", "error_code": "internal_error",
            "error_message": f"Lỗi nội bộ không lường trước: {type(e).__name__}: {e}",
            "hint": "Xem nhật ký job để biết chi tiết.", "finished_at": now,
        }, [("error", tb)]

    @staticmethod
    def _max_attempts(run: JobRun) -> int:
        return int(run._data.get("max_attempts") or 1)

    # --- Ghi DB (thread) ----------------------------------------------------------------------------------------

    # Ghi có điều kiện "job vẫn running": đọc-kiểm-ghi trong phiên `immediate` (khoá ghi từ câu đầu) nên không ai
    # chen giữa được; transaction chỉ dài vài câu lệnh.

    def _update_running(self, job_id: int, values: dict[str, Any]) -> bool:
        with self.services.db.session(immediate=True) as s:
            job = s.get(Job, job_id)
            if job is None or job.status != "running":
                return False
            for k, v in values.items():
                setattr(job, k, v)
            return True

    def _write_final(self, job_id: int, values: dict[str, Any], logs: list[tuple[str, str]]) -> dict | None:
        """Trạng thái cuối + log trong một transaction; chỉ khi job vẫn `running`. Trả `job_dict` hoặc None."""
        with self.services.db.session(immediate=True) as s:
            job = s.get(Job, job_id)
            if job is None or job.status != "running":
                logger.info("Job #%s đã bị đổi trạng thái bên ngoài worker; bỏ qua kết quả lần chạy", job_id)
                return None
            for k, v in values.items():
                setattr(job, k, v)
            job.updated_at = utcnow()
            rows = [jobs.add_log(s, job_id, level, msg) for level, msg in logs]
            data = jobs.job_dict(job)
            log_rows = [jobs.log_dict(r) for r in rows]
        for row in log_rows:
            self.services.bus.publish("job.log", row, project_id=data["project_id"])
        return data

    def _publish(self, data: dict) -> None:
        self.services.bus.publish("job.updated", data, project_id=data.get("project_id"))


__all__ = ["JobRun", "Worker", "retry_delay"]
