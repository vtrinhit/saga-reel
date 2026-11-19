from __future__ import annotations

import httpx
import pytest
from sagareel_mcp.client import AppError, SagaReelClient
from sagareel_mcp.server import BearerAuth, build_http_app, build_server

pytestmark = pytest.mark.anyio

TOKEN = "sr_" + "h" * 32


async def _inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _get(headers=None):
    app = BearerAuth(_inner, TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8991") as c:
        return await c.post("/mcp", headers=headers or {})


async def test_missing_bearer_rejected():
    r = await _get()
    assert r.status_code == 401
    assert r.json()["error_code"] == "unauthorized"
    assert TOKEN not in r.text


async def test_wrong_bearer_rejected():
    r = await _get({"Authorization": "Bearer sr_wrong"})
    assert r.status_code == 401


async def test_correct_bearer_passes():
    r = await _get({"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.text == "ok"


async def test_lifespan_scope_passes_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await BearerAuth(inner, TOKEN)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


def test_http_mode_requires_token():
    server, _ = build_server(SagaReelClient())
    with pytest.raises(AppError) as e:
        build_http_app(server, None)
    assert e.value.code == "token_required"


async def test_http_app_end_to_end_rejects_without_token():
    server, _ = build_server(SagaReelClient())
    app = build_http_app(server, TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8991") as c:
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
