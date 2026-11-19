"""Định tuyến LLM theo công việc (Task 13): routes, chuỗi dự phòng, run_task, ops `llm.*` / `template.*`, job `llm.run`."""

from __future__ import annotations

import json

import anyio
import pytest
from PIL import Image
from sqlalchemy import select

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.core.ops import invoke
from sagareel.db.models import LlmTask, Media, ProviderConnection, Template
from sagareel.services import llm

HINT_WORDS = ("MCP", "sr_context_pack", "sr_prompts_submit")


# --- Helper -------------------------------------------------------------------------------------------------------


def seed_llm_defaults(session) -> None:
    """Chèn 14 llm_task + template mặc định (việc seed khi khởi động thuộc Task 15)."""
    for t in llm.default_tasks():
        session.add(LlmTask(key=t["key"], label=t["label"], description=t["description"],
                            template_key=t["template_key"], needs_vision=t["needs_vision"], json_mode=t["json_mode"],
                            route={"connection": None, "model": None, "params": {}}, fallbacks=[]))
        body = llm.builtin_template(t["template_key"])
        session.add(Template(key=t["template_key"], kind="llm", body=body, locked=False,
                             builtin_hash=llm.body_hash(body)))
    session.flush()


def add_llm(session, name: str, **cols) -> ProviderConnection:
    base = {"kind": "llm", "adapter": "fake_llm", "name": name, "options": {}, "concurrency": 2, "enabled": True}
    row = ProviderConnection(**{**base, **cols})
    session.add(row)
    session.flush()
    return row


def call(client, name: str, payload: dict | None = None):
    return client.post(f"/api/ops/{name}", json=payload or {})


def ok(client, name: str, payload: dict | None = None) -> dict:
    r = call(client, name, payload)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def app_services(client):
    services = client.app.state.services
    with services.db.session() as s:
        seed_llm_defaults(s)
    return services


@pytest.fixture
def run_jobs(client):
    async def wait(job_id: int, timeout_s: int = 60) -> dict:
        def blocking() -> dict:
            body = ok(client, "job.wait", {"ids": [job_id], "timeout_s": timeout_s})
            assert not body["timed_out"], body
            return body["items"][0]

        return await anyio.to_thread.run_sync(blocking)

    return wait


@pytest.fixture
def client_with_bus(client, app_services):
    """Client có app đã seed + danh sách sự kiện bus đã phát `(type, data)`."""
    events: list[tuple[str, dict]] = []
    bus = app_services.bus
    orig = bus.publish

    def publish(t, d, project_id=None):
        events.append((t, dict(d)))
        orig(t, d, project_id=project_id)

    bus.publish = publish
    client.events = events
    return client


def set_route(client, task: str, *, connection: str = "fake-llm", model: str | None = None, **extra) -> dict:
    return ok(client, "llm.task_update", {"key": task, "route": {"connection": connection, "model": model,
                                                                 "params": {}}, **extra})


async def run_llm(client, run_jobs, task: str, variables: dict | None = None, **extra) -> dict:
    job_id = ok(client, "llm.run", {"task": task, "variables": variables or {"input": "x"}, **extra})["job_id"]
    job = await run_jobs(job_id)
    assert job["status"] == "succeeded", job
    return job["result"]


FAKE_REPLIES = {"caption": '{"captions": ["a"]}', "write_script": "Kịch bản", "default": "{}"}


# --- routes() -----------------------------------------------------------------------------------------------------


def test_routes_order_override_task_route_then_fallbacks(services):
    with services.db.session() as s:
        seed_llm_defaults(s)
        add_llm(s, "a")
        add_llm(s, "b")
        add_llm(s, "c")
        task = s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one()
        task.route = {"connection": "b", "model": "mb", "params": {"temperature": 0.1}}
        task.fallbacks = [{"connection": "c", "model": None, "params": {}}, {"connection": "b", "model": "mb"}]
        s.flush()
        rs = llm.routes(s, "caption", None)
        assert [(r.connection, r.model) for r in rs] == [("b", "mb"), ("c", None)]  # trùng bị bỏ
        assert rs[0].params == {"temperature": 0.1}
        # override có kết nối thay route chính cho lần chạy; dự phòng giữ nguyên
        rs = llm.routes(s, "caption", {"connection": "a", "model": "ma"})
        assert [(r.connection, r.model) for r in rs] == [("a", "ma"), ("c", None), ("b", "mb")]
        # override chỉ có model → áp lên route chính
        rs = llm.routes(s, "caption", {"model": "m-run"})
        assert [(r.connection, r.model) for r in rs][0] == ("b", "m-run")


