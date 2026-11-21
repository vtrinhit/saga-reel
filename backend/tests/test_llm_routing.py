"""Định tuyến LLM theo công việc (Task 13): routes, chuỗi dự phòng, run_task, ops `llm.*`, job `llm.run`.

Prompt là dữ liệu của project: test dùng một dự án có prompt chung `{{ input }}` cho mọi khoá."""

from __future__ import annotations

import json

import anyio
import pytest
from PIL import Image
from sqlalchemy import select

from sagareel.core.errors import AppError
from sagareel.db.models import LlmTask, Media, Project, ProviderConnection
from sagareel.services import llm

HINT_WORDS = ("MCP", "sr_context_pack", "sr_prompts_submit")


# --- Helper -------------------------------------------------------------------------------------------------------


GENERIC = "<!-- role: system -->\nBạn là trợ lý viết phim.\n<!-- role: user -->\n{{ input }}"
PID: list[int] = []


def add_prompt_project(session) -> int:
    """Dự án có prompt `GENERIC` cho mọi công việc LLM mặc định; trả id."""
    from sagareel.services import prompts

    p = Project(key=f"llm{len(PID)}", name="LLM")
    session.add(p)
    session.flush()
    for t in llm.default_tasks():
        prompts.put(session, p.id, t["template_key"], GENERIC)
    return p.id


def seed_llm_defaults(session) -> int:
    """Chèn llm_task mặc định (việc seed khi khởi động thuộc Task 15) + dự án có prompt; trả id dự án."""
    for t in llm.default_tasks():
        session.add(LlmTask(key=t["key"], label=t["label"], description=t["description"],
                            template_key=t["template_key"], needs_vision=t["needs_vision"], json_mode=t["json_mode"],
                            route={"connection": None, "model": None, "params": {}}, fallbacks=[]))
    session.flush()
    pid = add_prompt_project(session)
    PID[:] = [pid]
    return pid


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
    # Lifespan của app đã seed công việc LLM mặc định (services/seed.py, Task 15); thêm dự án có prompt.
    services = client.app.state.services
    with services.db.session() as s:
        assert s.scalars(select(LlmTask)).first() is not None
        PID[:] = [add_prompt_project(s)]
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
    job_id = ok(client, "llm.run", {"task": task, "variables": variables or {"input": "x"}, "project_id": PID[0],
                                    **extra})["job_id"]
    job = await run_jobs(job_id)
    assert job["status"] == "succeeded", job
    return job["result"]


FAKE_REPLIES = {"caption": '{"captions": ["a"]}', "story_write_script": "Kịch bản", "default": "{}"}


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
        rs = llm.routes(s, "story_write_script", None)
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
            llm.routes(s, "story_write_script", {"connection": "seeing"})
        assert e.value.code == "llm_not_configured"


