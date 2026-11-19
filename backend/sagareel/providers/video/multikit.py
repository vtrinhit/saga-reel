"""`multikit`: Grok ảnh → video qua HTTP API cục bộ của GrokTele (mặc định `http://127.0.0.1:8888`) hoặc MultiKit.

Hai app cùng một API (header `Authorization: Bearer <key>`, bỏ qua nếu khoá rỗng; `/health` không cần khoá):

    GET    /health          -> {ok}
    GET    /grok/accounts   -> {accounts[{label, status, reason, telegramUser…}], summary{total, usable}}
    POST   /grok/jobs       -> 202 {jobId, status, deduplicated}; 409 no_account_available; 507 disk_full
    GET    /grok/jobs/{id}  -> {status, stage, progress 0–1, result{filePath}, error{kind, message}}
    DELETE /grok/jobs/{id}  -> huỷ

Job nhận bất đồng bộ (202) và là hành động tốn lượt, nên trình tự trong `generate`:
1. `external_id` có sẵn (lần chạy trước đã được nhận) → poll luôn, không POST.
2. Ngược lại ghi dấu `state="submitting"` (kèm `client_ref`) → POST → 202 thì lưu `jobId` ngay. `clientRef` ổn định
   (`run.client_ref`, không có thì `sagareel:job:<id>`) nên POST lại sau khi sập giữa chừng chỉ nhận lại job cũ
   (`deduplicated: true`) chứ không tạo job mới. GrokTele **chắc chắn không nhận** (từ chối kết nối, 4xx, 409, 507,
   401/403, cổng 502/503/…) → xoá dấu rồi mới ném lỗi.
3. Poll tới `succeeded` → file phải tồn tại và > 5 KB, probe để `meta` có thời lượng/kích thước/có tiếng.

GrokTele trả file MP4 H.264 24 fps kèm AAC 48 kHz (và một luồng ảnh bìa `mjpeg`); `480p` bị nâng lên 720p.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import anyio
import httpx

from sagareel.media.ffmpeg import FFmpegError
from sagareel.media.probe import probe
from sagareel.providers.base import (
    CapabilityError,
    ConfigField,
    Health,
    JobUnknown,
    ProviderJobError,
    ProviderUnavailable,
    VideoAdapter,
    VideoRequest,
    VideoResult,
)
from sagareel.providers.registry import register_adapter
from sagareel.services.jobs import FORCE_NEW_HINT, NeedsAttention, TransientError

if TYPE_CHECKING:
    from sagareel.services.worker import JobRun

DEFAULT_URL = "http://127.0.0.1:8888"
DEFAULT_POLL_INTERVAL = 4.0
DEFAULT_TIMEOUT_S = 7200  # GrokTele tự xếp hàng; một job 6 s đo được ~176 s, có thể phải chờ tài khoản rảnh
MIN_VIDEO_BYTES = 5 * 1024
MAX_POLL_NETWORK_ERRORS = 30  # tắt/mở lại GrokTele không mất job, chỉ bỏ cuộc khi app tắt hẳn
CANCEL_TIMEOUT_S = 10.0
JOB_UNKNOWN_CODE = "multikit_job_unknown"

# Cổng/proxy trả các mã này khi request chưa tới được GrokTele → chắc chắn chưa tạo job.
_GATEWAY_STATUSES = {502, 503, 521, 522, 523, 525, 526, 530}

NOT_OPEN = "Chưa mở GrokTele/MultiKit"
OPEN_HINT = "Mở GrokTele (03.start.bat) hoặc MultiKit rồi chạy lại job."
NO_ACCOUNT_HINT = "Mở GrokTele kiểm tra tài khoản (cần đăng nhập Telegram/gia hạn)"
KEY_HINT = "Kiểm tra khoá API của kết nối (GrokTele: biến GROKTELE_API_KEY; MultiKit: Cài đặt › API)."

# Chép từ ReelFB `services/multikit.py` (gợi ý theo `error.kind` của job thất bại).
_ERROR_HINTS = {
    "moderated": "Grok kiểm duyệt nội dung — đổi ảnh/prompt, gửi lại y nguyên không giúp được.",
    "invalid_input": "Đầu vào sai (ảnh hoặc tham số).",
    "no_account_available": "Không tài khoản nào chạy được — mở MultiKit kiểm tra tài khoản.",
    "rate_limited": "Tài khoản bị giới hạn nhiều lần — thử lại sau hoặc thêm tài khoản.",
    "quota_exceeded": "Hết quota trên các tài khoản — thử lại sau hoặc thêm tài khoản.",
    "challenge_required": "Tài khoản cần xác minh — mở trang Tài khoản trong MultiKit.",
    "auth_required": "Tài khoản cần đăng nhập lại — mở trang Tài khoản trong MultiKit.",
}

_PHONE_RE = re.compile(r"\+?\d[\d .\-]{5,}\d")
_USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{3,}")


def _short(text: Any, limit: int = 300) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _scrub(text: Any, limit: int = 120) -> str:
    """Che chuỗi giống số điện thoại và @username Telegram (tên/lý do tài khoản, thông báo lỗi của GrokTele có thể
    chứa chúng) — dùng cho mọi chữ từ GrokTele đi vào health, lỗi job, meta."""
    return _USERNAME_RE.sub("@•••", _PHONE_RE.sub("•••", _short(text, limit)))


def _kind(raw: Any, default: str) -> str:
    """`error.kind` của GrokTele → mã snake_case ổn định cho `provider_<kind>`."""
    k = re.sub(r"[^a-z0-9]+", "_", str(raw or "").lower()).strip("_")
    return k or default


def _error_body(resp: httpx.Response) -> dict:
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        err = None
    return err if isinstance(err, dict) else {"message": _short(resp.text)}


class _RemoteCancelled(ProviderJobError):
    """Job ở trạng thái `cancelled` phía GrokTele mà người dùng không huỷ trong SagaReel."""


def _valid_video(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > MIN_VIDEO_BYTES
    except OSError:
        return False


@register_adapter
class MultiKitVideoAdapter(VideoAdapter):
    adapter_id = "multikit"
    label = "Grok video (GrokTele/MultiKit)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("base_url", "Địa chỉ GrokTele/MultiKit", "url", True, DEFAULT_URL,
                    help="GrokTele mặc định http://127.0.0.1:8888; MultiKit http://127.0.0.1:17890."),
        ConfigField("api_key", "Khoá API", "secret", False,
                    help="Để trống nếu GrokTele/MultiKit không đặt khoá."),
        ConfigField("poll_interval", "Chu kỳ hỏi trạng thái (giây)", "int", False, int(DEFAULT_POLL_INTERVAL)),
        ConfigField("timeout_s", "Thời gian chờ tối đa mỗi lượt (giây)", "int", False, DEFAULT_TIMEOUT_S,
                    help="Quá hạn thì job thử lại sau và tiếp tục chờ đúng job cũ (không gửi lại)."),
    ]
    default_capabilities: ClassVar[dict] = {
        "durations": [6, 10, 15],
        "resolutions": ["480p", "720p", "1080p"],
        "aspects": ["9:16", "16:9"],
        "needs_image": True,
        "has_audio": True,
    }
    http_timeout: ClassVar[float] = 30.0

    # --- Tiện ích ---------------------------------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        key = self.conn.api_key or ""
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _poll_interval(self) -> float:
        try:
            return max(0.0, float(self.conn.options.get("poll_interval", DEFAULT_POLL_INTERVAL)))
        except (TypeError, ValueError):
            return DEFAULT_POLL_INTERVAL

    def _timeout_s(self) -> float:
        try:
            return max(60.0, float(self.conn.options.get("timeout_s", DEFAULT_TIMEOUT_S)))
        except (TypeError, ValueError):
            return float(DEFAULT_TIMEOUT_S)

    def _not_open(self) -> str:
        return f"{NOT_OPEN} (không kết nối được {self.conn.base_url})."

    # --- Sức khoẻ ---------------------------------------------------------------------------------------------------

    async def check_health(self) -> Health:
        if not self.conn.base_url:
            return Health(status="not_configured", detail="Chưa nhập địa chỉ GrokTele/MultiKit.")
        try:
            async with self.client() as c:
                r = await c.get("/health")
                if not r.is_success:
                    return Health(status="dead", detail=f"GrokTele/MultiKit /health trả HTTP {r.status_code}.")
                acc = await c.get("/grok/accounts", headers=self._headers())
        except httpx.HTTPError:
            return Health(status="dead", detail=self._not_open())
        if acc.status_code in (401, 403):
            return Health(status="dead",
                          detail=f"GrokTele/MultiKit từ chối khoá API (HTTP {acc.status_code}). {KEY_HINT}")
        if not acc.is_success:
            return Health(status="dead", detail=f"GrokTele/MultiKit /grok/accounts trả HTTP {acc.status_code}.")
        try:
            data = acc.json()
            summary = data.get("summary") or {}
            usable = int(summary.get("usable") or 0)
            total = int(summary.get("total") or 0)
        except (ValueError, TypeError, AttributeError):
            return Health(status="dead", detail="GrokTele/MultiKit trả danh sách tài khoản không hợp lệ.")
        if usable > 0:
            return Health(status="alive", detail=f"Sẵn sàng: {usable}/{total} tài khoản dùng được.",
                          capabilities=self.capabilities())
        # Chỉ `label: status (reason)` — không bao giờ telegramUser/số điện thoại/session.
        parts = []
        for a in data.get("accounts") or []:
            if not isinstance(a, dict):
                continue
            line = f"{_scrub(a.get('label') or a.get('id') or '?')}: {_scrub(a.get('status') or '?')}"
            if a.get("reason"):
                line += f" ({_scrub(a['reason'])})"
            parts.append(line)
        listing = "; ".join(parts) if parts else "chưa có tài khoản nào"
        return Health(status="dead", detail=f"Không tài khoản nào dùng được — {listing}. {NO_ACCOUNT_HINT}.")

    async def estimate(self, req: VideoRequest) -> dict:
        return {"units": 1, "seconds": req.duration_s}

    # --- Tạo video --------------------------------------------------------------------------------------------------

    def _check_configured(self) -> None:
        if not self.conn.base_url:
            raise ProviderUnavailable("Chưa nhập địa chỉ GrokTele/MultiKit.",
                                      hint="Nhập địa chỉ trong Cài đặt → Provider.")

    def _check_request(self, req: VideoRequest) -> None:
        """Kiểm tra trước mỗi lần gửi (không cần khi chỉ poll job đã được nhận — ảnh đầu vào có thể đã bị xoá)."""
        caps = self.capabilities()
        if req.duration_s not in caps["durations"]:
            raise CapabilityError(f"Thời lượng {req.duration_s} s không được hỗ trợ.",
                                  hint=f"Chọn một trong {caps['durations']} giây.")
        if req.resolution not in caps["resolutions"]:
            raise CapabilityError(f"Độ phân giải {req.resolution} không được hỗ trợ.",
                                  hint=f"Chọn một trong {caps['resolutions']}.")
        self._aspect_sent(req.aspect)
        if not Path(req.image).is_file():
            raise ProviderJobError("invalid_input", f"Không tìm thấy ảnh đầu vào {Path(req.image).name}.",
                                   hint="Chọn lại ảnh cho cảnh này.")

    def _aspect_sent(self, aspect: str) -> str:
        """Tỉ lệ gửi đi. Grok chỉ có nút Dọc 9:16 / Ngang 16:9 (GrokTele cũng tự quy đổi 1:1, 4:5, 3:4… như vậy):
        tỉ lệ khác được quy về nút gần nhất — dọc hoặc vuông → 9:16, ngang → 16:9."""
        caps = self.capabilities()
        if aspect in caps["aspects"]:
            return aspect
        try:
            w_s, h_s = aspect.split(":")
            w, h = float(w_s), float(h_s)
            if w <= 0 or h <= 0:
                raise ValueError
        except ValueError:
            raise CapabilityError(f"Tỉ lệ khung {aspect!r} không hợp lệ.",
                                  hint=f"Chọn một trong {caps['aspects']}.") from None
        return "9:16" if w <= h else "16:9"

    def _payload(self, req: VideoRequest, client_ref: str) -> dict:
        return {
            "imagePath": str(Path(req.image).resolve()),
            "prompt": req.prompt,  # nguyên văn
            "aspectRatio": self._aspect_sent(req.aspect),
            "duration": int(req.duration_s),
            "resolution": req.resolution,
            "outputPath": str(Path(req.out_path).resolve()),
            "clientRef": client_ref,
            "priority": 0,
        }

    async def generate(self, req: VideoRequest, run: JobRun | None = None) -> VideoResult:
        self._check_configured()
        out = Path(req.out_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        external = dict(run.external) if run is not None else {}
        # Chưa có trạng thái provider nào (ngoài thế hệ `gen`): lần gửi đầu (hoặc sau job.retry / clear_attention).
        fresh = not {k: v for k, v in external.items() if k != "gen"}
        ref = str(external.get("client_ref") or self._base_ref(run))
        # Chỉ poll thẳng khi job đã được nhận (`external_id`) và lần gửi gần nhất không dang dở/bị từ chối. Dấu
        # `submitting` còn đó (vd. POST với ref mới hết giờ) hoặc dấu đã xoá (`state` = None: GrokTele chắc chắn không
        # nhận lần gửi gần nhất) → `external_id` có thể là job CŨ: gửi lại bằng `client_ref` đã lưu (GrokTele chống
        # trùng theo clientRef) rồi lưu jobId mới.
        pending = "state" in external and external["state"] in ("submitting", None)
        resumed = run is not None and bool(run.external_id) and not pending
        if resumed:
            job_id = str(run.external_id)
            deduped = False
            _log(run, "info", f"GrokTele: nối lại job {job_id}.")
        else:
            job_id, deduped = await self._submit(req, ref, run)

        async with self.client() as c:
            try:
                job = await self._wait(c, job_id, run)
            except _RemoteCancelled as e:
                if not resumed:
                    raise
                # Bị huỷ phía GrokTele khi SagaReel không chạy → người dùng quyết định, không tự gửi lại (tốn lượt).
                raise NeedsAttention(
                    "remote_cancelled",
                    f"Job GrokTele {job_id} đã bị huỷ phía GrokTele trong lúc SagaReel không chạy ({e.msg}).",
                    hint="Job bị huỷ phía GrokTele; dùng job.clear_attention (gửi lại) để tạo job mới, "
                         "hoặc cho thất bại.",
                ) from None
            produced = Path(str((job.get("result") or {}).get("filePath") or out))
            if not produced.exists() and deduped and fresh:
                # Lần gửi đầu mà clientRef trả lại một job cũ đã xong nhưng file không còn (vd. chạy lại job sau khi
                # file đã vào kho media) → gửi lại MỘT lần bằng ref mới (ghi bền trước khi POST như lần đầu).
                _log(run, "warning", f"GrokTele: job cũ {job_id} báo xong nhưng file không còn — gửi job mới.")
                ref = f"{self._base_ref(run)}:{uuid.uuid4().hex[:8]}"
                job_id, deduped = await self._submit(req, ref, run)
                job = await self._wait(c, job_id, run)
                produced = Path(str((job.get("result") or {}).get("filePath") or out))
            if _valid_video(produced):
                if not (out.exists() and os.path.samefile(produced, out)):
                    # File nằm ngoài chỗ ta yêu cầu (vd. job cũ trùng clientRef): chép về `out_path` — người gọi
                    # sẽ chuyển file vào kho media, không được chuyển file của nơi khác đi.
                    await anyio.to_thread.run_sync(shutil.copyfile, produced, out)
                    produced = out
                return await self._result(produced, job, job_id=job_id, ref=ref, deduped=deduped, req=req)
        raise ProviderJobError(
            "missing_file",
            f"GrokTele báo xong job {job_id} nhưng không có file video hợp lệ (cần tồn tại và > 5 KB) tại {produced}.",
            hint="Xem log của GrokTele/MultiKit. " + FORCE_NEW_HINT,
        )

    @staticmethod
    def _base_ref(run: JobRun | None) -> str:
        """clientRef của lần gửi: thế hệ `gen` (tăng khi force_new / clear_attention resubmit) thêm hậu tố `:g<gen>` để
        GrokTele không gộp lần gửi mới về job cũ cùng clientRef."""
        if run is None:
            return f"sagareel:direct:{uuid.uuid4().hex}"
        base = run.client_ref or f"sagareel:job:{run.job_id}"
        try:
            gen = int((run.external or {}).get("gen") or 0)
        except (TypeError, ValueError):
            gen = 0
        return f"{base}:g{gen}" if gen > 0 else base

    async def _clear_marker(self, run: JobRun | None) -> None:
        if run is not None:
            await run.save_external(None, state=None)

    async def _submit(self, req: VideoRequest, ref: str, run: JobRun | None) -> tuple[str, bool]:
        """POST /grok/jobs với dấu `submitting` bền trước khi gửi; trả (jobId, deduplicated)."""
        self._check_request(req)
        payload = self._payload(req, ref)
        if run is not None:
            await run.save_external(None, state="submitting", client_ref=ref)
        try:
            async with self.client() as c:
                resp = await c.post("/grok/jobs", json=payload, headers=self._headers())
        except httpx.ConnectError as e:  # chưa gửi được byte nào → GrokTele chắc chắn chưa nhận
            await self._clear_marker(run)
            raise ProviderUnavailable(self._not_open(), hint=OPEN_HINT) from e
        except httpx.HTTPError as e:
            # Có thể đã gửi xong: giữ dấu; clientRef giúp lần gửi lại chỉ nhận lại job cũ.
            raise TransientError(f"Mất kết nối GrokTele khi gửi job ({type(e).__name__}).", retry_after=30) from e

        status = resp.status_code
        if status in (200, 201, 202):
            try:
                data = resp.json()
            except ValueError:
                data = {}
            job_id = str((data or {}).get("jobId") or "")
            if not job_id:
                raise JobUnknown(
                    "GrokTele nhận yêu cầu nhưng không trả jobId — không rõ job đã được tạo chưa.",
                    hint="Mở GrokTele xem danh sách job; gửi lại sẽ dùng cùng clientRef nên không tạo trùng.",
                    code=JOB_UNKNOWN_CODE,
                )
            deduped = bool(data.get("deduplicated"))
            if run is not None:
                await run.save_external(job_id, state="submitted", client_ref=ref)
            _log(run, "info", f"GrokTele: {'nối lại' if deduped else 'đã tạo'} job {job_id} "
                              f"({payload['duration']} s {payload['resolution']} {payload['aspectRatio']}).")
            return job_id, deduped

        if status in _GATEWAY_STATUSES:
            await self._clear_marker(run)
            raise TransientError(f"GrokTele/MultiKit chưa sẵn sàng (HTTP {status}).", retry_after=30)
        if status >= 500 and status != 507:  # lỗi nội bộ: có thể đã tạo job → giữ dấu (clientRef chống trùng)
            raise TransientError(f"GrokTele lỗi nội bộ khi tạo job (HTTP {status}).", retry_after=30)

        # 4xx / 507 (kiểm tra ổ đĩa trước khi tạo job): GrokTele từ chối → chắc chắn không tạo job.
        await self._clear_marker(run)
        err = _error_body(resp)
        details = err.get("details") if isinstance(err.get("details"), dict) else {}
        reason = str(details.get("reason") or err.get("code") or "")
        message = _scrub(err.get("message"), 300)
        if status in (401, 403):
            raise ProviderUnavailable(f"GrokTele/MultiKit từ chối khoá API (HTTP {status}).", hint=KEY_HINT)
        if status == 409 and reason == "no_account_available":
            raise ProviderUnavailable(f"GrokTele không còn tài khoản nào chạy được: {message}", hint=NO_ACCOUNT_HINT)
        if status == 429:
            raise TransientError(f"GrokTele đang giới hạn yêu cầu (HTTP 429): {message}", retry_after=60)
        if status == 507 or reason == "disk_full":
            raise ProviderJobError("disk_full", f"Ổ đĩa lưu video của GrokTele sắp đầy: {message}",
                                   hint="Giải phóng ổ đĩa chứa thư mục xuất video rồi chạy lại job.")
        kind = _kind(reason, "rejected")
        raise ProviderJobError(kind, f"GrokTele từ chối tạo job (HTTP {status}): {message}",
                               hint=_ERROR_HINTS.get(kind, "Kiểm tra ảnh, prompt và tham số rồi chạy lại."))

    async def _wait(self, c: httpx.AsyncClient, job_id: str, run: JobRun | None) -> dict:
        """Poll tới `succeeded`; ném lỗi chuẩn khi thất bại/huỷ/mất kết nối lâu/quá hạn."""
        interval = self._poll_interval()
        deadline = time.monotonic() + self._timeout_s()
        network_errors = 0
        last_stage = None
        while True:
            if run is not None and run.cancelled.is_set():
                await self._cancel_on_provider(job_id)
                raise asyncio.CancelledError()
            job = None
            try:
                r = await c.get(f"/grok/jobs/{job_id}", headers=self._headers())
            except httpx.HTTPError as e:
                r = None
                network_errors += 1
                if network_errors == 1:
                    _log(run, "warning", f"GrokTele: mất kết nối khi chờ job {job_id}, thử lại… ({type(e).__name__})")
            if r is not None:
                if r.status_code in _GATEWAY_STATUSES:
                    network_errors += 1
                elif r.status_code in (401, 403):
                    raise ProviderUnavailable(f"GrokTele/MultiKit từ chối khoá API (HTTP {r.status_code}).",
                                              hint=KEY_HINT)
                elif r.status_code == 404:
                    raise ProviderJobError("not_found", f"GrokTele không còn job {job_id}.",
                                           hint=FORCE_NEW_HINT)
                elif not r.is_success:
                    network_errors += 1
                else:
                    network_errors = 0
                    try:
                        job = r.json()
                    except ValueError:
                        job = None
            if network_errors >= MAX_POLL_NETWORK_ERRORS:
                raise TransientError(
                    f"Mất kết nối GrokTele {network_errors} lần liên tiếp khi chờ job {job_id}.", retry_after=60)

            if isinstance(job, dict):
                status = str(job.get("status") or "")
                if status == "succeeded":
                    return job
                if status in ("failed", "cancelled", "canceled"):
                    if run is not None and run.cancelled.is_set():
                        raise asyncio.CancelledError()
                    raise self._job_error(job_id, job)
                progress = job.get("progress")
                stage = job.get("stage") or status
                if run is not None:
                    pct = round(float(progress) * 100, 1) if isinstance(progress, (int, float)) else None
                    run.progress(pct, stage=f"Grok đang tạo video ({stage})")
                if stage != last_stage:
                    last_stage = stage
                    _log(run, "info", f"GrokTele: job {job_id} {status}/{stage}")

            if time.monotonic() > deadline:
                raise TransientError(f"GrokTele chưa xong job {job_id} sau {self._timeout_s():.0f} s; sẽ chờ tiếp.")
            await self._sleep(interval, run)

    @staticmethod
    async def _sleep(interval: float, run: JobRun | None) -> None:
        if run is None or interval <= 0:
            await asyncio.sleep(interval)
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run.cancelled.wait(), timeout=interval)

    @staticmethod
    def _job_error(job_id: str, job: dict) -> ProviderJobError:
        err = job.get("error") if isinstance(job.get("error"), dict) else {}
        status = str(job.get("status") or "failed")
        kind = _kind(err.get("kind") or err.get("code"), "cancelled" if status.startswith("cancel") else "failed")
        message = _scrub(err.get("message"), 300) or f"trạng thái {status}"
        cls = _RemoteCancelled if status.startswith("cancel") else ProviderJobError
        return cls(
            kind, f"Job GrokTele {job_id} thất bại [{kind}]: {message}",
            hint=_ERROR_HINTS.get(kind, "Xem log của GrokTele/MultiKit.") + " " + FORCE_NEW_HINT,
        )

    async def _result(self, path: Path, job: dict, *, job_id: str, ref: str, deduped: bool,
                      req: VideoRequest) -> VideoResult:
        try:
            info = await anyio.to_thread.run_sync(probe, path)
        except FFmpegError as e:
            raise ProviderJobError("invalid_file", f"File video GrokTele trả về không đọc được: {path.name}.",
                                   hint=FORCE_NEW_HINT) from e
        try:
            aspect_sent = self._aspect_sent(req.aspect)
        except CapabilityError:  # chỉ để ghi meta; job đã xong
            aspect_sent = None
        return VideoResult(path=path, meta={
            "adapter": self.adapter_id,
            "job_id": job_id,
            "client_ref": ref,
            "deduplicated": deduped,
            "account": _scrub(job["accountLabel"]) if job.get("accountLabel") else None,
            "duration_ms": info.duration_ms,
            "duration_s": round(info.duration_ms / 1000, 3),
            "size": info.size,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "vcodec": info.vcodec,
            "acodec": info.acodec,
            "has_audio": info.has_audio,
            "aspect_sent": aspect_sent,
            "requested": {"duration_s": req.duration_s, "resolution": req.resolution, "aspect": req.aspect},
        })

    # --- Huỷ --------------------------------------------------------------------------------------------------------

    async def _cancel_on_provider(self, job_id: str) -> None:
        """Huỷ phía GrokTele để không tốn lượt cho clip bỏ đi; lỗi bỏ qua (không che việc huỷ)."""
        with contextlib.suppress(Exception):
            await asyncio.shield(asyncio.wait_for(self.cancel(job_id), CANCEL_TIMEOUT_S))

    async def cancel(self, external_id: str) -> None:
        async with self.client() as c:
            await c.delete(f"/grok/jobs/{external_id}", headers=self._headers())


def _log(run: JobRun | None, level: str, msg: str) -> None:
    if run is not None:
        run.log(level, msg)
