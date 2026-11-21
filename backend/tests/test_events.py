import asyncio
import json
import threading

import anyio
import pytest
from pydantic import BaseModel

from sagareel.container import Services
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.events import Event, EventBus
from sagareel.db.models import SkillFile


async def take(sub, n):
    """Lấy n sự kiện từ subscription (timeout 1 s mỗi cái) rồi đóng nó."""
    try:
        for _ in range(n):
            yield await asyncio.wait_for(sub.__anext__(), 1)
    finally:
        await sub.aclose()


# --- EventBus -----------------------------------------------------------------------------------------------------


async def test_publish_from_thread_reaches_subscriber():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    agen = bus.subscribe(None, None)
    await asyncio.to_thread(bus.publish, "job.updated", {"id": 1})
    ev = await asyncio.wait_for(agen.__anext__(), 1)
    assert ev.type == "job.updated" and ev.seq == 1
    assert isinstance(ev, Event) and ev.data == {"id": 1} and ev.project_id is None and ev.at > 0
    await agen.aclose()


async def test_replay_after_last_seq_and_resync():
    bus = EventBus(buffer_size=3)
    bus.bind_loop(asyncio.get_running_loop())
    for i in range(5):
        bus.publish("x", {"i": i})
    evs = [e async for e in take(bus.subscribe(None, 1), 4)]
    assert evs[0].type == "resync" and [e.data["i"] for e in evs[1:]] == [2, 3, 4]
    # resync mang seq ngay trước sự kiện phát lại đầu tiên → id SSE tăng đơn điệu
    assert [e.seq for e in evs] == [2, 3, 4, 5]


async def test_replay_without_gap_has_no_resync():
    bus = EventBus(buffer_size=10)
    bus.bind_loop(asyncio.get_running_loop())
    for i in range(5):
        bus.publish("x", {"i": i})
    evs = [e async for e in take(bus.subscribe(None, 2), 3)]
    assert [(e.type, e.seq) for e in evs] == [("x", 3), ("x", 4), ("x", 5)]


async def test_last_seq_none_gets_only_new_events():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    bus.publish("old", {})
    sub = bus.subscribe(None, None)
    bus.publish("new", {})
    evs = [e async for e in take(sub, 1)]
    assert [e.type for e in evs] == ["new"]


async def test_stale_last_seq_from_previous_boot_resyncs():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    bus.publish("a", {})
    bus.publish("b", {})
    evs = [e async for e in take(bus.subscribe(None, 99), 3)]
    assert [e.type for e in evs] == ["resync", "a", "b"]
    assert [e.seq for e in evs] == [0, 1, 2]


async def test_project_filter():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    sub = bus.subscribe(1, None)
    bus.publish("p2", {}, project_id=2)
    bus.publish("p1", {}, project_id=1)
    bus.publish("global", {})
    evs = [e async for e in take(sub, 2)]
    assert [(e.type, e.project_id) for e in evs] == [("p1", 1), ("global", None)]
    # replay cũng lọc theo project; subscriber project=None nhận tất cả
    assert [e.type for e in [e async for e in take(bus.subscribe(2, 0), 2)]] == ["p2", "global"]
    assert [e.type for e in [e async for e in take(bus.subscribe(None, 0), 3)]] == ["p2", "p1", "global"]


async def test_thread_and_loop_publishes_keep_seq_order():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    sub = bus.subscribe(None, None)

    def worker():
        for i in range(50):
            bus.publish("t", {"i": i})

    th = threading.Thread(target=worker)
    th.start()
    for i in range(50):
        bus.publish("l", {"i": i})
        await asyncio.sleep(0)
    await asyncio.to_thread(th.join)
    evs = [e async for e in take(sub, 100)]
    assert [e.seq for e in evs] == list(range(1, 101))
    assert [e.data["i"] for e in evs if e.type == "t"] == list(range(50))


async def test_thread_publish_snapshots_data_before_scheduling():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    sub = bus.subscribe(None, None)

    def worker():
        progress = {"pct": 10}
        bus.publish("job.progress", progress)
        progress["pct"] = 99  # worker tái dùng dict ngay sau publish
        progress["extra"] = True

    # join đồng bộ chặn loop → callback của publish chắc chắn chạy SAU khi worker đã sửa dict
    th = threading.Thread(target=worker)
    th.start()
    th.join()
    evs = [e async for e in take(sub, 1)]
    assert evs[0].data == {"pct": 10}


