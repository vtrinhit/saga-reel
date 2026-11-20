"""Luồng: trần số việc chạy cùng lúc theo loại provider trên toàn app (`services/concurrency`, worker, op `queue.*`)."""

from __future__ import annotations

import asyncio
from collections import Counter

import anyio
import pytest
from sagareel import ops as ops_pkg
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.db.models import GlobalSettings, Job, ProviderConnection
from sagareel.services import concurrency, jobs, settings
from sagareel.services.providers import ProviderService
from sqlalchemy import select

GATE = "test.cc_gate"


@pytest.fixture
def svc(services):
    """Services có ProviderService nối vào worker (như `create_app`) + handler `test.cc_gate` chờ test mở cổng."""
    services.providers = ProviderService(services)
    services.worker.lane_limit = services.providers.lane_limit
    state = {"active": Counter(), "peak": Counter(), "gates": {}, "order": []}

    def gate(job_id: int) -> asyncio.Event:
        return state["gates"].setdefault(job_id, asyncio.Event())

    @jobs.job_handler(GATE)
    async def gate_handler(run):
        kind = run.params.get("kind", "x")
        state["order"].append(run.job_id)
        state["active"][kind] += 1
        state["peak"][kind] = max(state["peak"][kind], state["active"][kind])
        try:
            await gate(run.job_id).wait()
        finally:
            state["active"][kind] -= 1
        return {}

    services.cc = state
    services.cc_gate = gate
    try:
        yield services
    finally:
        jobs.unregister_handler(GATE)


def owner(services) -> OpContext:
    return OpContext(services=services, actor="user:owner", role="owner")


def add_conn(services, name: str, kind: str, concurrency_: int = 5) -> int:
    with services.db.session() as s:
        row = ProviderConnection(kind=kind, adapter="maivideo", name=name, concurrency=concurrency_, enabled=True,
                                 health={}, options={})
        s.add(row)
        s.flush()
        return row.id


def put_global(services, **values) -> None:
    with services.db.session() as s:
        g = s.get(GlobalSettings, 1)
        if g is None:
            s.add(GlobalSettings(id=1, data=dict(values)))
        else:
            g.data = {**g.data, **values}


def enqueue(services, lane: str, kind: str = "x", **kw) -> int:
    return jobs.submit(services, GATE, {"kind": kind}, lane=lane, **kw)["id"]


def statuses(services, ids) -> list[str]:
    with services.db.session() as s:
        rows = {j.id: j.status for j in s.scalars(select(Job).where(Job.id.in_(ids)))}
    return [rows[i] for i in ids]