def test_routes_default_to_first_enabled_llm_connection(services):
    with services.db.session() as s:
        seed_llm_defaults(s)
        add_llm(s, "off", enabled=False)
        add_llm(s, "img", kind="image", adapter="fake_image")
        add_llm(s, "first")
        add_llm(s, "second")
        rs = llm.routes(s, "write_script", None)
        assert [(r.connection, r.model) for r in rs] == [("first", None)]


def test_routes_skip_non_vision_for_vision_tasks(services):
    with services.db.session() as s:
        seed_llm_defaults(s)
        add_llm(s, "text-only", options={"vision": False})
        add_llm(s, "seeing")
        task = s.scalars(select(LlmTask).where(LlmTask.key == "score_take")).one()
        task.route = {"connection": "text-only", "model": None, "params": {}}
        task.fallbacks = [{"connection": "seeing", "model": "v1", "params": {}}]
        s.flush()
        assert [r.connection for r in llm.routes(s, "score_take", None)] == ["seeing"]
        assert [r.connection for r in llm.routes(s, "caption", None)] == ["text-only"]  # không cần ảnh
        assert [r.connection for r in llm.routes(s, "drift_check", None)] == ["seeing"]  # mặc định: kết nối có vision
        task.fallbacks = []
        s.flush()
        # route của công việc không dùng được, không còn dự phòng → kết nối đang bật đầu tiên đọc được ảnh
        assert [(r.connection, r.model) for r in llm.routes(s, "score_take", None)] == [("seeing", None)]
        s.scalars(select(ProviderConnection).where(ProviderConnection.name == "seeing")).one().enabled = False
        s.flush()
        with pytest.raises(AppError) as e:
            llm.routes(s, "score_take", None)
        assert e.value.code == "llm_not_configured" and "text-only" in e.value.detail
        # override chỉ định kết nối thì không tự đổi sang kết nối khác
        with pytest.raises(AppError) as e:
            llm.routes(s, "write_script", {"connection": "seeing"})
        assert e.value.code == "llm_not_configured"


def test_routes_without_any_llm_raise_not_configured(services):
    with services.db.session() as s:
        seed_llm_defaults(s)
        with pytest.raises(AppError) as e:
            llm.routes(s, "write_script", None)
    assert e.value.code == "llm_not_configured" and e.value.status == 409
    assert all(w in e.value.hint for w in HINT_WORDS)
    with services.db.session() as s, pytest.raises(AppError) as e:
        llm.routes(s, "khong_co", None)
    assert e.value.code == "llm_task_not_found"


# --- run_task ----------------------------------------------------------------------------------------------------


async def test_fallback_chain_used_when_primary_fails(services):
    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "primary", options={"fail": True})
            add_llm(s, "backup", options={"replies": {"caption": '{"captions": ["ok"]}'}})
            task = s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one()
            task.route = {"connection": "primary", "model": "p1", "params": {}}
            task.fallbacks = [{"connection": "backup", "model": "b1", "params": {}}]

    await anyio.to_thread.run_sync(setup)
    out = await llm.run_task(services, "caption", {"input": "ảnh mèo"})
    assert out.data == {"captions": ["ok"]}
    assert (out.route.connection, out.route.model) == ("backup", "b1")
    assert len(out.attempts) == 2
    assert out.attempts[0]["ok"] is False and out.attempts[0]["error_code"] == "provider_unavailable"
    assert out.attempts[1]["ok"] is True and out.attempts[1]["connection"] == "backup"


