"""Adapter ảnh `openai_images` (Task 11): /images/generations, /images/edits khi có ảnh tham chiếu, chống gửi trùng."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from run_stub import RunStub

from sagareel.providers import registry
from sagareel.providers.base import CapabilityError, Connection, ImageRequest, JobUnknown, ProviderJobError
from sagareel.services.jobs import TransientError

BASE = "http://img.test/v1"
GEN = f"{BASE}/images/generations"
EDIT = f"{BASE}/images/edits"
KEY = "sk-SECRET-IMG-5555"
PNG = b"\x89PNG\r\n\x1a\n" + b"\1" * 64
B64 = {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def make(http):
    def factory(api_key: str = KEY, base_url: str = BASE, model: str = "", **options):
        conn = Connection(id=3, name="oa", adapter="openai_images", kind="image", base_url=base_url, api_key=api_key,
                          default_model=model, options=options, concurrency=1)
        return registry.get("openai_images")(conn, http)

    return factory


@pytest.fixture
def run_ctx(tmp_path):
    return RunStub(tmp_path / "work")


def make_req(tmp_path, **kw) -> ImageRequest:
    base = {"prompt": "mèo", "refs": [], "aspect": "9:16", "resolution": "1440p", "model": None, "quality": None,
            "out_dir": tmp_path / "out"}
    return ImageRequest(**{**base, **kw})


@respx.mock
async def test_generation_json_and_b64_result(make, tmp_path, run_ctx):
    prompt = "  Bà cụ bên hiên,\nánh chiều — Ễ ộ  "
    route = respx.post(GEN).mock(return_value=httpx.Response(200, json=B64))
    res = await make().generate(make_req(tmp_path, prompt=prompt), run_ctx)
    body = json.loads(route.calls.last.request.content)
    assert body == {"model": "gpt-image-1", "prompt": prompt, "n": 1, "size": "1024x1536"}
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"
    assert res.paths[0].read_bytes() == PNG and res.paths[0].parent == tmp_path / "out"
    assert run_ctx.saves[0][1]["state"] == "submitting" and run_ctx.external["state"] == "completed"
    assert KEY not in json.dumps(res.meta, ensure_ascii=False)


@respx.mock
@pytest.mark.parametrize(("aspect", "size"), [("16:9", "1536x1024"), ("1:1", "1024x1024"), ("4:5", "1024x1536")])
async def test_size_follows_aspect(make, tmp_path, aspect, size):
    route = respx.post(GEN).mock(return_value=httpx.Response(200, json=B64))
    await make(model="m1").generate(make_req(tmp_path, aspect=aspect), None)
    body = json.loads(route.calls.last.request.content)
    assert body["size"] == size and body["model"] == "m1"


@respx.mock
async def test_size_option_overrides(make, tmp_path):
    route = respx.post(GEN).mock(return_value=httpx.Response(200, json=B64))
    await make(size="2048x2048").generate(make_req(tmp_path), None)
    assert json.loads(route.calls.last.request.content)["size"] == "2048x2048"


@respx.mock
async def test_refs_use_edits_multipart_in_order(make, tmp_path, run_ctx):
    a, b = tmp_path / "a.png", tmp_path / "b.jpg"
    a.write_bytes(b"\x89PNG-first")
    b.write_bytes(b"\xff\xd8\xff-second")
    prompt = " sửa  ảnh\n"
    route = respx.post(EDIT).mock(return_value=httpx.Response(200, json=B64))
    await make().generate(make_req(tmp_path, refs=[a, b], prompt=prompt), run_ctx)
    req = route.calls.last.request
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    content = req.content
    assert content.count(b'name="image[]"') == 2
    assert 0 < content.index(b"\x89PNG-first") < content.index(b"\xff\xd8\xff-second")
    assert b'name="prompt"\r\n\r\n' + prompt.encode("utf-8") + b"\r\n" in content


@respx.mock
async def test_url_result_is_downloaded(make, tmp_path):
    respx.post(GEN).mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.other.test/i.png"}]}))
    dl = respx.get("https://cdn.other.test/i.png").mock(return_value=httpx.Response(200, content=PNG))
    res = await make().generate(make_req(tmp_path), None)
    assert res.paths[0].read_bytes() == PNG
    assert "Authorization" not in dl.calls.last.request.headers  # khoá không đi sang máy khác


@respx.mock
async def test_read_timeout_after_send_is_job_unknown(make, tmp_path, run_ctx):
    respx.post(GEN).mock(side_effect=httpx.ReadTimeout("x"))
    with pytest.raises(JobUnknown) as e:
        await make().generate(make_req(tmp_path), run_ctx)
    assert e.value.code == "openai_images_job_unknown"
    assert run_ctx.external["state"] == "submitting"


@respx.mock
async def test_resume_with_submitting_state_does_not_post(make, tmp_path, run_ctx):
    run_ctx.preset_external(None, state="submitting")
    route = respx.post(GEN)
    with pytest.raises(JobUnknown):
        await make().generate(make_req(tmp_path), run_ctx)
    assert route.call_count == 0


@respx.mock
async def test_resume_after_success_never_reposts(make, tmp_path):
    """Job nối lại sau khi ảnh đã tạo (vd. app tắt lúc đăng ký media): trả lại file cũ, hoặc hỏi người dùng."""
    route = respx.post(GEN).mock(return_value=httpx.Response(200, json=B64))
    run = RunStub(tmp_path / "work")
    first = await make().generate(make_req(tmp_path), run)
    assert run.external["result_file"] == str(first.paths[0])
    again = await make().generate(make_req(tmp_path), run)
    assert again.paths == first.paths and route.call_count == 1
    first.paths[0].unlink()  # media.prepare đã chuyển file đi
    with pytest.raises(JobUnknown):
        await make().generate(make_req(tmp_path), run)
    assert route.call_count == 1


@respx.mock
async def test_rejection_and_gateway_clear_state(make, tmp_path, run_ctx):
    respx.post(GEN).mock(return_value=httpx.Response(400, json={"error": {"message": "safety system"}}))
    with pytest.raises(ProviderJobError) as e:
        await make().generate(make_req(tmp_path), run_ctx)
    assert e.value.code == "provider_rejected" and "safety system" in e.value.msg
    assert run_ctx.external.get("state") is None
    respx.post(GEN).mock(return_value=httpx.Response(503))
    with pytest.raises(TransientError):
        await make().generate(make_req(tmp_path), run_ctx)
    assert run_ctx.external.get("state") is None


@respx.mock
async def test_garbled_success_keeps_state(make, tmp_path, run_ctx):
    respx.post(GEN).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(JobUnknown):
        await make().generate(make_req(tmp_path), run_ctx)
    assert run_ctx.external["state"] == "submitting"


async def test_too_many_refs_is_capability_error(make, tmp_path, run_ctx):
    refs = []
    for i in range(3):
        p = tmp_path / f"r{i}.png"
        p.write_bytes(b"x")
        refs.append(p)
    with pytest.raises(CapabilityError):
        await make(max_refs=2).generate(make_req(tmp_path, refs=refs), run_ctx)
    assert run_ctx.saves == []


async def test_estimate(make, tmp_path):
    assert await make().estimate(make_req(tmp_path)) == {"units": 1, "kind": "image"}


@respx.mock
async def test_health(make):
    route = respx.get(f"{BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-image-1"}, {"id": "dall-e-3"}]}))
    h = await make().check_health()
    assert h.status == "alive" and h.capabilities["models"] == ["gpt-image-1", "dall-e-3"]
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(401))
    h = await make().check_health()
    assert h.status == "dead" and KEY not in h.detail
    respx.get(f"{BASE}/models").mock(side_effect=httpx.ConnectError("x"))
    assert (await make().check_health()).status == "dead"


async def test_health_without_base_url_is_not_configured(make):
    assert (await make(base_url="").check_health()).status == "not_configured"
