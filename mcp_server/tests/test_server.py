from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.client import Client
from sagareel_mcp.client import SagaReelClient
from sagareel_mcp.server import build_server

from .conftest import APP

pytestmark = pytest.mark.anyio

FILE_TOKEN = "sr_" + "f" * 32


def _text(result) -> str:
    return result.content[0].text


@pytest.fixture
def app_mock(catalog):
    with respx.mock(base_url=APP, assert_all_called=False) as m:
        m.get("/api/ops").respond(json=catalog)
        yield m


async def _session(client: SagaReelClient | None = None):
    server, state = build_server(client or SagaReelClient())
    return Client(server), state


async def test_list_tools_generated_from_catalog(app_mock, catalog):
    c, _ = await _session()
    async with c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    expected = {"sr_" + o["name"].replace(".", "_") for o in catalog if o["mcp"] == "tool"}
    assert expected <= set(tools)
    assert {"sr_describe_ops", "sr_call", "sr_reload", "sr_job_wait"} <= set(tools)
    t = tools["sr_project_delete"]
    assert t.annotations.destructive_hint is True
    assert "confirm_token" in t.description
    assert tools["sr_project_list"].annotations.read_only_hint is True
    assert tools["sr_entity_get"].input_schema["required"] == ["type", "id"]


async def test_op_tool_forwards_params(app_mock):
    route = app_mock.post("/api/ops/project.get").respond(json={"id": 3, "name": "Phim A"})
    c, _ = await _session()
    async with c:
        r = await c.call_tool("sr_project_get", {"id": 3})
    assert not r.is_error
    assert json.loads(route.calls.last.request.content) == {"id": 3}
    assert json.loads(_text(r)) == {"id": 3, "name": "Phim A"}
    assert r.structured_content == {"id": 3, "name": "Phim A"}


async def test_sr_call_returns_app_error_text(app_mock):
    app_mock.post("/api/ops/project.get").respond(422, json={
        "ok": False, "error_code": "validation_failed", "detail": "Dữ liệu đầu vào không hợp lệ.",
        "hint": "Sửa các trường bị lỗi.", "status": 422,
        "extra": {"errors": [{"loc": ["id"], "msg": "Field required"}]},
    })
    c, _ = await _session()
    async with c:
        r = await c.call_tool("sr_call", {"op": "project.get", "params": {}})
    assert r.is_error
    text = _text(r)
    assert text.startswith("validation_failed: Dữ liệu đầu vào không hợp lệ. (gợi ý: Sửa các trường bị lỗi.)")
    assert "  - id: Field required" in text


async def test_sr_call_unknown_op_forwarded(app_mock):
    route = app_mock.post("/api/ops/nope.op").respond(404, json={
        "ok": False, "error_code": "unknown_op", "detail": "Không có thao tác nào tên 'nope.op'.",
        "hint": "Gọi GET /api/ops để xem danh sách thao tác."})
    c, _ = await _session()
    async with c:
        r = await c.call_tool("sr_call", {"op": "nope.op"})
    assert route.called and r.is_error and _text(r).startswith("unknown_op:")


async def test_describe_ops_filters_group(app_mock):
    c, _ = await _session()
    async with c:
        r = await c.call_tool("sr_describe_ops", {"group": "project", "with_schema": False})
        bad = await c.call_tool("sr_describe_ops", {"group": "khong_co"})
    ops = json.loads(_text(r))["ops"]
    assert {o["name"] for o in ops} >= {"project.get", "project.delete"}
    assert all(o["name"].startswith("project.") for o in ops)
    assert all("params_schema" not in o for o in ops)
    assert {o["tool"] for o in ops if o["name"] == "project.get"} == {"sr_project_get"}
    assert bad.is_error and _text(bad).startswith("unknown_group:")


async def test_app_down_at_start_then_reload(catalog):
    with respx.mock(base_url=APP, assert_all_called=False) as m:
        ops_route = m.get("/api/ops")
        ops_route.side_effect = httpx.ConnectError("refused")
        c, _state = await _session()
        async with c:
            tools = {t.name for t in (await c.list_tools()).tools}
            assert "sr_project_get" not in tools and "sr_reload" in tools
            r = await c.call_tool("sr_project_get", {"id": 1})
            assert r.is_error and _text(r).startswith("app_not_running:")
            ops_route.side_effect = None
            ops_route.return_value = httpx.Response(200, json=catalog)
            r = await c.call_tool("sr_reload", {})
            assert not r.is_error, _text(r)
            tools = {t.name for t in (await c.list_tools()).tools}
            assert "sr_project_get" in tools


async def test_job_wait_chunks_until_terminal(app_mock, monkeypatch):
    from sagareel_mcp import server as srv

    monkeypatch.setattr(srv, "JOB_WAIT_CHUNK_S", 5)
    calls = []

    def answer(request):
        body = json.loads(request.content)
        calls.append(body)
        done = len(calls) >= 2
        return httpx.Response(200, json={"items": [{"id": 7, "status": "succeeded" if done else "running"}],
                                         "timed_out": not done})

    app_mock.post("/api/ops/job.wait").mock(side_effect=answer)
    c, _ = await _session()
    async with c:
        r = await c.call_tool("sr_job_wait", {"ids": [7], "timeout": 12})
    out = json.loads(_text(r))
    assert out["timed_out"] is False and out["items"][0]["status"] == "succeeded"
    assert calls == [{"ids": [7], "timeout_s": 5}, {"ids": [7], "timeout_s": 5}]