async def test_apply_failure_is_logged_and_keeps_seq_contiguous(monkeypatch, caplog):
    import sagareel.core.events as events_mod

    loop = asyncio.get_running_loop()
    loop_errors = []
    loop.set_exception_handler(lambda lp, context: loop_errors.append(context))
    bus = EventBus()
    bus.bind_loop(loop)
    sub = bus.subscribe(None, None)
    real_event = events_mod.Event

    def flaky_event(seq, type, *rest):
        if type == "bad":
            raise RuntimeError("dựng sự kiện hỏng")
        return real_event(seq, type, *rest)

    monkeypatch.setattr(events_mod, "Event", flaky_event)
    await asyncio.to_thread(bus.publish, "bad", {"i": 0})  # hỏng bên trong callback trên loop
    await asyncio.to_thread(bus.publish, "good", {"i": 1})
    bus.publish("good2", {"i": 2})
    evs = [e async for e in take(sub, 2)]
    assert [(e.type, e.seq) for e in evs] == [("good", 1), ("good2", 2)]
    assert bus.last_seq == 2
    assert loop_errors == []
    assert any("bad" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")


def test_publish_with_non_mapping_data_is_logged_not_raised(caplog):
    bus = EventBus()
    bus.publish("x", None)  # type: ignore[arg-type]
    bus.publish("y", {})
    assert bus.last_seq == 1
    assert any(r.levelname == "ERROR" for r in caplog.records)


async def test_slow_subscriber_overflow_becomes_resync():
    bus = EventBus(buffer_size=3)
    bus.bind_loop(asyncio.get_running_loop())
    sub = bus.subscribe(None, None)
    for i in range(5):
        bus.publish("x", {"i": i})
    evs = [e async for e in take(sub, 1)]
    assert evs[0].type == "resync" and evs[0].data["reason"] == "overflow"


async def test_closed_subscription_is_unregistered():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    sub = bus.subscribe(None, None)
    await sub.aclose()
    bus.publish("x", {})
    with pytest.raises(StopAsyncIteration):
        await sub.__anext__()
    assert bus.subscriber_count == 0


def test_publish_without_bound_loop_is_buffered_and_does_not_raise():
    bus = EventBus()
    bus.publish("x", {"i": 1})
    loop = asyncio.new_event_loop()
    bus.bind_loop(loop)
    loop.close()
    bus.publish("y", {"i": 2})  # loop đã đóng: không được ném lỗi vào op

    async def replay():
        return [e async for e in take(bus.subscribe(None, 0), 2)]

    assert [e.type for e in asyncio.run(replay())] == ["x", "y"]


# --- SSE ----------------------------------------------------------------------------------------------------------


def test_sse_endpoint_sends_hello(client):
    with client.stream("GET", "/api/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        first = next(l for l in r.iter_lines() if l.startswith("event:"))
        assert first == "event: server.hello"
        assert client.app.state.services.bus.subscriber_count == 1
    # client ngắt → generator bị huỷ, subscription được huỷ đăng ký (không rò subscriber chết)
    assert client.app.state.services.bus.subscriber_count == 0


def _read_events(r, n):
    """Đọc n sự kiện SSE (bỏ qua ping/comment) → list dict {id?, event, data}."""
    out, cur = [], {}
    for line in r.iter_lines():
        if not line:
            if "event" in cur:
                out.append(cur)
                if len(out) == n:
                    return out
            cur = {}
        elif not line.startswith(":"):
            k, _, v = line.partition(":")
            cur[k] = v.removeprefix(" ")
    return out


def test_sse_replays_after_last_event_id_with_project_filter(client):
    services = client.app.state.services
    bus = services.bus
    client.portal.call(_publish_on_loop, bus, [("a", 1), ("b", 2), ("c", None), ("d", 1)])
    with client.stream("GET", "/api/events?project=1", headers={"Last-Event-ID": "1"}) as r:
        evs = _read_events(r, 3)
    assert evs[0]["event"] == "server.hello" and "id" not in evs[0]
    assert json.loads(evs[0]["data"]) == {"boot_id": services.boot_id}
    assert [(e["id"], e["event"]) for e in evs[1:]] == [("3", "c"), ("4", "d")]
    assert json.loads(evs[2]["data"]) == {"n": "d"}
    assert bus.subscriber_count == 0


def test_sse_replays_after_last_event_id_query_param(client):
    """EventSource mới tạo không gửi được header Last-Event-ID → client gửi `?last_event_id=` khi tự mở lại."""
    bus = client.app.state.services.bus
    client.portal.call(_publish_on_loop, bus, [("a", None), ("b", None), ("c", None)])
    with client.stream("GET", "/api/events?last_event_id=1") as r:
        evs = _read_events(r, 3)
    assert evs[0]["event"] == "server.hello"
    assert [(e["id"], e["event"]) for e in evs[1:]] == [("2", "b"), ("3", "c")]


def test_sse_last_event_id_header_wins_over_query(client):
    """Trình duyệt tự nối lại dùng lại URL cũ (query cũ) nhưng gửi header mới nhất → header thắng."""
    bus = client.app.state.services.bus
    client.portal.call(_publish_on_loop, bus, [("a", None), ("b", None), ("c", None)])
    with client.stream("GET", "/api/events?last_event_id=1", headers={"Last-Event-ID": "2"}) as r:
        evs = _read_events(r, 2)
    assert [(e.get("id"), e["event"]) for e in evs] == [(None, "server.hello"), ("3", "c")]


def test_sse_bad_last_event_id_query_is_ignored(client):
    bus = client.app.state.services.bus
    client.portal.call(_publish_on_loop, bus, [("a", None)])
    with client.stream("GET", "/api/events?last_event_id=abc") as r:
        first = _read_events(r, 1)
    assert first[0]["event"] == "server.hello"
    # header hỏng không che query hợp lệ
    with client.stream("GET", "/api/events?last_event_id=0", headers={"Last-Event-ID": "x"}) as r:
        evs = _read_events(r, 2)
    assert [(e.get("id"), e["event"]) for e in evs] == [(None, "server.hello"), ("1", "a")]


def test_sse_bad_last_event_id_is_ignored(client):
    with client.stream("GET", "/api/events", headers={"Last-Event-ID": "abc"}) as r:
        evs = _read_events(r, 1)
    assert evs[0]["event"] == "server.hello"


async def _publish_on_loop(bus, items):
    for name, pid in items:
        bus.publish(name, {"n": name}, project_id=pid)


# --- entity.changed từ ops ----------------------------------------------------------------------------------------


class _TplParams(BaseModel):
    body: str = "x"
    fail_after: bool = False
    noop: bool = False


_TEST_OPS = ("test.tpl_create", "test.tpl_create_async")


@pytest.fixture
def tpl_ops():
    @ops.op("test.tpl_create", summary="Tạo 2 template trong 2 UoW (chỉ dùng trong test)", params=_TplParams)
    def tpl_create(ctx: OpContext, p: _TplParams) -> dict:
        ids = []
        if p.noop:
            with ctx.uow("không đổi gì"):
                pass
            return {"ids": ids}
        for i in range(2):
            with ctx.uow("Tạo template") as s:
                t = SkillFile(path=f"tpl.{p.body}.{i}", body=p.body)
                s.add(t)
                s.flush()
                ids.append(t.id)
        if p.fail_after:
            raise RuntimeError("hỏng sau khi đã commit")
        return {"ids": ids}

    @ops.op("test.tpl_create_async", summary="Tạo template, bản async", params=_TplParams)
    async def tpl_create_async(ctx: OpContext, p: _TplParams) -> dict:
        def work():
            u = ctx.uow("Tạo template")
            with u as s:
                t = SkillFile(path=f"tpl.a.{p.body}", body=p.body)
                s.add(t)
                s.flush()
                return t.id

        return {"id": await anyio.to_thread.run_sync(work)}

    try:
        yield
    finally:
        for name in _TEST_OPS:
            ops.REGISTRY.pop(name, None)


@pytest.fixture
def ctx(settings, db):
    services = Services(settings=settings, boot_id="b" * 32, db=db)
    return OpContext(services=services, actor="user:owner", role="owner", project_id=7)


async def test_invoke_publishes_entity_changed_once_per_op(ctx, tpl_ops):
    bus = ctx.services.bus
    bus.bind_loop(asyncio.get_running_loop())
    sub = bus.subscribe(None, None)
    out = await ops.invoke("test.tpl_create", {"body": "m"}, ctx)
    evs = [e async for e in take(sub, 1)]
    ev = evs[0]
    assert ev.type == "entity.changed" and ev.project_id == 7
    assert ev.data["entities"] == [["skill_file", out["ids"][0]], ["skill_file", out["ids"][1]]]
    assert isinstance(ev.data["change_set"], list) and len(ev.data["change_set"]) == 2
    assert bus.last_seq == 1  # đúng một sự kiện cho cả op


async def test_execute_async_op_publishes_single_change_set(ctx, tpl_ops):
    bus = ctx.services.bus
    bus.bind_loop(asyncio.get_running_loop())
    sub = bus.subscribe(None, None)
    out = await ops.execute(ops.get_spec("test.tpl_create_async"), _TplParams(body="z"), ctx)
    ev = [e async for e in take(sub, 1)][0]
    assert ev.type == "entity.changed" and isinstance(ev.data["change_set"], int)
    assert ev.data["entities"] == [["skill_file", out["id"]]]


async def test_no_change_publishes_nothing(ctx, tpl_ops):
    bus = ctx.services.bus
    bus.bind_loop(asyncio.get_running_loop())
    await ops.invoke("test.tpl_create", {"noop": True}, ctx)
    assert bus.last_seq == 0


async def test_op_failing_after_commit_still_publishes(ctx, tpl_ops):
    bus = ctx.services.bus
    bus.bind_loop(asyncio.get_running_loop())
    with pytest.raises(RuntimeError):
        await ops.invoke("test.tpl_create", {"body": "f", "fail_after": True}, ctx)
    assert bus.last_seq == 1
    ev = [e async for e in take(bus.subscribe(None, 0), 1)][0]
    assert ev.type == "entity.changed" and len(ev.data["entities"]) == 2


async def test_history_op_publishes_with_uow_project(ctx, tpl_ops):
    bus = ctx.services.bus
    bus.bind_loop(asyncio.get_running_loop())
    out = await ops.invoke("test.tpl_create_async", {"body": "h"}, ctx)
    # undo với project_id tường minh khác ngữ cảnh → sự kiện mang project của UoW
    ctx0 = OpContext(services=ctx.services, actor="user:owner", role="owner", project_id=None)
    sub = bus.subscribe(None, None)
    await ops.invoke("history.undo", {"project_id": 7}, ctx0)
    ev = [e async for e in take(sub, 1)][0]
    assert ev.type == "entity.changed" and ev.project_id == 7
    assert ev.data["entities"] == [["skill_file", out["id"]]]


class _BrokenBus:
    def publish(self, *a, **kw):
        raise RuntimeError("bus hỏng")


async def test_entity_changed_failure_never_masks_op_result_or_error(ctx, tpl_ops, caplog):
    ctx.services.bus = _BrokenBus()
    # handler lỗi sau khi commit: lỗi gốc vẫn được ném ra, không bị lỗi phát sự kiện che mất
    with pytest.raises(RuntimeError, match="hỏng sau khi đã commit"):
        await ops.invoke("test.tpl_create", {"body": "g", "fail_after": True}, ctx)
    # handler thành công: kết quả vẫn trả về bình thường
    out = await ops.invoke("test.tpl_create", {"body": "k"}, ctx)
    assert len(out["ids"]) == 2
    assert sum("entity.changed" in r.getMessage() for r in caplog.records if r.levelname == "ERROR") == 2


def test_uow_outside_op_does_not_collect(ctx):
    # UoW chạy ngoài op: không có collector, không lỗi, không phát gì
    with ctx.uow(op_name="test.direct") as s:
        s.add(SkillFile(path="tpl.direct", body="d"))
    assert ctx.services.bus.last_seq == 0
