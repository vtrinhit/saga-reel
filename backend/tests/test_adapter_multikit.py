"""Adapter video `multikit` (GrokTele/MultiKit, Grok ảnh → video) — mọi HTTP qua respx, không gọi GrokTele thật."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from sagareel.providers import registry
from sagareel.providers.base import (
    Connection,
    JobUnknown,
    ProviderJobError,
    ProviderUnavailable,
    VideoRequest,
)
from sagareel.services.jobs import TransientError

BASE = "http://gt.test"
KEY = "gt-secret-key-9876"
PHONE = "+84901234567"
TG_USER = "nguyenvana_tg"


# --- Helper -------------------------------------------------------------------------------------------------------


class FakeRun:
    """Đủ phần `JobRun` adapter dùng: client_ref, external_id/external, save_external (gộp như thật), progress, log."""

    def __init__(self, job_id: int = 42, client_ref: str | None = "proj7:scene03") -> None:
        self.job_id = job_id
        self.client_ref = client_ref
        self.external_id: str | None = None
        self.external: dict = {}
        self.cancelled = asyncio.Event()
        self.saves: list[tuple[str | None, dict]] = []
        self.progresses: list[tuple[float | None, str | None]] = []
        self.logs: list[tuple[str, str]] = []

    async def save_external(self, external_id: str | None = None, **state) -> None:
        self.saves.append((external_id, dict(state)))
        self.external = {**self.external, **state}
        if external_id is not None:
            self.external_id = external_id

    def progress(self, pct=None, stage=None, eta_s=None) -> None:
        self.progresses.append((pct, stage))

    def log(self, level: str, msg: str) -> None:
        self.logs.append((level, msg))


def make_adapter(http: httpx.AsyncClient, *, api_key: str = KEY, base_url: str = BASE, **options):
    conn = Connection(id=3, name="grok", adapter="multikit", kind="video", base_url=base_url, api_key=api_key,
                      default_model="", options={"poll_interval": 0, **options}, concurrency=2)
    return registry.get("multikit")(conn, http)


@pytest.fixture(scope="session")
def sample_mp4(tmp_path_factory) -> Path:
    """MP4 thật như GrokTele tải về: H.264 24 fps + AAC 48 kHz + luồng ảnh bìa `mjpeg` (attached_pic), 1 s."""
    folder = tmp_path_factory.mktemp("mp4")
    cover, out = folder / "cover.jpg", folder / "sample.mp4"
    ff = shutil.which("ffmpeg")
    for args in (
        ["-f", "lavfi", "-i", "color=red:size=64x64", "-frames:v", "1", str(cover)],
        ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-i", str(cover),
         "-map", "0:v", "-map", "1:a", "-map", "2:v", "-t", "1", "-c:v:0", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:v:1", "mjpeg",
         "-disposition:v:1", "attached_pic", str(out)],
    ):
        subprocess.run([ff, "-v", "error", "-y", *args], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    assert out.stat().st_size > 5 * 1024
    return out


@pytest.fixture
async def http():
    async with httpx.AsyncClient(base_url=BASE) as c:
        yield c


@pytest.fixture
def adapter(http):
    return make_adapter(http)


@pytest.fixture
def run_ctx() -> FakeRun:
    return FakeRun()


@pytest.fixture
def req(tmp_path) -> VideoRequest:
    img = tmp_path / "in" / "frame.png"
    img.parent.mkdir()
    Image.new("RGB", (64, 112), (200, 80, 40)).save(img)
    return VideoRequest(image=img, prompt="Cô gái quay đầu", duration_s=6, resolution="720p", aspect="9:16",
                        out_path=tmp_path / "job" / "video.mp4")


def body_of(call) -> dict:
    return json.loads(call.request.content)


def accepted(job_id: str = "job_1", dedup: bool = False) -> httpx.Response:
    return httpx.Response(202, json={"jobId": job_id, "status": "queued", "deduplicated": dedup})


def running(job_id: str = "job_1", progress: float = 0.5) -> httpx.Response:
    return httpx.Response(200, json={"jobId": job_id, "status": "running", "stage": "rendering",
                                     "progress": progress, "result": None, "error": None})


def succeeded_copying(sample: Path, post_route, job_id: str = "job_1"):
    """side_effect cho GET: chép file mẫu vào `outputPath` của lần POST cuối rồi trả `succeeded`."""

    def effect(request):
        out = Path(body_of(post_route.calls.last)["outputPath"])
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample, out)
        return httpx.Response(200, json={"jobId": job_id, "status": "succeeded", "stage": "done", "progress": 1,
                                         "accountLabel": "acc1",
                                         "result": {"filePath": str(out), "bytes": out.stat().st_size},
                                         "error": None})

    return effect


def api_error(status: int, code: str, message: str, **details) -> httpx.Response:
    err = {"code": code, "message": message}
    if details:
        err["details"] = details
    return httpx.Response(status, json={"error": err})


# --- Đăng ký, năng lực --------------------------------------------------------------------------------------------


def test_registered_as_video_with_capabilities():
    cls = registry.get("multikit")
    assert cls.kind == "video"
    assert cls.default_capabilities == {
        "durations": [6, 10, 15], "resolutions": ["480p", "720p", "1080p"], "aspects": ["9:16", "16:9"],
        "needs_image": True, "has_audio": True,
    }
    assert [f.name for f in cls.config_fields if f.type == "secret"] == ["api_key"]


# --- Sức khoẻ -----------------------------------------------------------------------------------------------------


ACCOUNTS_NONE_USABLE = {
    "accounts": [
        {"id": "acc1", "label": "acc1", "status": "dead", "reason": "session_revoked", "telegramUser": TG_USER,
         "phone": PHONE},
        {"id": "acc2", "label": "acc2", "status": "expired", "reason": None, "telegramUser": PHONE},
    ],
    "summary": {"total": 2, "usable": 0, "active": 0},
}


@respx.mock
async def test_health_dead_when_no_usable_account(adapter):
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"ok": True}))
    acc = respx.get(f"{BASE}/grok/accounts").mock(return_value=httpx.Response(200, json=ACCOUNTS_NONE_USABLE))
    h = await adapter.check_health()
    assert h.status == "dead"
    assert "acc1: dead (session_revoked)" in h.detail
    assert "acc2: expired" in h.detail
    for secret in (PHONE, "901234567", TG_USER, "phone", "telegramUser", KEY):
        assert secret not in h.detail
    assert acc.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"


@respx.mock
async def test_health_alive_with_usable_account(adapter):
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{BASE}/grok/accounts").mock(return_value=httpx.Response(200, json={
        "accounts": [{"label": "acc1", "status": "active", "telegramUser": TG_USER}],
        "summary": {"total": 1, "usable": 1}}))
    h = await adapter.check_health()
    assert h.status == "alive"
    assert h.capabilities["durations"] == [6, 10, 15]
    assert TG_USER not in h.detail


@respx.mock
async def test_health_network_error_is_dead(adapter):
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    h = await adapter.check_health()
    assert h.status == "dead"
    assert "Chưa mở GrokTele/MultiKit" in h.detail


@respx.mock
async def test_health_bad_key_is_dead_without_leaking_key(adapter):
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{BASE}/grok/accounts").mock(return_value=api_error(401, "unauthorized", "Sai hoặc thiếu API key"))
    h = await adapter.check_health()
    assert h.status == "dead"
    assert KEY not in h.detail


async def test_health_not_configured_without_url(http):
    h = await make_adapter(http, base_url="").check_health()
    assert h.status == "not_configured"


@respx.mock
async def test_no_auth_header_when_key_empty(http):
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"ok": True}))
    acc = respx.get(f"{BASE}/grok/accounts").mock(return_value=httpx.Response(200, json={
        "accounts": [], "summary": {"total": 0, "usable": 0}}))
    await make_adapter(http, api_key="").check_health()
    assert "Authorization" not in acc.calls.last.request.headers


# --- Gửi + poll ---------------------------------------------------------------------------------------------------


@respx.mock
async def test_submit_poll_success(adapter, run_ctx, req, sample_mp4):
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    responses = iter([running(progress=0.25), running(progress=0.5)])
    finish = succeeded_copying(sample_mp4, post)
    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=lambda request: next(responses, None) or finish(request))

    result = await adapter.generate(req, run_ctx)

    body = body_of(post.calls.last)
    assert body == {
        "imagePath": str(req.image.resolve()), "prompt": "Cô gái quay đầu", "aspectRatio": "9:16", "duration": 6,
        "resolution": "720p", "outputPath": str(req.out_path.resolve()), "clientRef": "proj7:scene03", "priority": 0,
    }
    assert post.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"
    # đánh dấu "submitting" ghi TRƯỚC khi POST, jobId ghi ngay sau 202
    assert run_ctx.saves[0] == (None, {"state": "submitting", "client_ref": "proj7:scene03"})
    assert run_ctx.saves[1][0] == "job_1"
    assert run_ctx.external_id == "job_1" and run_ctx.external["state"] == "submitted"
    assert [p for p, _ in run_ctx.progresses if p is not None][:2] == [25.0, 50.0]
    assert result.path == req.out_path.resolve()
    assert result.meta["has_audio"] is True
    assert result.meta["size"] == sample_mp4.stat().st_size
    assert 900 <= result.meta["duration_ms"] <= 1200
    # luồng ảnh bìa mjpeg không được lẫn vào thông tin video chính
    assert (result.meta["vcodec"], result.meta["width"], result.meta["height"]) == ("h264", 320, 240)
    assert result.meta["job_id"] == "job_1" and result.meta["client_ref"] == "proj7:scene03"


@respx.mock
async def test_prompt_verbatim(adapter, run_ctx, req, sample_mp4):
    req.prompt = "  Cô gái  quay đầu,\n\tmỉm cười…  \n"
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=succeeded_copying(sample_mp4, post))
    await adapter.generate(req, run_ctx)
    raw = post.calls.last.request.content.decode("utf-8")
    assert json.loads(raw)["prompt"] == "  Cô gái  quay đầu,\n\tmỉm cười…  \n"


@respx.mock
async def test_client_ref_falls_back_to_job_id(adapter, req, sample_mp4):
    run = FakeRun(job_id=77, client_ref=None)
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=succeeded_copying(sample_mp4, post))
    await adapter.generate(req, run)
    assert body_of(post.calls.last)["clientRef"] == "sagareel:job:77"


@pytest.mark.parametrize(("aspect", "sent"), [("1:1", "9:16"), ("4:5", "9:16"), ("3:4", "9:16"), ("4:3", "16:9")])
@respx.mock
async def test_other_aspects_map_to_grok_buttons(adapter, run_ctx, req, sample_mp4, aspect, sent):
    """Grok chỉ có nút Dọc 9:16 / Ngang 16:9 (GrokTele cũng tự quy đổi như vậy): gửi tỉ lệ đã quy đổi, ghi vào meta."""
    req.aspect = aspect
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=succeeded_copying(sample_mp4, post))
    result = await adapter.generate(req, run_ctx)
    assert body_of(post.calls.last)["aspectRatio"] == sent
    assert result.meta["aspect_sent"] == sent and result.meta["requested"]["aspect"] == aspect


async def test_unparseable_aspect_is_capability_error(adapter, run_ctx, req):
    from sagareel.providers.base import CapabilityError

    req.aspect = "dọc"
    with pytest.raises(CapabilityError):
        await adapter.generate(req, run_ctx)
    assert run_ctx.saves == []


async def test_unsupported_duration_is_capability_error(adapter, run_ctx, req):
    from sagareel.providers.base import CapabilityError

    req.duration_s = 8
    with pytest.raises(CapabilityError):
        await adapter.generate(req, run_ctx)
    assert run_ctx.saves == []


# --- Lỗi khi gửi: xoá dấu "submitting" khi chắc chắn GrokTele không nhận --------------------------------------------


def assert_marker_cleared(run: FakeRun) -> None:
    assert run.external_id is None
    assert run.external.get("state") is None
    assert run.saves[0][1]["state"] == "submitting"  # đã đánh dấu trước khi POST


@respx.mock
async def test_409_no_account_is_provider_unavailable(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=api_error(
        409, "conflict", "no_account_available: mọi tài khoản đều hết hạn", reason="no_account_available"))
    with pytest.raises(ProviderUnavailable) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "provider_unavailable"
    assert ei.value.hint == "Mở GrokTele kiểm tra tài khoản (cần đăng nhập Telegram/gia hạn)"
    assert_marker_cleared(run_ctx)


@respx.mock
async def test_507_is_disk_full(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=api_error(
        507, "insufficient_storage", "Ổ đĩa còn 200 MB", reason="disk_full"))
    with pytest.raises(ProviderJobError) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "provider_disk_full"
    assert_marker_cleared(run_ctx)


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_auth_rejected_is_provider_unavailable(adapter, run_ctx, req, status):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=api_error(status, "unauthorized", "Sai hoặc thiếu API key"))
    with pytest.raises(ProviderUnavailable) as ei:
        await adapter.generate(req, run_ctx)
    assert KEY not in ei.value.msg and KEY not in (ei.value.hint or "")
    assert_marker_cleared(run_ctx)


@respx.mock
async def test_400_invalid_input_is_job_error(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=api_error(
        400, "invalid_input", "imagePath: không tồn tại", field="imagePath"))
    with pytest.raises(ProviderJobError) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "provider_invalid_input"
    assert_marker_cleared(run_ctx)


@respx.mock
async def test_connect_refused_clears_marker(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailable) as ei:
        await adapter.generate(req, run_ctx)
    assert "Chưa mở GrokTele/MultiKit" in ei.value.msg
    assert_marker_cleared(run_ctx)


@respx.mock
async def test_gateway_status_clears_marker_and_is_transient(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=httpx.Response(502))
    with pytest.raises(TransientError):
        await adapter.generate(req, run_ctx)
    assert_marker_cleared(run_ctx)


@respx.mock
async def test_ambiguous_send_keeps_marker(adapter, run_ctx, req):
    """Gửi xong mà mất kết nối khi chờ trả lời: không biết GrokTele đã nhận chưa → giữ dấu (clientRef chống trùng)."""
    respx.post(f"{BASE}/grok/jobs").mock(side_effect=httpx.ReadTimeout("timeout"))
    with pytest.raises(TransientError):
        await adapter.generate(req, run_ctx)
    assert run_ctx.external_id is None
    assert run_ctx.external == {"state": "submitting", "client_ref": "proj7:scene03"}


@respx.mock
async def test_202_without_job_id_is_job_unknown(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=httpx.Response(202, json={"status": "queued"}))
    with pytest.raises(JobUnknown) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "multikit_job_unknown"
    assert run_ctx.external["state"] == "submitting"


# --- Nối lại ------------------------------------------------------------------------------------------------------


@respx.mock
async def test_resume_reuses_client_ref(adapter, run_ctx, req, sample_mp4):
    post = respx.post(f"{BASE}/grok/jobs")
    post.side_effect = [httpx.ReadTimeout("mất kết nối sau khi gửi"), accepted(dedup=True)]
    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=succeeded_copying(sample_mp4, post))
    with pytest.raises(TransientError):
        await adapter.generate(req, run_ctx)
    # "khởi động lại": cùng job (external_state giữ nguyên), chạy lại handler
    result = await adapter.generate(req, run_ctx)
    first, second = (body_of(c) for c in post.calls)
    assert first["clientRef"] == second["clientRef"] == "proj7:scene03"
    assert run_ctx.external_id == "job_1"
    assert result.meta["deduplicated"] is True


@respx.mock
async def test_resume_with_external_id_polls_without_post(adapter, run_ctx, req, sample_mp4):
    run_ctx.external_id = "job_9"
    run_ctx.external = {"state": "submitted", "client_ref": "proj7:scene03"}
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted("job_other"))
    req.out_path.parent.mkdir(parents=True)
    shutil.copyfile(sample_mp4, req.out_path)
    respx.get(f"{BASE}/grok/jobs/job_9").mock(return_value=httpx.Response(200, json={
        "jobId": "job_9", "status": "succeeded", "progress": 1, "result": {"filePath": str(req.out_path)}}))
    result = await adapter.generate(req, run_ctx)
    assert not post.called
    assert result.path == req.out_path


@respx.mock
async def test_resume_finds_job_cancelled_while_away_resubmits_same_ref(adapter, run_ctx, req, sample_mp4):
    """Tắt app (handler bị huỷ → DELETE) hoặc vắng > 5 phút (GrokTele tự huỷ job không ai hỏi): khi nối lại thấy
    `cancelled` mà người dùng không huỷ → gửi lại một lần cùng clientRef (GrokTele bỏ qua job đã huỷ khi chống
    trùng)."""
    run_ctx.external_id = "job_9"
    run_ctx.external = {"state": "submitted", "client_ref": "proj7:scene03"}
    respx.get(f"{BASE}/grok/jobs/job_9").mock(return_value=httpx.Response(200, json={
        "jobId": "job_9", "status": "cancelled", "error": {"kind": "cancelled", "message": "Đã huỷ"}}))
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted("job_10"))
    respx.get(f"{BASE}/grok/jobs/job_10").mock(side_effect=succeeded_copying(sample_mp4, post, "job_10"))
    result = await adapter.generate(req, run_ctx)
    assert body_of(post.calls.last)["clientRef"] == "proj7:scene03"
    assert run_ctx.external_id == "job_10"
    assert result.path.is_file()


@respx.mock
async def test_job_cancelled_on_provider_during_poll_fails(adapter, run_ctx, req):
    """Job vừa gửi trong lần chạy này bị huỷ phía GrokTele (vd. 08.cancel-all.bat) → thất bại, không tự gửi lại."""
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    respx.get(f"{BASE}/grok/jobs/job_1").mock(return_value=httpx.Response(200, json={
        "jobId": "job_1", "status": "cancelled", "error": {"kind": "cancelled", "message": "Đã huỷ"}}))
    with pytest.raises(ProviderJobError) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "provider_cancelled"
    assert post.call_count == 1


@respx.mock
async def test_file_elsewhere_is_copied_to_out_path(adapter, run_ctx, req, sample_mp4, tmp_path):
    """filePath khác outputPath (job cũ trùng clientRef): chép về out_path, file gốc giữ nguyên."""
    elsewhere = tmp_path / "other_job" / "video.mp4"
    elsewhere.parent.mkdir()
    shutil.copyfile(sample_mp4, elsewhere)
    respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted(dedup=True))
    respx.get(f"{BASE}/grok/jobs/job_1").mock(return_value=httpx.Response(200, json={
        "jobId": "job_1", "status": "succeeded", "progress": 1, "result": {"filePath": str(elsewhere)}}))
    result = await adapter.generate(req, run_ctx)
    assert result.path == req.out_path.resolve() and result.path.is_file()
    assert elsewhere.is_file()


@respx.mock
async def test_dedup_to_finished_job_with_missing_file_resubmits_with_new_ref(adapter, run_ctx, req, sample_mp4):
    """clientRef trả lại job cũ đã xong nhưng file đã bị chuyển đi (vd. chạy lại job) → gửi lại bằng ref mới."""
    post = respx.post(f"{BASE}/grok/jobs")
    post.side_effect = [accepted("job_old", dedup=True), accepted("job_new")]
    respx.get(f"{BASE}/grok/jobs/job_old").mock(return_value=httpx.Response(200, json={
        "jobId": "job_old", "status": "succeeded", "progress": 1,
        "result": {"filePath": str(req.out_path.resolve())}}))
    respx.get(f"{BASE}/grok/jobs/job_new").mock(side_effect=succeeded_copying(sample_mp4, post, "job_new"))
    result = await adapter.generate(req, run_ctx)
    refs = [body_of(c)["clientRef"] for c in post.calls]
    assert refs[0] == "proj7:scene03"
    assert refs[1].startswith("proj7:scene03:") and refs[1] != refs[0]
    assert run_ctx.external_id == "job_new" and run_ctx.external["client_ref"] == refs[1]
    assert result.path.is_file()


# --- Poll: lỗi, huỷ -----------------------------------------------------------------------------------------------


@respx.mock
async def test_failed_job_maps_kind_and_hint(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    respx.get(f"{BASE}/grok/jobs/job_1").mock(return_value=httpx.Response(200, json={
        "jobId": "job_1", "status": "failed", "progress": 0.3,
        "error": {"kind": "moderated", "message": "Bot từ chối nội dung"}}))
    with pytest.raises(ProviderJobError) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "provider_moderated"
    assert ei.value.hint == "Grok kiểm duyệt nội dung — đổi ảnh/prompt, gửi lại y nguyên không giúp được."


@respx.mock
async def test_succeeded_without_file_is_missing_file(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    respx.get(f"{BASE}/grok/jobs/job_1").mock(return_value=httpx.Response(200, json={
        "jobId": "job_1", "status": "succeeded", "progress": 1, "result": {"filePath": str(req.out_path)}}))
    with pytest.raises(ProviderJobError) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "provider_missing_file"


@respx.mock
async def test_tiny_file_is_missing_file(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    req.out_path.parent.mkdir(parents=True)
    req.out_path.write_bytes(b"\x00" * 1000)
    respx.get(f"{BASE}/grok/jobs/job_1").mock(return_value=httpx.Response(200, json={
        "jobId": "job_1", "status": "succeeded", "progress": 1, "result": {"filePath": str(req.out_path)}}))
    with pytest.raises(ProviderJobError) as ei:
        await adapter.generate(req, run_ctx)
    assert ei.value.code == "provider_missing_file"


@respx.mock
async def test_30_poll_network_errors_is_transient_and_keeps_job(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    get = respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TransientError):
        await adapter.generate(req, run_ctx)
    assert get.call_count == 30
    assert run_ctx.external_id == "job_1"


@respx.mock
async def test_poll_recovers_after_some_network_errors(adapter, run_ctx, req, sample_mp4):
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    finish = succeeded_copying(sample_mp4, post)
    errors = iter(range(29))

    def effect(request):
        if next(errors, None) is not None:
            raise httpx.ConnectError("refused")
        return finish(request)

    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=effect)
    result = await adapter.generate(req, run_ctx)
    assert result.path.is_file()


@respx.mock
async def test_cancel_sends_delete(adapter, run_ctx, req):
    respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())

    def effect(request):
        run_ctx.cancelled.set()  # người dùng bấm huỷ khi job đang chạy
        return running()

    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=effect)
    delete = respx.delete(f"{BASE}/grok/jobs/job_1").mock(return_value=httpx.Response(200, json={"ok": True}))
    with pytest.raises(asyncio.CancelledError):
        await adapter.generate(req, run_ctx)
    assert delete.called
    assert delete.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"


@respx.mock
async def test_cancel_method_sends_delete(adapter):
    delete = respx.delete(f"{BASE}/grok/jobs/job_5").mock(return_value=httpx.Response(200, json={"ok": True}))
    await adapter.cancel("job_5")
    assert delete.called


async def test_try_video_job_end_to_end(client, sample_mp4):
    """Qua op `provider.try_video` + worker thật: dấu submitting/jobId được lưu bền, video vào kho media kèm meta."""
    import anyio
    from sqlalchemy import select

    from sagareel.db.models import Job, Media

    def call(name: str, payload: dict):
        r = client.post(f"/api/ops/{name}", json=payload)
        assert r.status_code == 200, r.text
        return r.json()

    def wait(job_id: int) -> dict:
        body = call("job.wait", {"ids": [job_id], "timeout_s": 60})
        assert not body["timed_out"], body
        return body["items"][0]

    call("provider.create", {"kind": "image", "adapter": "fake_image", "name": "fake-img"})
    img_job = await anyio.to_thread.run_sync(
        wait, call("provider.try_image", {"connection": "fake-img", "prompt": "nền", "aspect": "9:16"})["job_id"])
    image_id = img_job["result"]["media_ids"][0]
    call("provider.create", {"kind": "video", "adapter": "multikit", "name": "grok", "base_url": BASE,
                             "api_key": KEY, "options": {"poll_interval": 0}})

    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        post = mock.post("/grok/jobs").mock(return_value=accepted("gt_77"))
        mock.get("/grok/jobs/gt_77").mock(side_effect=succeeded_copying(sample_mp4, post, "gt_77"))
        job_id = call("provider.try_video", {"connection": "grok", "image_media_id": image_id,
                                             "prompt": " đi bộ ", "duration": 6, "resolution": "720p"})["job_id"]
        job = await anyio.to_thread.run_sync(wait, job_id)

    assert job["status"] == "succeeded", job
    body = body_of(post.calls.last)
    assert body["prompt"] == " đi bộ " and body["clientRef"] == f"sagareel:job:{job_id}"
    services = client.app.state.services
    with services.db.session() as s:
        row = s.get(Job, job_id)
        assert row.external_id == "gt_77"
        assert row.external_state == {"state": "submitted", "client_ref": f"sagareel:job:{job_id}"}
        m = s.scalars(select(Media).where(Media.id == job["result"]["media_ids"][0])).one()
        assert m.kind == "video" and m.meta["provider_meta"]["has_audio"] is True


@respx.mock
async def test_generate_without_run(adapter, req, sample_mp4):
    """Gọi trực tiếp (run=None): vẫn có clientRef, không cần ghi trạng thái."""
    post = respx.post(f"{BASE}/grok/jobs").mock(return_value=accepted())
    respx.get(f"{BASE}/grok/jobs/job_1").mock(side_effect=succeeded_copying(sample_mp4, post))
    result = await adapter.generate(req, None)
    assert body_of(post.calls.last)["clientRef"].startswith("sagareel:")
    assert result.meta["has_audio"] is True
