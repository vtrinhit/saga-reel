from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sagareel.app import create_app
from sagareel.config import AppSettings, load_settings
from sagareel.db.session import Database


@pytest.fixture
def settings(tmp_path) -> AppSettings:
    # env_file trỏ tới file tạm không tồn tại: test không bao giờ đọc .env.local thật.
    return load_settings(
        data_dir=tmp_path / "data",
        env_file=tmp_path / ".env.local",
        outputs_dir=tmp_path / "outputs",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def db(tmp_path) -> Iterator[Database]:
    """Database đã migrate trên tmp_path; dispose engine sau test (Windows giữ handle SQLite/WAL nếu không)."""
    database = Database(tmp_path / "x.db")
    database.migrate()
    try:
        yield database
    finally:
        database.dispose()


# --- Hàng đợi job (Task 7) ----------------------------------------------------------------------------------------


class JobProbe:
    """Ghi lại hành vi của handler test: số job đang chạy/đỉnh theo lane, thứ tự chạy, JobRun theo id."""

    def __init__(self) -> None:
        from collections import Counter

        self.active: Counter = Counter()
        self.peak: Counter = Counter()
        self.order: list[int] = []
        self.runs: dict = {}
        self.gates: dict = {}  # job_id → asyncio.Event do test mở để handler `test.gate` đi tiếp
        self.errors: dict = {}

    def gate(self, job_id: int):
        import asyncio

        return self.gates.setdefault(job_id, asyncio.Event())


TEST_JOB_TYPES = ("test.sleep", "test.flaky", "test.boom", "test.resumable")


@pytest.fixture
def test_job_handlers():
    """Đăng ký handler `test.*` cho hàng đợi job; gỡ sau test (registry production không chứa chúng)."""
    import asyncio

    from sagareel.services import jobs

    probe = JobProbe()

    async def _track(run, seconds: float) -> None:
        lane = run.lane
        probe.order.append(run.job_id)
        probe.runs[run.job_id] = run
        probe.active[lane] += 1
        probe.peak[lane] = max(probe.peak[lane], probe.active[lane])
        try:
            await asyncio.sleep(seconds)
        finally:
            probe.active[lane] -= 1

    @jobs.job_handler("test.sleep")
    async def sleep_handler(run):
        await _track(run, float(run.params.get("s", 0)))
        return {"slept": run.params.get("s", 0)}

    @jobs.job_handler("test.flaky")
    async def flaky_handler(run):
        await _track(run, 0)
        if run.attempt == 0:
            raise jobs.TransientError("Lỗi tạm thời (test)", retry_after=0.01)
        return {"attempt": run.attempt}

    @jobs.job_handler("test.boom")
    async def boom_handler(run):
        await _track(run, 0)
        raise ValueError("boom (test)")

    @jobs.job_handler("test.resumable", resumable=True)
    async def resumable_handler(run):
        await _track(run, float(run.params.get("s", 0)))
        return {"external_id": run.external_id, "external": dict(run.external)}

    try:
        yield probe
    finally:
        for name in TEST_JOB_TYPES:
            jobs.unregister_handler(name)


@pytest.fixture
def services(settings, test_job_handlers):
    """Services trên DB tạm đã migrate, có worker (chưa chạy; tick nhanh cho test)."""
    from sagareel.container import Services
    from sagareel.services.worker import Worker

    database = Database(settings.db_path)
    database.migrate()
    svc = Services(settings=settings, boot_id="w" * 32, db=database)
    svc.worker = Worker(svc, lane_limit=svc.lane_limit, tick=0.05)
    try:
        yield svc
    finally:
        database.dispose()