async def test_transient_failure_also_falls_back_and_all_failed_raises(services):
    from sagareel.services.jobs import TransientError

    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "flaky", options={"fail": "transient"})
            add_llm(s, "dead", options={"fail": True})
            task = s.scalars(select(LlmTask).where(LlmTask.key == "write_script")).one()
            task.route = {"connection": "flaky", "model": None, "params": {}}
            task.fallbacks = [{"connection": "dead", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    with pytest.raises(TransientError):  # còn lỗi tạm thời → job sẽ thử lại cả chuỗi sau
        await llm.run_task(services, "write_script", {"input": "x"})


async def test_invalid_json_moves_to_next_route_and_fences_are_stripped(services):
    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "chatty", options={"replies": {"outline": "Sure! here it is"}})
            add_llm(s, "fenced", options={"replies": {"outline": '```json\n{"acts": [1, 2]}\n```'}})
            task = s.scalars(select(LlmTask).where(LlmTask.key == "outline")).one()
            task.route = {"connection": "chatty", "model": None, "params": {}}
            task.fallbacks = [{"connection": "fenced", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    out = await llm.run_task(services, "outline", {"input": "x"})
    assert out.data == {"acts": [1, 2]} and out.route.connection == "fenced"
    assert out.attempts[0]["error_code"] == "llm_invalid_json"


async def test_run_task_reads_config_every_call(services):
    def setup(model: str):
        with services.db.session() as s:
            if s.scalars(select(LlmTask)).first() is None:
                seed_llm_defaults(s)
                add_llm(s, "x")
            task = s.scalars(select(LlmTask).where(LlmTask.key == "write_script")).one()
            task.route = {"connection": "x", "model": model, "params": {}}

    await anyio.to_thread.run_sync(setup, "m1")
    first = await llm.run_task(services, "write_script", {"input": "x"})
    await anyio.to_thread.run_sync(setup, "m2")
    second = await llm.run_task(services, "write_script", {"input": "x"})
    assert (first.route.model, second.route.model) == ("m1", "m2")


async def test_run_task_sends_rendered_template_and_variables_verbatim(services, monkeypatch):
    from sagareel.providers import registry

    seen = []
    cls = registry.get("fake_llm")
    real = cls.chat

    async def spy(self, req, on_delta=None):
        seen.append(req)
        return await real(self, req, on_delta)

    monkeypatch.setattr(cls, "chat", spy)

    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "x")

    await anyio.to_thread.run_sync(setup)
    text = "  Dòng một\r\n  dòng hai  "
    await llm.run_task(services, "write_script", {"input": text})
    req = seen[0]
    assert req.messages[-1] == {"role": "user", "content": text}
    assert req.messages[0]["role"] == "system" and req.params["template_key"] == "write_script"
    assert req.json_mode is False


# --- Ops -------------------------------------------------------------------------------------------------------


def test_no_llm_configured_error_suggests_alternatives(client, app_services):
    r = call(client, "llm.run", {"task": "write_script", "variables": {"input": "x"}})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error_code"] == "llm_not_configured"
    assert all(w in body["hint"] for w in HINT_WORDS)
    assert ok(client, "job.list")["items"] == []  # không tạo job


def test_missing_variable_rejected_before_enqueue(client, app_services):
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm"})
    r = call(client, "llm.run", {"task": "write_script", "variables": {}})
    assert r.status_code == 422 and r.json()["error_code"] == "template_variable_missing"
    assert r.json()["extra"]["name"] == "input"


async def test_route_change_takes_effect_immediately(client, app_services, run_jobs):
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                   "options": {"replies": FAKE_REPLIES}})
    set_route(client, "caption", model="m1")
    r1 = await run_llm(client, run_jobs, "caption")
    set_route(client, "caption", model="m2")
    r2 = await run_llm(client, run_jobs, "caption")
    assert (r1["route"]["model"], r2["route"]["model"]) == ("m1", "m2")
    assert r2["data"] == {"captions": ["a"]} and r2["text"] == FAKE_REPLIES["caption"]
    assert set(r2) >= {"text", "data", "route", "usage"}


async def test_llm_run_emits_deltas(client_with_bus, run_jobs):
    client = client_with_bus
    reply = " ".join(f"từ{i}" for i in range(200))
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                   "options": {"replies": {"write_script": reply}}})
    result = await run_llm(client, run_jobs, "write_script")
    assert result["text"] == reply and result["data"] is None
    deltas = [d for t, d in client.events if t == "llm.delta"]
    assert deltas and all(set(d) >= {"job_id", "text"} for d in deltas)
    assert "".join(d["text"] for d in deltas) == reply
    assert len(deltas) < 200  # gộp (≤ 10 sự kiện/giây), không phải mỗi từ một sự kiện