def test_routes_without_any_llm_raise_not_configured(services):
    with services.db.session() as s:
        seed_llm_defaults(s)
        with pytest.raises(AppError) as e:
            llm.routes(s, "story_write_script", None)
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
    out = await llm.run_task(services, "caption", {"input": "ảnh mèo"}, project_id=PID[0])
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
            add_llm(s, "flaky", options={"fail_transient": True})
            add_llm(s, "dead", options={"fail": True})
            task = s.scalars(select(LlmTask).where(LlmTask.key == "story_write_script")).one()
            task.route = {"connection": "flaky", "model": None, "params": {}}
            task.fallbacks = [{"connection": "dead", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    with pytest.raises(TransientError):  # còn lỗi tạm thời → job sẽ thử lại cả chuỗi sau
        await llm.run_task(services, "story_write_script", {"input": "x"}, project_id=PID[0])


async def test_invalid_json_moves_to_next_route_and_fences_are_stripped(services):
    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "chatty", options={"replies": {"story_outline": "Sure! here it is"}})
            add_llm(s, "fenced", options={"replies": {"story_outline": '```json\n{"acts": [1, 2]}\n```'}})
            task = s.scalars(select(LlmTask).where(LlmTask.key == "story_outline")).one()
            task.route = {"connection": "chatty", "model": None, "params": {}}
            task.fallbacks = [{"connection": "fenced", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    out = await llm.run_task(services, "story_outline", {"input": "x"}, project_id=PID[0])
    assert out.data == {"acts": [1, 2]} and out.route.connection == "fenced"
    assert out.attempts[0]["error_code"] == "llm_invalid_json"


async def test_run_task_reads_config_every_call(services):
    def setup(model: str):
        with services.db.session() as s:
            if s.scalars(select(LlmTask)).first() is None:
                seed_llm_defaults(s)
                add_llm(s, "x")
            task = s.scalars(select(LlmTask).where(LlmTask.key == "story_write_script")).one()
            task.route = {"connection": "x", "model": model, "params": {}}

    await anyio.to_thread.run_sync(setup, "m1")
    first = await llm.run_task(services, "story_write_script", {"input": "x"}, project_id=PID[0])
    await anyio.to_thread.run_sync(setup, "m2")
    second = await llm.run_task(services, "story_write_script", {"input": "x"}, project_id=PID[0])
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
    await llm.run_task(services, "story_write_script", {"input": text}, project_id=PID[0])
    req = seen[0]
    assert req.messages[-1] == {"role": "user", "content": text}
    assert req.messages[0]["role"] == "system" and req.params["template_key"] == "story_write_script"
    assert req.json_mode is False


# --- Ops -------------------------------------------------------------------------------------------------------


def test_no_llm_configured_error_suggests_alternatives(client, app_services):
    r = call(client, "llm.run", {"task": "story_write_script", "variables": {"input": "x"}})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error_code"] == "llm_not_configured"
    assert all(w in body["hint"] for w in HINT_WORDS)
    assert ok(client, "job.list")["items"] == []  # không tạo job


def test_missing_variable_rejected_before_enqueue(client, app_services):
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm"})
    r = call(client, "llm.run", {"task": "story_write_script", "variables": {}, "project_id": PID[0]})
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
                                   "options": {"replies": {"story_write_script": reply}}})
    result = await run_llm(client, run_jobs, "story_write_script")
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
    r = call(client, "llm.run", {"task": "score_take", "variables": {"input": "x"}, "images_media_ids": [999],
                                 "project_id": PID[0]})
    assert r.status_code == 404


def test_task_list_and_update_validates(client, app_services):
    items = {t["key"]: t for t in ok(client, "llm.task_list")["items"]}
    assert len(items) == len(llm.default_tasks()) and items["score_take"]["needs_vision"] and items["caption"]["json_mode"]
    assert items["caption"]["effective_routes"] == []
    r = call(client, "llm.task_update", {"key": "caption", "route": {"connection": "khong-co"}})
    assert r.status_code == 404 and r.json()["error_code"] == "connection_not_found"
    ok(client, "provider.create", {"kind": "image", "adapter": "fake_image", "name": "img"})
    r = call(client, "llm.task_update", {"key": "caption", "fallbacks": [{"connection": "img"}]})
    assert r.status_code == 422 and r.json()["error_code"] == "capability_mismatch"
    r = call(client, "llm.task_update", {"key": "caption", "template_key": "khong_co"})
    assert r.status_code == 422 and r.json()["error_code"] == "prompt_key_unknown"
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
                                             "template_key": "story_memory_summarize", "json_mode": True})
    assert created["key"] == "summarize_comments" and created["json_mode"] is True
    r = call(client, "llm.task_create", {"key": "summarize_comments", "label": "x", "template_key": "caption"})
    assert r.status_code == 409
    r = call(client, "llm.task_create", {"key": "Bad Key", "label": "x", "template_key": "caption"})
    assert r.status_code == 422


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
    for name in ("llm.task_update", "llm.task_create"):
        assert name in cat
    assert not [n for n in cat if n.startswith("template.")]  # không còn template chung của tool
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
                                   "options": {"fail": True}})
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                   "options": {"replies": FAKE_REPLIES}})
    ok(client, "llm.task_update", {"key": "story_write_script", "route": {"connection": "broken"},
                                   "fallbacks": [{"connection": "fake-llm", "model": "fb"}]})
    job_id = ok(client, "llm.run", {"task": "story_write_script", "variables": {"input": "x"},
                                    "project_id": PID[0]})["job_id"]
    job = await run_jobs(job_id)
    assert job["status"] == "succeeded", job
    assert job["result"]["route"]["connection"] == "fake-llm" and len(job["result"]["attempts"]) == 2
    logs = [row["message"] for row in ok(client, "job.logs", {"id": job_id})["items"]]
    calls = [m for m in logs if m.startswith("llm_call")]
    assert len(calls) == 2 and "connection=broken" in calls[0] and "connection=fake-llm" in calls[1]
    resets = [d for t, d in client.events if t == "llm.delta" and d.get("reset")]
    assert len(resets) == 1


LLM_KEY = "sk-LLM-SECRET-7777"


@pytest.mark.parametrize("mode", ["crash", "rejected"])
async def test_llm_run_never_leaks_api_key_into_job_or_logs(client, app_services, run_jobs, monkeypatch, caplog, mode):
    """Lỗi adapter lọt khoá API (lỗi lạ có traceback, hoặc lỗi chuẩn có thông báo kèm khoá): job_log, lỗi job, kết quả
    và log ứng dụng của job `llm.run` không bao giờ chứa khoá (như job provider `provider_job`)."""
    from sagareel.providers import registry
    from sagareel.services.jobs import JobFailed

    async def leaky_chat(self, req, on_delta=None):
        if mode == "crash":
            raise RuntimeError(f"boom: Authorization: Bearer {self.conn.api_key}")
        raise JobFailed("provider_rejected", f"Provider từ chối khoá {self.conn.api_key}.")

    monkeypatch.setattr(registry.get("fake_llm"), "chat", leaky_chat)
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "leaky", "api_key": LLM_KEY})
    set_route(client, "story_write_script", connection="leaky")
    caplog.set_level("DEBUG")
    job_id = ok(client, "llm.run", {"task": "story_write_script", "variables": {"input": "x"},
                                    "project_id": PID[0]})["job_id"]
    job = await run_jobs(job_id)
    assert job["status"] == "failed", job
    logs = json.dumps(ok(client, "job.logs", {"id": job_id}), ensure_ascii=False)
    assert ("boom" in logs) if mode == "crash" else ("Provider từ chối khoá" in logs)  # lỗi vẫn được ghi…
    assert LLM_KEY not in logs  # …nhưng đã che khoá
    assert LLM_KEY not in json.dumps(job, ensure_ascii=False)
    assert LLM_KEY not in caplog.text