async def wait_until(pred, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not await anyio.to_thread.run_sync(pred):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Hết giờ chờ điều kiện")
        await asyncio.sleep(0.01)


async def settle(services, ticks: int = 4) -> None:
    """Chờ vài nhịp điều phối (tick test = 0.05 s)."""
    await asyncio.sleep(services.worker.tick * ticks)


async def release_all(services, ids) -> None:
    for j in ids:
        services.cc_gate(j).set()
    await wait_until(lambda: all(st == "succeeded" for st in statuses(services, ids)))


# --- Đọc trần -------------------------------------------------------------------------------------------------------


def test_caps_defaults_and_clamp(svc):
    with svc.db.session() as s:
        assert concurrency.caps(svc, s) == {"image": 2, "video": 2, "llm": 3, "tts": 2, "stt": 1, "render": 1}
    put_global(svc, **{"concurrency.video": 0, "concurrency.image": 500})
    with svc.db.session() as s:
        caps = concurrency.caps(svc, s)
    assert caps["video"] == 1 and caps["image"] == 20


def test_lane_kinds_maps_connections_and_internal_lanes(svc):
    v = add_conn(svc, "v1", "video")
    t = add_conn(svc, "t1", "tts")
    with svc.db.session() as s:
        got = concurrency.lane_kinds(s, {f"conn:{v}", f"conn:{t}", "ffmpeg", "story", "pipeline", "conn:999", "L"})
    assert got == {f"conn:{v}": "video", f"conn:{t}": "tts", "ffmpeg": "render", "story": "llm"}


# --- Worker ---------------------------------------------------------------------------------------------------------


async def test_kind_cap_is_hard_ceiling_across_connections(svc):
    put_global(svc, **{"concurrency.video": 1})
    a, b = add_conn(svc, "va", "video"), add_conn(svc, "vb", "video")
    ids = [enqueue(svc, f"conn:{c}", "video") for c in (a, b, a, b)]
    await svc.worker.start()
    try:
        await wait_until(lambda: statuses(svc, ids).count("running") == 1)
        await settle(svc)
        assert statuses(svc, ids).count("running") == 1
        for j in ids:  # mở dần từng cổng: không lúc nào 2 việc video chạy cùng lúc
            svc.cc_gate(j).set()
        await wait_until(lambda: all(st == "succeeded" for st in statuses(svc, ids)))
    finally:
        await svc.worker.stop()
    assert svc.cc["peak"]["video"] == 1


async def test_connection_cap_stays_a_sub_cap(svc):
    put_global(svc, **{"concurrency.image": 5})
    a = add_conn(svc, "ia", "image", concurrency_=1)
    ids = [enqueue(svc, f"conn:{a}", "image") for _ in range(3)]
    await svc.worker.start()
    try:
        await wait_until(lambda: "running" in statuses(svc, ids))
        await settle(svc)
        assert statuses(svc, ids).count("running") == 1
        await release_all(svc, ids)
    finally:
        await svc.worker.stop()


async def test_other_kinds_and_plain_lanes_are_not_limited(svc):
    put_global(svc, **{"concurrency.video": 1})
    v = add_conn(svc, "v", "video")
    i = add_conn(svc, "i", "image")
    vids = [enqueue(svc, f"conn:{v}", "video") for _ in range(2)]
    imgs = [enqueue(svc, f"conn:{i}", "image") for _ in range(2)]
    plain = [enqueue(svc, "L") for _ in range(2)]
    await svc.worker.start()
    try:
        await wait_until(lambda: statuses(svc, imgs + plain) == ["running"] * 4)
        assert statuses(svc, vids).count("running") == 1
        await release_all(svc, vids + imgs + plain)
    finally:
        await svc.worker.stop()


async def test_raising_cap_starts_queued_jobs_immediately(svc):
    put_global(svc, **{"concurrency.video": 1})
    a, b = add_conn(svc, "va", "video"), add_conn(svc, "vb", "video")
    ids = [enqueue(svc, f"conn:{c}", "video") for c in (a, b, a)]
    svc.worker.tick = 30.0  # không có nhịp định kỳ: chỉ `wake()` khi đổi trần mới làm việc chờ chạy
    await svc.worker.start()
    try:
        await wait_until(lambda: statuses(svc, ids).count("running") == 1)
        await anyio.to_thread.run_sync(lambda: settings.set_global(owner(svc), {"concurrency.video": 3}))
        await wait_until(lambda: statuses(svc, ids).count("running") == 3, timeout=2.0)
        await release_all(svc, ids)
    finally:
        await svc.worker.stop()


async def test_lowering_cap_never_cancels_running_jobs(svc):
    put_global(svc, **{"concurrency.video": 3})
    a = add_conn(svc, "va", "video")
    ids = [enqueue(svc, f"conn:{a}", "video") for _ in range(5)]
    await svc.worker.start()
    try:
        await wait_until(lambda: statuses(svc, ids).count("running") == 3)
        await anyio.to_thread.run_sync(lambda: settings.set_global(owner(svc), {"concurrency.video": 1}))
        await settle(svc)
        assert statuses(svc, ids)[:3] == ["running"] * 3  # việc đang chạy giữ nguyên
        svc.cc_gate(ids[0]).set()
        svc.cc_gate(ids[1]).set()
        await wait_until(lambda: statuses(svc, ids)[:2] == ["succeeded"] * 2)
        await settle(svc)
        assert statuses(svc, ids)[3:] == ["queued", "queued"]  # còn 1 đang chạy = trần mới → chưa khởi chạy thêm
        svc.cc_gate(ids[2]).set()
        await wait_until(lambda: statuses(svc, ids).count("running") == 1 and statuses(svc, ids)[2] == "succeeded")
        await settle(svc)
        assert statuses(svc, ids)[3:].count("running") == 1
        await release_all(svc, ids)
    finally:
        await svc.worker.stop()


async def test_fair_round_robin_across_connections_of_same_kind(svc):
    put_global(svc, **{"concurrency.video": 2})
    a, b = add_conn(svc, "va", "video"), add_conn(svc, "vb", "video")
    a_ids = [enqueue(svc, f"conn:{a}", "video") for _ in range(3)]  # a có job cũ hơn hết
    b_ids = [enqueue(svc, f"conn:{b}", "video") for _ in range(3)]
    await svc.worker.start()
    try:
        await wait_until(lambda: statuses(svc, a_ids + b_ids).count("running") == 2)
        assert statuses(svc, [a_ids[0], b_ids[0]]) == ["running", "running"]  # mỗi kết nối một, không dồn cho a
        await release_all(svc, a_ids + b_ids)
    finally:
        await svc.worker.stop()


async def test_priority_beats_round_robin_within_kind(svc):
    put_global(svc, **{"concurrency.video": 1})
    a, b = add_conn(svc, "va", "video"), add_conn(svc, "vb", "video")
    old = enqueue(svc, f"conn:{a}", "video")
    urgent = enqueue(svc, f"conn:{b}", "video", priority=5)
    await svc.worker.start()
    try:
        await wait_until(lambda: "running" in statuses(svc, [old, urgent]))
        assert statuses(svc, [old, urgent]) == ["queued", "running"]
        await release_all(svc, [old, urgent])
    finally:
        await svc.worker.stop()


async def test_render_cap_applies_to_ffmpeg_lane(svc):
    put_global(svc, **{"concurrency.render": 1, "jobs.ffmpeg_concurrency": 4})
    ids = [enqueue(svc, "ffmpeg", "render") for _ in range(3)]
    await svc.worker.start()
    try:
        await wait_until(lambda: "running" in statuses(svc, ids))
        await settle(svc)
        assert statuses(svc, ids).count("running") == 1
        await release_all(svc, ids)
    finally:
        await svc.worker.stop()


async def test_provider_update_concurrency_applies_immediately(svc):
    ops_pkg.load_all()
    put_global(svc, **{"concurrency.image": 10})
    a = add_conn(svc, "ia", "image", concurrency_=1)
    ids = [enqueue(svc, f"conn:{a}", "image") for _ in range(3)]
    svc.worker.tick = 30.0
    await svc.worker.start()
    try:
        await wait_until(lambda: statuses(svc, ids).count("running") == 1)
        await ops.execute(ops.REGISTRY["provider.update"],
                          ops.parse_params(ops.REGISTRY["provider.update"], {"id": a, "fields": {"concurrency": 3}}),
                          owner(svc))
        await wait_until(lambda: statuses(svc, ids).count("running") == 3, timeout=2.0)
        await release_all(svc, ids)
    finally:
        await svc.worker.stop()


# --- Op `queue.*` ---------------------------------------------------------------------------------------------------


async def call(services, name: str, params: dict, role: str = "owner"):
    ops_pkg.load_all()
    spec = ops.REGISTRY[name]
    ctx = OpContext(services=services, actor="user:owner" if role == "owner" else "user:viewer", role=role)
    return await ops.execute(spec, ops.parse_params(spec, params), ctx)


async def test_queue_concurrency_overview(svc):
    v = add_conn(svc, "va", "video", concurrency_=4)
    enqueue(svc, f"conn:{v}", "video")
    enqueue(svc, "story", "llm")
    out = await call(svc, "queue.concurrency", {})
    kinds = {k["kind"]: k for k in out["kinds"]}
    assert list(kinds) == ["image", "video", "llm", "tts", "stt", "render"]
    assert kinds["video"]["cap"] == 2 and kinds["video"]["queued"] == 1 and kinds["video"]["running"] == 0
    assert kinds["video"]["label"] == "Video"
    conn = next(c for c in kinds["video"]["connections"] if c["connection_id"] == v)
    assert conn == {"lane": f"conn:{v}", "connection_id": v, "name": "va", "enabled": True, "cap": 4,
                    "running": 0, "queued": 1}
    assert kinds["llm"]["queued"] == 1
    assert any(c["lane"] == "story" for c in kinds["llm"]["connections"])
    assert any(c["lane"] == "ffmpeg" for c in kinds["render"]["connections"])
    assert out["min"] == 1 and out["max"] == 20


async def test_queue_set_concurrency_writes_settings_publishes_and_undoes(svc):
    events = []
    orig = svc.bus.publish
    svc.bus.publish = lambda t, d, project_id=None: (events.append(t), orig(t, d, project_id))
    woken = []
    orig_wake = svc.worker.wake
    svc.worker.wake = lambda: (woken.append(1), orig_wake())
    out = await call(svc, "queue.set_concurrency", {"values": {"video": 4, "image": 3}})
    kinds = {k["kind"]: k["cap"] for k in out["kinds"]}
    assert kinds["video"] == 4 and kinds["image"] == 3
    assert "queue.updated" in events and woken
    with svc.db.session() as s:
        assert s.get(GlobalSettings, 1).data["concurrency.video"] == 4
    await call(svc, "history.undo", {})
    out = await call(svc, "queue.concurrency", {})
    assert {k["kind"]: k["cap"] for k in out["kinds"]}["video"] == 2


@pytest.mark.parametrize("values", [{"video": 0}, {"video": 21}, {"bogus": 2}, {}])
async def test_queue_set_concurrency_validates(svc, values):
    from sagareel.core.errors import AppError

    with pytest.raises(AppError) as e:
        await call(svc, "queue.set_concurrency", {"values": values})
    assert e.value.code == "validation_failed"


async def test_queue_set_concurrency_requires_owner(svc):
    from sagareel.core.errors import AppError

    with pytest.raises(AppError):
        await call(svc, "queue.set_concurrency", {"values": {"video": 2}}, role="viewer")
    out = await call(svc, "queue.concurrency", {}, role="viewer")
    assert len(out["kinds"]) == 6


def test_rest_routes(client):
    r = client.post("/api/ops/queue.concurrency", json={})
    assert r.status_code == 200, r.text
    assert [k["kind"] for k in r.json()["kinds"]] == ["image", "video", "llm", "tts", "stt", "render"]
    r = client.post("/api/ops/queue.set_concurrency", json={"values": {"render": 2}})
    assert r.status_code == 200, r.text
    assert {k["kind"]: k["cap"] for k in r.json()["kinds"]}["render"] == 2
    r = client.post("/api/ops/queue.set_concurrency", json={"values": {"render": 99}})
    assert r.status_code == 422 and r.json()["error_code"] == "validation_failed"
