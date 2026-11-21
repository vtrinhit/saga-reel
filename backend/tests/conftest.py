from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sagareel.app import create_app
from sagareel.config import AppSettings, load_settings
from sagareel.db.session import Database


@pytest.fixture
def settings(tmp_path) -> AppSettings:
    # env_file trỏ tới file tạm không tồn tại: test không bao giờ đọc .env.local thật.
    # frontend_dist trỏ tới thư mục tạm (chưa build): `frontend/dist` thật (sau `npm run build`) không đổi hành vi test.
    s = load_settings(
        data_dir=tmp_path / "data",
        env_file=tmp_path / ".env.local",
        outputs_dir=tmp_path / "outputs",
    )
    # seed_samples=False: dự án mẫu không tự xuất hiện trong mọi test (test riêng gọi samples.seed).
    return dataclasses.replace(s, frontend_dist=tmp_path / "frontend_dist", seed_samples=False)


@pytest.fixture
def client(settings):
    # Client CỤC BỘ (Host 127.0.0.1:8990, IP loopback): chủ sở hữu không cần đăng nhập. Host mặc định `testserver`
    # của TestClient bị middleware bảo mật chặn (`untrusted_host`).
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8990", client=("127.0.0.1", 50000)) as c:
        yield c


LOCAL_BASE_URL = "http://127.0.0.1:8990"
LOCAL_PEER = ("127.0.0.1", 50000)


@pytest.fixture
def make_local_client():
    """Tạo TestClient CỤC BỘ cho một app tự dựng (Host 127.0.0.1:8990, IP loopback → chủ không cần đăng nhập khi
    chưa đặt mật khẩu). TestClient mặc định (`testserver`, IP `testclient`) bị middleware bảo mật chặn."""

    def make(app, **kwargs) -> TestClient:
        return TestClient(app, base_url=LOCAL_BASE_URL, client=LOCAL_PEER, **kwargs)

    return make


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
    from sagareel import handlers
    from sagareel.container import Services
    from sagareel.services.worker import Worker

    handlers.load_all()
    database = Database(settings.db_path)
    database.migrate()
    svc = Services(settings=settings, boot_id="w" * 32, db=database)
    svc.worker = Worker(svc, lane_limit=svc.lane_limit, tick=0.05)
    try:
        yield svc
    finally:
        database.dispose()


# --- M1+ (Task W0): ngữ cảnh chủ sở hữu, project "demo", clip video tí hon -----------------------------------------


@pytest.fixture
def owner_ctx(request, settings):
    """`OpContext` chủ sở hữu trên cùng DB với test: fixture `services` nếu test dùng nó, rồi `db`, không thì DB tại
    `settings.db_path` (chung với app của fixture `client`)."""
    from sagareel.container import Services
    from sagareel.core.context import OpContext

    owned: Database | None = None
    if "services" in request.fixturenames:
        svc = request.getfixturevalue("services")
    else:
        if "db" in request.fixturenames:
            database = request.getfixturevalue("db")
        else:
            database = owned = Database(settings.db_path)
            database.migrate()
        svc = Services(settings=settings, boot_id="o" * 32, db=database)
    try:
        yield OpContext(services=svc, actor="user:owner", role="owner")
    finally:
        if owned is not None:
            owned.dispose()


@pytest.fixture
def project(owner_ctx) -> int:
    """Id của project "demo" (tên "Demo") vừa tạo qua `services.projects.create`, kèm bộ prompt của dữ liệu mẫu
    "Phim truyện người thật" (prompt là dữ liệu của project — project trống không có prompt nào)."""
    from sagareel.services import projects, samples

    pid = projects.create(owner_ctx, name="Demo", key="demo")["id"]
    with owner_ctx.services.db.session() as s:
        samples.install_prompts(s, pid, samples.DEFAULT_SAMPLE)
    return pid


@pytest.fixture(scope="session")
def _clip_tiny_source(tmp_path_factory):
    """Video gốc 64×112, 1 s, 30 fps, có tiếng — sinh một lần cho cả phiên test."""
    import subprocess

    out = tmp_path_factory.mktemp("clip_tiny") / "tiny.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=64x112:d=1:r=30", "-f", "lavfi", "-i", "sine=d=1",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True,
    )
    return out


@pytest.fixture
def make_clip_tiny(settings, _clip_tiny_source, tmp_path):
    """Factory: `make_clip_tiny(database, project_id=None) -> media_id` — đăng ký một media video 64×112 1 s.

    Mỗi lần gọi chép file gốc (kho media chuyển file vào `data/media`) và đổi vài byte metadata để sha256 khác nhau
    → mỗi lần một bản ghi media mới.
    """
    import shutil

    from sagareel.services import media

    counter = iter(range(1_000_000))

    def make(database: Database, project_id: int | None = None) -> int:
        n = next(counter)
        dst = tmp_path / f"clip_tiny_{n}.mp4"
        shutil.copy(_clip_tiny_source, dst)
        with open(dst, "ab") as f:  # byte thừa sau moov: ffprobe bỏ qua, sha256 đổi
            f.write(f"sagareel-tiny-{n}".encode())
        with database.session() as s:
            m = media.register(s, dst, kind="video", origin="fake", settings=settings, project_id=project_id)
            return m.id

    return make