async def test_provider_rejection_404_falls_back(services):
    import httpx
    import respx

    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "real", adapter="openai_compat", base_url="http://llm.test/v1", default_model="m-gone")
            add_llm(s, "backup", options={"replies": {"story_write_script": "Kịch bản dự phòng"}})
            task = s.scalars(select(LlmTask).where(LlmTask.key == "story_write_script")).one()
            task.route = {"connection": "real", "model": None, "params": {}}
            task.fallbacks = [{"connection": "backup", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    with respx.mock:
        respx.post("http://llm.test/v1/chat/completions").mock(return_value=httpx.Response(
            404, json={"error": {"message": "model_not_found: m-gone"}}))
        out = await llm.run_task(services, "story_write_script", {"input": "x"}, project_id=PID[0])
    assert out.text == "Kịch bản dự phòng" and out.route.connection == "backup"
    assert [(a["connection"], a["ok"], a.get("error_code")) for a in out.attempts] == [
        ("real", False, "provider_rejected"), ("backup", True, None)]


async def test_app_input_error_stops_chain(services):
    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "a")
            add_llm(s, "b")
            task = s.scalars(select(LlmTask).where(LlmTask.key == "story_write_script")).one()
            task.route = {"connection": "a", "model": None, "params": {}}
            task.fallbacks = [{"connection": "b", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    with pytest.raises(AppError) as e:
        await llm.run_task(services, "story_write_script", {}, project_id=PID[0])
    assert e.value.code == "template_variable_missing"


# --- Fix round 1 -----------------------------------------------------------------------------------------------------


async def test_empty_response_moves_to_fallback(services):
    import httpx
    import respx

    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "real", adapter="openai_compat", base_url="http://llm.test/v1", default_model="m")
            add_llm(s, "backup", options={"replies": {"story_write_script": "dự phòng"}})
            task = s.scalars(select(LlmTask).where(LlmTask.key == "story_write_script")).one()
            task.route = {"connection": "real", "model": None, "params": {}}
            task.fallbacks = [{"connection": "backup", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    with respx.mock:
        respx.post("http://llm.test/v1/chat/completions").mock(return_value=httpx.Response(200, content=b""))
        out = await llm.run_task(services, "story_write_script", {"input": "x"}, on_delta=lambda _: None, project_id=PID[0])
    assert out.text == "dự phòng"
    assert [a.get("error_code") for a in out.attempts] == ["provider_empty_response", None]


async def test_truncated_json_moves_to_fallback_and_text_is_flagged(services):
    def setup():
        with services.db.session() as s:
            seed_llm_defaults(s)
            add_llm(s, "short", options={"replies": {"caption": '{"captions": ["a"]}', "story_write_script": "cụt"},
                                         "finish_reason": "length"})
            add_llm(s, "long", options={"replies": {"caption": '{"captions": ["b"]}'}})
            for key in ("caption", "story_write_script"):
                task = s.scalars(select(LlmTask).where(LlmTask.key == key)).one()
                task.route = {"connection": "short", "model": None, "params": {}}
                task.fallbacks = [{"connection": "long", "model": None, "params": {}}]

    await anyio.to_thread.run_sync(setup)
    out = await llm.run_task(services, "caption", {"input": "x"}, project_id=PID[0])
    assert out.data == {"captions": ["b"]} and out.truncated is False
    assert out.attempts[0]["error_code"] == "llm_truncated" and out.attempts[0]["truncated"] is True
    out = await llm.run_task(services, "story_write_script", {"input": "x"}, project_id=PID[0])  # không phải JSON: giữ, đánh dấu bị cắt
    assert out.text == "cụt" and out.truncated is True and out.attempts[0]["finish_reason"] == "length"
    assert out.to_dict()["truncated"] is True


def test_fallback_without_connection_is_rejected(client, app_services):
    ok(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm"})
    r = call(client, "llm.task_update", {"key": "caption", "fallbacks": [{"connection": "fake-llm"}, {"model": "m"}]})
    assert r.status_code == 422 and r.json()["error_code"] == "validation_failed"
    assert r.json()["extra"]["errors"][0]["loc"] == ["fallbacks", 1, "connection"]
    r = call(client, "llm.task_create", {"key": "x_task", "label": "x", "template_key": "caption",
                                         "fallbacks": [{"model": "m"}]})
    assert r.status_code == 422