async def test_job_wait_timeout_returns_timed_out(app_mock, monkeypatch):
    from sagareel_mcp import server as srv

    monkeypatch.setattr(srv, "JOB_WAIT_CHUNK_S", 5)
    calls = []

    def answer(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"items": [{"id": 7, "status": "running"}], "timed_out": True})

    app_mock.post("/api/ops/job.wait").mock(side_effect=answer)
    c, _ = await _session()
    async with c:
        r = await c.call_tool("sr_job_wait", {"ids": [7], "timeout": 7})
        bad = await c.call_tool("sr_job_wait", {"ids": []})
    assert not r.is_error and json.loads(_text(r))["timed_out"] is True
    assert [b["timeout_s"] for b in calls] == [5, 2]
    assert bad.is_error and _text(bad).startswith("invalid_arguments:")


async def test_job_wait_polls_job_get_without_job_wait_op(catalog, monkeypatch):
    from sagareel_mcp import server as srv

    monkeypatch.setattr(srv, "JOB_POLL_S", 0.01)
    states = iter(["queued", "running", "failed"])
    with respx.mock(base_url=APP, assert_all_called=False) as m:
        m.get("/api/ops").respond(json=[o for o in catalog if o["name"] != "job.wait"])
        m.post("/api/ops/job.get").mock(side_effect=lambda req: httpx.Response(
            200, json={"id": 1, "status": next(states)}))
        c, _ = await _session()
        async with c:
            r = await c.call_tool("sr_job_wait", {"ids": [1], "timeout": 5})
    out = json.loads(_text(r))
    assert out == {"items": [{"id": 1, "status": "failed"}], "timed_out": False}


# --- token -----------------------------------------------------------------------------------------------------


def _write_token(tmp_path, token=FILE_TOKEN):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "local-agent.token").write_text(token + "\n", encoding="utf-8")


async def test_env_token_wins(app_mock, monkeypatch, tmp_path):
    _write_token(tmp_path)
    monkeypatch.setenv("SAGAREEL_TOKEN", "sr_envtoken_aaaaaaaaaaaaaaaaaaaa")
    route = app_mock.post("/api/ops/system.info").respond(json={})
    client = SagaReelClient()
    assert client.token_source == "env"
    await client.call("system.info")
    assert route.calls.last.request.headers["authorization"] == "Bearer sr_envtoken_aaaaaaaaaaaaaaaaaaaa"
    await client.aclose()


async def test_file_token_on_loopback(app_mock, tmp_path):
    _write_token(tmp_path)
    route = app_mock.post("/api/ops/system.info").respond(json={})
    client = SagaReelClient()
    assert client.token_source == "file"
    await client.call("system.info")
    assert route.calls.last.request.headers["authorization"] == f"Bearer {FILE_TOKEN}"
    await client.aclose()


async def test_file_token_never_sent_to_remote(monkeypatch, tmp_path):
    _write_token(tmp_path)
    monkeypatch.setenv("SAGAREEL_URL", "https://studio.example.com")
    with respx.mock(base_url="https://studio.example.com") as m:
        route = m.post("/api/ops/system.info").respond(401, json={
            "ok": False, "error_code": "auth_required", "detail": "Cần đăng nhập.", "hint": None})
        client = SagaReelClient()
        assert client.token is None and client.token_source == "none"
        r = None
        try:
            await client.call("system.info")
        except Exception as e:  # noqa: BLE001
            r = e
        assert getattr(r, "code", None) == "auth_required"
        assert all("authorization" not in c.request.headers for c in route.calls)
        await client.aclose()


async def test_file_token_reread_on_401(app_mock, tmp_path):
    _write_token(tmp_path, "sr_" + "o" * 32)
    new = "sr_" + "n" * 32
    seen = []

    def answer(request):
        seen.append(request.headers.get("authorization"))
        if request.headers.get("authorization") != f"Bearer {new}":
            return httpx.Response(401, json={"ok": False, "error_code": "auth_required", "detail": "x"})
        return httpx.Response(200, json={"ok": True})

    app_mock.post("/api/ops/system.info").mock(side_effect=answer)
    client = SagaReelClient()
    _write_token(tmp_path, new)  # app khởi động lại → token mới
    assert await client.call("system.info") == {"ok": True}
    assert seen == ["Bearer sr_" + "o" * 32, f"Bearer {new}"]
    await client.aclose()


async def test_token_suspended_surfaces_code_and_hint_without_reread(app_mock, tmp_path):
    """Task BF: agent bị tạm dừng → 403 token_suspended đi thẳng ra (mã + hint), không đọc lại file token."""
    _write_token(tmp_path)
    route = app_mock.post("/api/ops/system.info").respond(403, json={
        "ok": False, "error_code": "token_suspended", "detail": "Agent 'x' đang bị tạm dừng.",
        "hint": "Chủ sở hữu bật lại trong giao diện."})
    client = SagaReelClient()
    err = None
    try:
        await client.call("system.info")
    except Exception as e:  # noqa: BLE001
        err = e
    assert getattr(err, "code", None) == "token_suspended" and "bật lại" in (getattr(err, "hint", "") or "")
    assert len(route.calls) == 1
    await client.aclose()