async def test_llm_run_with_image_media_and_override(client, app_services, run_jobs, monkeypatch):
    from sagareel.providers import registry

    seen = []
    cls = registry.get("fake_llm")
    real = cls.chat

    async def spy(self, req, on_delta=None):
        seen.append((self.conn.name, req))
        return await real(self, req, on_delta)

    monkeypatch.setattr(cls, "chat", spy)
    for name in ("fake-llm", "other"):
        ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": name,
                                       "options": {"replies": FAKE_REPLIES}})
    path = app_services.settings.data_dir / "media" / "a.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 32)).save(path)
    with app_services.db.session() as s:
        m = Media(kind="image", path="media/a.png", mime="image/png", size=path.stat().st_size, sha256="2" * 64,
                  origin="upload", meta={}, width=64, height=32)
        s.add(m)
        s.flush()
        mid = m.id
    result = await run_llm(client, run_jobs, "score_take", images_media_ids=[mid],
                           override={"connection": "other", "model": "vx"})
    assert result["route"] == {"connection": "other", "model": "vx", "params": {}}
    name, req = seen[0]
    assert name == "other" and req.images == [path.resolve()] and req.model == "vx"
    r = call(client, "llm.run", {"task": "score_take", "variables": {"input": "x"}, "images_media_ids": [999]})
    assert r.status_code == 404


def test_task_list_and_update_validates(client, app_services):
    items = {t["key"]: t for t in ok(client, "llm.task_list")["items"]}
    assert len(items) == 14 and items["score_take"]["needs_vision"] and items["caption"]["json_mode"]
    assert items["caption"]["effective_routes"] == []
    r = call(client, "llm.task_update", {"key": "caption", "route": {"connection": "khong-co"}})
    assert r.status_code == 404 and r.json()["error_code"] == "connection_not_found"
    ok(client, "provider.create", {"kind": "image", "adapter": "fake_image", "name": "img"})
    r = call(client, "llm.task_update", {"key": "caption", "fallbacks": [{"connection": "img"}]})
    assert r.status_code == 422 and r.json()["error_code"] == "capability_mismatch"
    r = call(client, "llm.task_update", {"key": "caption", "template_key": "khong_co"})
    assert r.status_code == 404 and r.json()["error_code"] == "template_not_found"
    r = call(client, "llm.task_update", {"key": "khong_co", "label": "x"})
    assert r.status_code == 404 and r.json()["error_code"] == "llm_task_not_found"
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm"})
    items = {t["key"]: t for t in ok(client, "llm.task_list")["items"]}
    assert items["caption"]["effective_routes"] == [{"connection": "fake-llm", "model": None, "params": {}}]


def test_task_update_is_undoable(client, app_services):
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm"})
    set_route(client, "caption", model="m1", label="Chú thích mới")
    with app_services.db.session() as s:
        task = s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one()
        assert task.route["model"] == "m1" and task.label == "Chú thích mới"
    ok(client, "history.undo")
    with app_services.db.session() as s:
        task = s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one()
        assert task.route["model"] is None and task.label != "Chú thích mới"


def test_task_create(client, app_services):
    created = ok(client, "llm.task_create", {"key": "summarize_comments", "label": "Tóm tắt bình luận",
                                             "template_key": "memory_summarize", "json_mode": True})
    assert created["key"] == "summarize_comments" and created["json_mode"] is True
    r = call(client, "llm.task_create", {"key": "summarize_comments", "label": "x", "template_key": "caption"})
    assert r.status_code == 409
    r = call(client, "llm.task_create", {"key": "Bad Key", "label": "x", "template_key": "caption"})
    assert r.status_code == 422


