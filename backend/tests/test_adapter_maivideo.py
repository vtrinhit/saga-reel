"""Adapter ảnh `maivideo` (Task 11): payload nguyên văn, poll, tải ảnh, chống tạo trùng job tốn tiền."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
import respx
from run_stub import RunStub

from sagareel.providers import registry
from sagareel.providers.base import (
    CapabilityError,
    Connection,
    ImageRequest,
    JobUnknown,
    ProviderJobError,
    ProviderUnavailable,
)
from sagareel.services.jobs import NeedsAttention, TransientError

BASE = "http://mai.test"
URL = f"{BASE}/api/v1/image-generation"
JOB_URL = f"{URL}/j1"
STATUS_URL = f"{URL}/status"
MODELS_URL = f"{URL}/models"
FILE_URL = f"{BASE}/static/exports/a.png"
KEY = "mv-SECRET-KEY-4321"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 2048
DONE = {"job_id": "j1", "status": "completed", "images": [{"file_url": "/static/exports/a.png"}]}


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def make(http):
    def factory(api_key: str = KEY, base_url: str = BASE, **options):
        conn = Connection(id=7, name="mai", adapter="maivideo", kind="image", base_url=base_url, api_key=api_key,
                          default_model="gpt-image-2.0", options={"poll_interval": 0, **options}, concurrency=1)
        return registry.get("maivideo")(conn, http)

    return factory


@pytest.fixture
def adapter(make):
    return make()


@pytest.fixture
def run_ctx(tmp_path):
    return RunStub(tmp_path / "work")


@pytest.fixture
def req(tmp_path):
    return ImageRequest(prompt="mèo", refs=[], aspect="9:16", resolution="1440p", model=None, quality=None,
                        out_dir=tmp_path / "out")


def mock_success(post_body: dict | None = None):
    post = respx.post(URL).mock(return_value=httpx.Response(200, json=post_body or {"job_id": "j1", "status": "queued"}))
    respx.get(JOB_URL).mock(side_effect=[httpx.Response(200, json={"job_id": "j1", "status": "processing"}),
                                         httpx.Response(200, json=DONE)])
    download = respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=PNG))
    return post, download


# --- Đường thành công ---------------------------------------------------------------------------------------------


@respx.mock
async def test_happy_path_downloads_image(adapter, req, run_ctx):
    post, download = mock_success()
    res = await adapter.generate(req, run_ctx)
    assert len(res.paths) == 1 and res.paths[0].read_bytes() == PNG
    assert res.paths[0].parent == req.out_dir and res.paths[0].suffix == ".png"
    assert run_ctx.external_id == "j1" and run_ctx.external["state"] == "submitted"
    assert post.calls.last.request.headers["X-API-Key"] == KEY
    assert download.calls.last.request.headers["X-API-Key"] == KEY
    assert res.meta["job_id"] == "j1" and res.meta["adapter"] == "maivideo"
    assert not list(req.out_dir.glob("*.tmp"))  # ghi qua file tạm rồi os.replace
    assert KEY not in json.dumps(res.meta, ensure_ascii=False) + repr(run_ctx.saves) + repr(run_ctx.logs)


@respx.mock
async def test_marker_is_durable_before_post(adapter, req, run_ctx):
    seen = {}

    def on_post(request):
        seen["external"] = dict(run_ctx.external)
        return httpx.Response(200, json={"job_id": "j1"})

    respx.post(URL).mock(side_effect=on_post)
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json=DONE))
    respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=PNG))
    await adapter.generate(req, run_ctx)
    assert seen["external"]["state"] == "submitting" and len(seen["external"]["fingerprint"]) == 64
    assert run_ctx.saves[0] == (None, {"state": "submitting", "fingerprint": seen["external"]["fingerprint"]})


@respx.mock
async def test_prompt_and_refs_sent_verbatim_in_order(adapter, tmp_path, run_ctx):
    a, b = tmp_path / "a.png", tmp_path / "b.jpg"
    a.write_bytes(b"\x89PNG-aaa")
    b.write_bytes(b"\xff\xd8\xff-bbb")
    prompt = "  Bà cụ ngồi bên hiên,\nánh chiều — Ễ ộ ữ  "
    post, _ = mock_success()
    req = ImageRequest(prompt=prompt, refs=[a, b], aspect="16:9", resolution="1080p", model="gpt-image-2.5",
                       quality="high", out_dir=tmp_path / "out")
    await adapter.generate(req, run_ctx)
    body = json.loads(post.calls.last.request.content)
    assert body["prompt"] == prompt
    assert body["reference_image_urls"] == [
        "data:image/png;base64," + base64.b64encode(b"\x89PNG-aaa").decode(),
        "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff-bbb").decode(),
    ]
    assert {k: body[k] for k in ("model", "resolution", "detail_level", "aspect_ratio", "n")} == {
        "model": "gpt-image-2.5", "resolution": "1080p", "detail_level": "high", "aspect_ratio": "16:9", "n": 1}


@respx.mock
async def test_defaults_and_resolution_tiers(adapter, req, run_ctx):
    post, _ = mock_success()
    req.resolution = "4K"
    await adapter.generate(req, run_ctx)
    body = json.loads(post.calls.last.request.content)
    assert body["model"] == "gpt-image-2.0" and body["resolution"] == "2160p" and body["detail_level"] == "medium"
    assert "reference_image_urls" not in body


@respx.mock
async def test_fingerprint_depends_on_ref_content_not_path(make, tmp_path):
    fps = []
    for content in (b"one", b"one", b"two"):
        ref = tmp_path / f"r{len(fps)}.png"
        ref.write_bytes(content)
        run = RunStub(tmp_path)
        respx.post(URL).mock(side_effect=httpx.ReadTimeout("x"))
        req = ImageRequest(prompt="p", refs=[ref], aspect="9:16", resolution="1440p", model=None, quality=None,
                           out_dir=tmp_path / "out")
        with pytest.raises(JobUnknown):
            await make().generate(req, run)
        fps.append(run.external["fingerprint"])
    assert fps[0] == fps[1] != fps[2]


@respx.mock
async def test_404_falls_back_to_batch_status(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j1"}))
    respx.get(JOB_URL).mock(return_value=httpx.Response(404))
    batch = respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"jobs": [DONE], "missing_job_ids": []}))
    respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=PNG))
    res = await adapter.generate(req, run_ctx)
    assert res.paths[0].read_bytes() == PNG
    assert batch.calls.last.request.url.params["job_ids"] == "j1"


@respx.mock
async def test_job_missing_everywhere_needs_attention_keeping_id(adapter, req, run_ctx):
    run_ctx.preset_external("j1", state="submitted")
    respx.get(JOB_URL).mock(return_value=httpx.Response(404))
    respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"jobs": [], "missing_job_ids": ["j1"]}))
    with pytest.raises(NeedsAttention) as e:
        await adapter.generate(req, run_ctx)
    assert e.value.code == "maivideo_job_missing" and run_ctx.external_id == "j1"


@respx.mock
async def test_absolute_file_url_on_other_host_gets_no_key(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j1"}))
    done = {"status": "completed", "images": [{"file_url": "https://cdn.other.test/x.png"}]}
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json=done))
    dl = respx.get("https://cdn.other.test/x.png").mock(return_value=httpx.Response(200, content=PNG))
    await adapter.generate(req, run_ctx)
    assert "X-API-Key" not in dl.calls.last.request.headers


# --- Chống tạo trùng ----------------------------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("exc", [httpx.ReadTimeout("x"), httpx.RemoteProtocolError("x"), httpx.ReadError("x"),
                                 httpx.WriteTimeout("x")])
async def test_read_timeout_after_send_is_job_unknown(adapter, req, run_ctx, exc):
    respx.post(URL).mock(side_effect=exc)
    with pytest.raises(JobUnknown) as e:
        await adapter.generate(req, run_ctx)
    assert e.value.code == "maivideo_job_unknown" and "MaiVideo" in e.value.hint
    assert run_ctx.external["state"] == "submitting" and run_ctx.external_id is None


@respx.mock
async def test_resume_with_submitting_state_does_not_post(adapter, req, run_ctx):
    run_ctx.preset_external(None, state="submitting")
    route = respx.post(URL)
    with pytest.raises(JobUnknown) as e:
        await adapter.generate(req, run_ctx)
    assert route.call_count == 0 and e.value.code == "maivideo_job_unknown"
    assert run_ctx.external["state"] == "submitting"


@respx.mock
async def test_resume_with_job_id_only_polls(adapter, req, run_ctx):
    run_ctx.preset_external("j1", state="submitted")
    route = respx.post(URL)
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json=DONE))
    respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=PNG))
    res = await adapter.generate(req, run_ctx)
    assert route.call_count == 0 and res.paths[0].is_file()


@respx.mock
@pytest.mark.parametrize("status", [502, 503, 521, 522, 523, 525, 526, 530])
async def test_gateway_502_is_transient_and_clears_state(adapter, req, run_ctx, status):
    respx.post(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(TransientError):
        await adapter.generate(req, run_ctx)
    assert run_ctx.external.get("state") is None and run_ctx.external_id is None


@respx.mock
@pytest.mark.parametrize("exc", [httpx.ConnectError("x"), httpx.ConnectTimeout("x"), httpx.PoolTimeout("x")])
async def test_unsent_errors_are_transient_and_clear_state(adapter, req, run_ctx, exc):
    respx.post(URL).mock(side_effect=exc)
    with pytest.raises(TransientError):
        await adapter.generate(req, run_ctx)
    assert run_ctx.external.get("state") is None


@respx.mock
async def test_4xx_is_rejected_and_clears_state(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(400, json={"message": "prompt quá dài"}))
    with pytest.raises(ProviderJobError) as e:
        await adapter.generate(req, run_ctx)
    assert e.value.code == "provider_rejected" and "prompt quá dài" in e.value.msg
    assert run_ctx.external.get("state") is None


@respx.mock
async def test_429_is_transient_with_retry_after(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(TransientError) as e:
        await adapter.generate(req, run_ctx)
    assert e.value.retry_after == 7 and run_ctx.external.get("state") is None


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(504), httpx.Response(524),
                                      httpx.Response(200, json={"status": "queued"}),
                                      httpx.Response(200, content=b"<html>")])
async def test_ambiguous_answers_keep_state(adapter, req, run_ctx, response):
    respx.post(URL).mock(return_value=response)
    with pytest.raises(JobUnknown):
        await adapter.generate(req, run_ctx)
    assert run_ctx.external["state"] == "submitting" and run_ctx.external_id is None


@respx.mock
async def test_job_error_status_is_provider_failed(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j1"}))
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json={"status": "failed",
                                                                   "images": [{"error": "vi phạm nội dung"}]}))
    with pytest.raises(ProviderJobError) as e:
        await adapter.generate(req, run_ctx)
    assert e.value.code == "provider_failed" and "vi phạm nội dung" in e.value.msg


@respx.mock
async def test_poll_timeout_is_transient_and_keeps_job_id(make, req, run_ctx):
    post = respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j1"}))
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json={"status": "queued"}))
    with pytest.raises(TransientError):
        await make(timeout_s=0).generate(req, run_ctx)
    assert run_ctx.external_id == "j1"
    # lần sau: chỉ poll, không POST lại
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json=DONE))
    respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=PNG))
    await make().generate(req, run_ctx)
    assert post.call_count == 1


@respx.mock
async def test_poll_errors_are_retried_until_done(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j1"}))
    respx.get(JOB_URL).mock(side_effect=[httpx.ConnectError("x"), httpx.Response(500), httpx.Response(200, json=DONE)])
    respx.get(FILE_URL).mock(return_value=httpx.Response(200, content=PNG))
    res = await adapter.generate(req, run_ctx)
    assert res.paths[0].is_file()


@respx.mock
async def test_download_failure_is_transient_and_keeps_job_id(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j1"}))
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json=DONE))
    respx.get(FILE_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(TransientError):
        await adapter.generate(req, run_ctx)
    assert run_ctx.external_id == "j1" and not list(req.out_dir.glob("*"))


@respx.mock
async def test_cancel_during_poll(adapter, req, run_ctx):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j1"}))
    respx.get(JOB_URL).mock(return_value=httpx.Response(200, json={"status": "queued"}))
    run_ctx.cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        await adapter.generate(req, run_ctx)


@respx.mock
async def test_too_many_refs_is_capability_error(adapter, tmp_path, run_ctx):
    refs = []
    for i in range(10):
        p = tmp_path / f"r{i}.png"
        p.write_bytes(b"x")
        refs.append(p)
    route = respx.post(URL)
    req = ImageRequest(prompt="x", refs=refs, aspect="9:16", resolution="1440p", model=None, quality=None,
                       out_dir=tmp_path / "out")
    with pytest.raises(CapabilityError):
        await adapter.generate(req, run_ctx)
    assert route.call_count == 0 and run_ctx.saves == []


class _BrokenSaveRun(RunStub):
    """`save_external(job_id, …)` hỏng (vd. DB khoá/đầy) sau khi MaiVideo đã nhận job."""

    def __init__(self, workdir, exc: BaseException) -> None:
        super().__init__(workdir)
        self.exc = exc

    async def save_external(self, external_id=None, **state):
        if external_id is not None:
            self.logs.append(("debug", "<save_external job_id>"))
            raise self.exc
        await super().save_external(external_id, **state)


@respx.mock
async def test_job_id_logged_before_save_and_db_error_is_job_unknown_with_id(adapter, req, tmp_path):
    import sqlite3

    run = _BrokenSaveRun(tmp_path / "w", sqlite3.OperationalError("database is locked"))
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j-777", "status": "queued"}))
    poll = respx.get(f"{URL}/j-777").mock(return_value=httpx.Response(200, json=DONE))
    with pytest.raises(JobUnknown) as e:
        await adapter.generate(req, run)
    assert e.value.code == "maivideo_job_unknown" and "j-777" in str(e.value)
    assert KEY not in str(e.value) + (e.value.hint or "")
    msgs = [m for _, m in run.logs]
    first_with_id = next(i for i, m in enumerate(msgs) if "j-777" in m)
    assert first_with_id < msgs.index("<save_external job_id>")  # job id đã vào log trước khi lưu
    assert poll.call_count == 0


@respx.mock
async def test_job_id_also_goes_to_app_log_before_save(adapter, req, tmp_path, caplog):
    """`run.log` đi qua cùng bộ ghi DB đang hỏng → mã job (đã tính tiền) phải nằm cả trong log ứng dụng."""
    import logging
    import sqlite3

    run = _BrokenSaveRun(tmp_path / "w", sqlite3.OperationalError("disk I/O error"))
    run.job_id = 42
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j-779"}))
    with caplog.at_level(logging.INFO, logger="sagareel.providers.maivideo"), pytest.raises(JobUnknown):
        await adapter.generate(req, run)
    recs = [r for r in caplog.records if r.name == "sagareel.providers.maivideo" and "j-779" in r.getMessage()]
    assert recs and recs[0].levelno >= logging.WARNING and "#42" in recs[0].getMessage()
    assert all(KEY not in r.getMessage() for r in caplog.records)


@respx.mock
async def test_job_state_lost_on_save_propagates(adapter, req, tmp_path):
    from sagareel.services.jobs import JobStateLost

    run = _BrokenSaveRun(tmp_path / "w", JobStateLost("Job #1 không còn chạy"))
    respx.post(URL).mock(return_value=httpx.Response(200, json={"job_id": "j-778"}))
    with pytest.raises(JobStateLost):
        await adapter.generate(req, run)


async def test_missing_key_is_unavailable(make, req, run_ctx):
    with pytest.raises(ProviderUnavailable):
        await make(api_key="").generate(req, run_ctx)
    assert run_ctx.saves == []


async def test_estimate(adapter, req):
    assert await adapter.estimate(req) == {"units": 1, "kind": "image"}


# --- Sức khoẻ -----------------------------------------------------------------------------------------------------

CATALOG = {
    "success": True,
    "models": ["gpt-image-2.0", "gpt-image-2.5"],
    "model_resolutions": {"gpt-image-2.0": ["1080p", "1440p", "2160p"]},
    "model_aspect_ratios": {"gpt-image-2.0": ["9:16", "16:9", "1:1"]},
    "model_detail_levels": {"gpt-image-2.0": ["low", "medium", "high"]},
    "max_reference_images": 9,
    "max_reference_images_by_model": {"gpt-image-2.0": 9},
    "max_prompt_length": 4000,
    "quota": {"remaining": 0, "available": 120},
}


@respx.mock
async def test_health_reads_quota(adapter):
    route = respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json=CATALOG))
    h = await adapter.check_health()
    assert h.status == "alive"  # hạn mức tháng = 0 nhưng còn tín dụng: không phải "dead"
    assert h.capabilities["max_reference_images"] == 9
    assert h.capabilities["quota"] == {"remaining": 0, "available": 120}
    assert {"models", "model_resolutions", "model_aspect_ratios", "model_detail_levels"} <= set(h.capabilities)
    assert route.calls.last.request.headers["X-API-Key"] == KEY
    assert KEY not in h.detail


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_health_bad_key_is_dead(adapter, status):
    respx.get(MODELS_URL).mock(return_value=httpx.Response(status))
    h = await adapter.check_health()
    assert h.status == "dead" and KEY not in h.detail


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 521, 530])
async def test_health_rate_limit_or_server_error_is_unknown(adapter, status):
    respx.get(MODELS_URL).mock(return_value=httpx.Response(status, json={"error": f"lỗi {KEY}"}))
    h = await adapter.check_health()
    assert h.status == "unknown" and str(status) in h.detail and KEY not in h.detail


@respx.mock
async def test_health_other_4xx_is_dead(adapter):
    respx.get(MODELS_URL).mock(return_value=httpx.Response(404))
    assert (await adapter.check_health()).status == "dead"


@respx.mock
async def test_health_unreachable_is_dead(adapter):
    respx.get(MODELS_URL).mock(side_effect=httpx.ConnectError("x"))
    assert (await adapter.check_health()).status == "dead"


async def test_health_without_key_is_not_configured(make):
    assert (await make(api_key="").check_health()).status == "not_configured"


def test_registered_with_config_fields():
    cls = registry.get("maivideo")
    names = {f.name for f in cls.config_fields}
    assert {"base_url", "api_key", "default_model", "poll_interval", "timeout_s"} <= names
    timeout = next(f for f in cls.config_fields if f.name == "timeout_s")
    assert timeout.default == 1800
    assert cls.kind == "image" and cls.default_capabilities["max_reference_images"] == 9