def test_template_list_get_update_reset(client, app_services):
    items = {t["key"]: t for t in ok(client, "template.list")["items"]}
    assert len(items) == 14 and items["caption"]["modified"] is False and items["caption"]["has_builtin"]
    got = ok(client, "template.get", {"key": "caption"})
    assert "{{ input }}" in got["body"] and got["variables"] == ["input"]
    new_body = "<!-- role: user -->\nViết chú thích cho {{ input }} theo giọng {{ tone }}"
    upd = ok(client, "template.update", {"key": "caption", "body": new_body})
    assert upd["body"] == new_body and upd["modified"] is True and upd["variables"] == ["input", "tone"]
    r = call(client, "template.update", {"key": "caption", "body": "{% if %}"})
    assert r.status_code == 422 and r.json()["error_code"] == "template_syntax_error"
    reset = ok(client, "template.reset", {"key": "caption"})
    assert reset["body"] == llm.builtin_template("caption") and reset["modified"] is False
    r = call(client, "template.get", {"key": "khong_co"})
    assert r.status_code == 404 and r.json()["error_code"] == "template_not_found"
    r = call(client, "template.update", {"key": "moi", "body": "{{ input }}"})
    assert r.status_code == 404
    created = ok(client, "template.update", {"key": "moi", "body": "{{ input }}", "create_if_missing": True})
    assert created["key"] == "moi" and created["has_builtin"] is False
    r = call(client, "template.reset", {"key": "moi"})
    assert r.status_code == 404 and r.json()["error_code"] == "template_no_builtin"


async def test_locked_template_cannot_be_changed_by_llm(app_services):
    with app_services.db.session() as s:
        s.scalars(select(Template).where(Template.key == "caption")).one().locked = True
    ctx = OpContext(services=app_services, actor="llm:gpt-x", role="owner")
    with pytest.raises(AppError) as e:
        await invoke("template.update", {"key": "caption", "body": "{{ input }}"}, ctx)
    assert e.value.code == "template_locked"
    owner = OpContext(services=app_services, actor="user:owner", role="owner")
    assert (await invoke("template.update", {"key": "caption", "body": "{{ input }}"}, owner))["body"] == "{{ input }}"


def test_llm_models_op(client, app_services):
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                   "options": {"models": ["a", "b"]}})
    assert ok(client, "llm.models", {"connection": "fake-llm"})["models"] == ["a", "b"]
    r = call(client, "llm.models", {"connection": "khong-co"})
    assert r.status_code == 404


def test_catalog_flags(client):
    cat = {o["name"]: o for o in client.get("/api/ops").json()}
    assert cat["llm.task_list"]["read_only"] and cat["llm.task_list"]["viewer"] and cat["llm.task_list"]["mcp"] == "tool"
    assert cat["llm.run"]["long_running"] and cat["llm.run"]["mcp"] == "tool"
    assert cat["llm.models"]["read_only"]
    for name in ("llm.task_update", "llm.task_create", "template.list", "template.get", "template.update",
                 "template.reset"):
        assert name in cat
    assert json.dumps(cat["llm.run"]["params_schema"])


async def test_delta_throttle_limits_rate_and_keeps_text():
    import asyncio
    import time

    emitted: list[tuple[float, str]] = []
    throttle = llm.DeltaThrottle(lambda t: emitted.append((time.monotonic(), t)))
    words = [f"w{i} " for i in range(50)]
    for w in words:
        throttle.push(w)
        await asyncio.sleep(0.01)
    throttle.flush()
    assert "".join(t for _, t in emitted) == "".join(words)
    gaps = [b[0] - a[0] for a, b in zip(emitted, emitted[1:-1])]  # lần flush cuối được phép sớm
    assert all(g >= 0.09 for g in gaps), gaps
    assert len(emitted) < 15


async def test_llm_run_logs_llm_call_and_resets_deltas_on_fallback(client_with_bus, run_jobs):
    client = client_with_bus
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "broken",
                                   "options": {"fail": "unavailable"}})
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                   "options": {"replies": FAKE_REPLIES}})
    ok(client, "llm.task_update", {"key": "write_script", "route": {"connection": "broken"},
                                   "fallbacks": [{"connection": "fake-llm", "model": "fb"}]})
    job_id = ok(client, "llm.run", {"task": "write_script", "variables": {"input": "x"}})["job_id"]
    job = await run_jobs(job_id)
    assert job["status"] == "succeeded", job
    assert job["result"]["route"]["connection"] == "fake-llm" and len(job["result"]["attempts"]) == 2
    logs = [row["message"] for row in ok(client, "job.logs", {"id": job_id})["items"]]
    calls = [m for m in logs if m.startswith("llm_call")]
    assert len(calls) == 2 and "connection=broken" in calls[0] and "connection=fake-llm" in calls[1]
    resets = [d for t, d in client.events if t == "llm.delta" and d.get("reset")]
    assert len(resets) == 1
