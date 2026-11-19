"""`maivideo`: tạo ảnh qua MaiVideo (PixVerse Image API) — provider ảnh mặc định, **tính tiền mỗi job được nhận**.

Luồng: `POST {base}/api/v1/image-generation` → `job_id` → poll `GET .../{job_id}` (404 → tra
`GET .../status?job_ids=<id>`) → `completed|partial` → tải `images[].file_url` (tương đối → ghép base, kèm
`X-API-Key`) vào `out_dir/<uuid>.<đuôi>`.

Chống tạo trùng (xem `_paid.py`): dấu `state="submitting"` + `fingerprint` bền trước POST; nối lại có `external_id` →
chỉ poll; có dấu mà không có `external_id` → `JobUnknown("maivideo_job_unknown")`. Poll quá `options.timeout_s`
(mặc định 1800 s — MaiVideo có thể xếp hàng > 10 phút mà vẫn tính tiền) → `TransientError` **giữ** `external_id`.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import anyio
import httpx

from sagareel.providers.base import (
    CapabilityError,
    ConfigField,
    Health,
    ImageAdapter,
    ImageRequest,
    ImageResult,
    JobUnknown,
    ProviderJobError,
    ProviderUnavailable,
)
from sagareel.providers.image import _paid
from sagareel.providers.registry import register_adapter
from sagareel.services.jobs import JobStateLost, TransientError

DEFAULT_BASE_URL = "https://maivideo.top"
DEFAULT_MODEL = "gpt-image-2.0"
DEFAULT_DETAIL = "medium"
DEFAULT_POLL_INTERVAL_S = 10
DEFAULT_TIMEOUT_S = 1800
MAX_REFS = 9
MISSING_POLLS_LIMIT = 3  # số lần liên tiếp MaiVideo xác nhận "không có job này" trước khi dừng hỏi người dùng
MIN_IMAGE_BYTES = 1024
RESOLUTION_TIERS = {"1K": "1080p", "2K": "1440p", "4K": "2160p"}
DETAIL_LEVELS = ("low", "medium", "high")
DETAIL_LEVELS_GPT25 = (*DETAIL_LEVELS, "xhigh", "max")
DONE_STATUSES = ("completed", "partial")
FAILED_STATUSES = ("error", "failed")
CATALOG_KEYS = ("models", "model_resolutions", "model_aspect_ratios", "model_detail_levels", "max_reference_images",
                "max_reference_images_by_model", "max_prompt_length", "quota")

# MaiVideo chỉ trả lời POST sau khi đã nhận job; lúc tải nặng mất hơn 60 s → chờ đọc lâu cho POST.
SUBMIT_TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=120.0, pool=30.0)
POLL_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)
HEALTH_TIMEOUT = httpx.Timeout(15.0)

UNKNOWN_HINT = (
    "Kiểm tra lịch sử job trên MaiVideo trước: nếu ảnh đã được tạo thì lấy ảnh đó và gọi "
    "job.clear_attention(action=fail); nếu chưa có thì gọi job.clear_attention(action=resubmit) để gửi lại "
    "(có thể tốn thêm một lượt)."
)
_NOT_FOUND = object()  # MaiVideo xác nhận không có job này


class _Unconfirmed(Exception):
    """Chưa xác nhận được trạng thái job lần này (HTTP lạ, JSON hỏng); vòng poll thử lại."""


@register_adapter
class MaiVideoImageAdapter(ImageAdapter):
    adapter_id = "maivideo"
    label = "MaiVideo (tạo ảnh)"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("base_url", "Địa chỉ máy chủ", "url", True, DEFAULT_BASE_URL),
        ConfigField("api_key", "Khoá API (X-API-Key)", "secret", True),
        ConfigField("default_model", "Model mặc định", "str", False, DEFAULT_MODEL),
        ConfigField("detail_level", "Mức chi tiết mặc định", "enum", False, DEFAULT_DETAIL,
                    choices=list(DETAIL_LEVELS_GPT25),
                    help="xhigh và max chỉ dùng được với gpt-image-2.5; model khác tự về medium."),
        ConfigField("poll_interval", "Chu kỳ kiểm tra job (giây)", "int", False, DEFAULT_POLL_INTERVAL_S),
        ConfigField("timeout_s", "Thời gian chờ tối đa mỗi lượt (giây)", "int", False, DEFAULT_TIMEOUT_S,
                    help="Quá hạn: job thử lại sau và chỉ kiểm tra tiếp job cũ, không gửi lại (không tốn thêm)."),
        ConfigField("max_reference_images", "Số ảnh tham chiếu tối đa", "int", False, MAX_REFS),
    ]
    default_capabilities: ClassVar[dict] = {
        "aspects": ["9:16", "16:9", "1:1", "4:5", "3:4"],
        "resolutions": ["1080p", "1440p", "2160p"],
        "models": [DEFAULT_MODEL, "gpt-image-2.5"],
        "max_reference_images": MAX_REFS,
    }

    # --- Tiện ích ------------------------------------------------------------------------------------------------

    @property
    def base(self) -> str:
        return (self.conn.base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL

    @property
    def key(self) -> str:
        return (self.conn.api_key or "").strip()

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.key}

    def _opt(self, name: str, default: float) -> float:
        try:
            return max(0.0, float(self.conn.options.get(name, default)))
        except (TypeError, ValueError):
            return float(default)

    def _payload(self, req: ImageRequest) -> dict[str, Any]:
        model = req.model or self.conn.default_model or DEFAULT_MODEL
        resolution = RESOLUTION_TIERS.get((req.resolution or "").upper(), req.resolution) or "1440p"
        detail = (req.quality or self.conn.options.get("detail_level") or DEFAULT_DETAIL).lower()
        allowed = DETAIL_LEVELS_GPT25 if model.lower().startswith("gpt-image-2.5") else DETAIL_LEVELS
        if detail not in allowed:
            detail = DEFAULT_DETAIL
        return {
            "prompt": req.prompt,  # nguyên văn
            "model": model,
            "resolution": resolution,
            "detail_level": detail,
            "aspect_ratio": req.aspect,
            "n": 1,
        }

    # --- Năng lực, ước tính, sức khoẻ ----------------------------------------------------------------------------

    async def estimate(self, req: ImageRequest) -> dict:
        return {"units": 1, "kind": "image"}

    async def check_health(self) -> Health:
        if not self.key:
            return Health(status="not_configured", detail="Chưa nhập khoá API MaiVideo.")
        try:
            async with self.client() as c:
                r = await c.get(f"{self.base}/api/v1/image-generation/models", headers=self._headers(),
                                timeout=HEALTH_TIMEOUT)
        except httpx.HTTPError as e:
            return Health(status="dead", detail=f"Không kết nối được MaiVideo ({type(e).__name__}).")
        if r.status_code in (401, 403):
            return Health(status="dead", detail=f"MaiVideo từ chối khoá API (HTTP {r.status_code}).")
        if r.status_code != 200:
            return Health(status=_paid.health_status_for(r.status_code),
                          detail=f"MaiVideo trả lỗi HTTP {r.status_code}: {_paid.error_detail(r, self.key)[:200]}")
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return Health(status="dead", detail="MaiVideo trả danh mục model không hợp lệ.")
        caps = {k: data[k] for k in CATALOG_KEYS if k in data}
        caps.setdefault("max_reference_images", self.capabilities()["max_reference_images"])
        detail = "MaiVideo sẵn sàng."
        quota = data.get("quota")
        if isinstance(quota, dict):
            # Hạn mức tháng có thể về 0 trong khi tín dụng trọn đời vẫn còn — vẫn dùng được, không phải "dead".
            parts = [f"{k} = {quota[k]}" for k in ("remaining", "available") if k in quota]
            if parts:
                detail += " Hạn mức: " + ", ".join(parts) + "."
        return Health(status="alive", detail=detail, capabilities=caps)

    # --- Tạo ảnh -------------------------------------------------------------------------------------------------

    async def generate(self, req: ImageRequest, run=None) -> ImageResult:
        if not self.key:
            raise ProviderUnavailable("Chưa nhập khoá API MaiVideo.",
                                      hint="Nhập khoá API trong Cài đặt → Provider rồi chạy lại.")
        limit = int(self.capabilities().get("max_reference_images") or MAX_REFS)
        if len(req.refs) > limit:
            raise CapabilityError(f"MaiVideo nhận tối đa {limit} ảnh tham chiếu (yêu cầu có {len(req.refs)}).",
                                  hint=f"Bớt ảnh tham chiếu xuống còn {limit}.")
        paid = _paid.PaidPost(run, provider="MaiVideo", code="maivideo_job_unknown", hint=UNKNOWN_HINT,
                              secret=self.key)
        payload = self._payload(req)
        job_id = run.external_id if run is not None else None
        async with self.client() as c:
            if job_id:
                self._log(run, "info", f"Tiếp tục theo dõi job MaiVideo {job_id} (không gửi lại).")
            else:
                paid.check_resume()
                job_id = await self._submit(c, paid, req, payload, run)
            data = await self._poll(c, job_id, run)
            path, file_url = await self._download(c, job_id, data, req)
        self._log(run, "info", f"Đã tải ảnh của job MaiVideo {job_id}.")
        if run is not None:
            run.progress(100.0, stage="Đã tải ảnh MaiVideo")
        meta = {k: v for k, v in payload.items() if k != "prompt"}
        return ImageResult(paths=[path], meta={
            "adapter": self.adapter_id, "job_id": job_id, "status": str(data.get("status") or "").lower(),
            "prompt_received": req.prompt, "refs": len(req.refs), "file_url": file_url, **meta,
        })

    @staticmethod
    def _log(run: Any, level: str, msg: str) -> None:
        if run is not None:
            run.log(level, msg)

    async def _submit(self, c: httpx.AsyncClient, paid: _paid.PaidPost, req: ImageRequest, payload: dict,
                      run: Any) -> str:
        refs = await anyio.to_thread.run_sync(_paid.read_refs, list(req.refs))
        fp = _paid.fingerprint(payload, [data for _, data in refs])
        body = dict(payload)
        if refs:
            body["reference_image_urls"] = [_paid.data_url(p, data) for p, data in refs]
        if run is not None:
            run.progress(0.0, stage="Đang gửi yêu cầu tới MaiVideo")
        resp = await paid.send(fp, lambda: c.post(f"{self.base}/api/v1/image-generation", json=body,
                                                  headers=self._headers(), timeout=SUBMIT_TIMEOUT))
        try:
            data = resp.json()
        except ValueError:
            raise await paid.keep(f"HTTP {resp.status_code}, trả lời không phải JSON") from None
        job_id = _job_id(data)
        if not job_id:
            raise await paid.keep(f"HTTP {resp.status_code}, thiếu job_id")
        # Ghi log trước: nếu lưu `external_id` hỏng, mã job vẫn còn trong log để người dùng đối chiếu.
        self._log(run, "info", f"MaiVideo đã nhận job {job_id}.")
        if run is not None:
            try:
                await run.save_external(job_id, state="submitted", post_error=None)
            except JobStateLost:
                raise  # job đã bị huỷ/đặt lại: không đi tiếp
            except Exception as e:
                raise JobUnknown(
                    f"MaiVideo đã nhận job {job_id} nhưng không lưu được mã job ({type(e).__name__}).",
                    hint=f"Job MaiVideo {job_id} đã được tạo (đã tính tiền). Lấy ảnh của job này trên MaiVideo rồi "
                         "gọi job.clear_attention(action=fail); chỉ gửi lại (action=resubmit) nếu job đó không có ảnh.",
                    code="maivideo_job_unknown",
                ) from e
        return job_id

    async def _status(self, c: httpx.AsyncClient, job_id: str) -> dict | object:
        """Trạng thái job (dict), `_NOT_FOUND` nếu MaiVideo xác nhận không có, hoặc ném `_Unconfirmed`."""
        r = await c.get(f"{self.base}/api/v1/image-generation/{job_id}", headers=self._headers(), timeout=POLL_TIMEOUT)
        if r.status_code == 200:
            return _json_dict(r)
        if r.status_code != 404:
            raise _Unconfirmed(f"HTTP {r.status_code}")
        batch = await c.get(f"{self.base}/api/v1/image-generation/status", params={"job_ids": job_id},
                            headers=self._headers(), timeout=POLL_TIMEOUT)
        if batch.status_code != 200:
            raise _Unconfirmed(f"GET job 404, tra cứu hàng loạt HTTP {batch.status_code}")
        data = _json_dict(batch)
        for job in data.get("jobs") or []:
            if isinstance(job, dict) and str(job.get("job_id") or job.get("id")) == job_id:
                return job
        if job_id in [str(x) for x in data.get("missing_job_ids") or []]:
            return _NOT_FOUND
        raise _Unconfirmed("GET job 404, tra cứu hàng loạt chưa xác nhận")

    async def _poll(self, c: httpx.AsyncClient, job_id: str, run: Any) -> dict:
        interval = self._opt("poll_interval", DEFAULT_POLL_INTERVAL_S)
        timeout = self._opt("timeout_s", DEFAULT_TIMEOUT_S)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        missing = 0
        while True:
            if run is not None and run.cancelled.is_set():
                raise asyncio.CancelledError()
            try:
                data = await self._status(c, job_id)
            except (httpx.HTTPError, _Unconfirmed) as e:
                self._log(run, "warning", f"Chưa đọc được trạng thái job MaiVideo {job_id}: {_reason(e)}; thử lại.")
                data = None
            if data is _NOT_FOUND:
                missing += 1
                if missing >= MISSING_POLLS_LIMIT:
                    raise JobUnknown(
                        f"MaiVideo báo không còn job {job_id} sau {missing} lần kiểm tra liên tiếp.",
                        hint="Kiểm tra lịch sử MaiVideo (job có thể đã bị xoá hoặc hết hạn) rồi gọi "
                             "job.clear_attention(action=resubmit|fail). Gửi lại có thể tốn thêm một lượt.",
                        code="maivideo_job_missing",
                    )
            elif isinstance(data, dict):
                missing = 0
                status = str(data.get("status") or "").lower()
                if status in DONE_STATUSES or _image_urls(data):
                    return data
                if status in FAILED_STATUSES:
                    reason = _first_error(data) or status
                    raise ProviderJobError("failed", f"MaiVideo báo job {job_id} lỗi: {reason}",
                                           hint="Sửa prompt/ảnh tham chiếu rồi chạy lại (lần chạy lại là job mới).")
                if run is not None:
                    run.progress(None, stage=f"MaiVideo: {status or 'đang xử lý'}")
            if loop.time() >= deadline:
                raise TransientError(
                    f"Job MaiVideo {job_id} chưa xong sau {timeout:.0f} s; lần sau sẽ kiểm tra tiếp job này "
                    "(không gửi lại).")
            await asyncio.sleep(interval)

    async def _download(self, c: httpx.AsyncClient, job_id: str, data: dict, req: ImageRequest) -> tuple:
        urls = _image_urls(data)
        if not urls:
            reason = _first_error(data)
            raise ProviderJobError("failed", f"MaiVideo xong job {job_id} nhưng không trả ảnh nào"
                                   + (f": {reason}" if reason else "."))
        file_url = _paid.absolute_url(urls[0], self.base)
        headers = self._headers() if _paid.same_origin(file_url, self.base) else {}  # khoá chỉ gửi về MaiVideo
        try:
            r = await c.get(file_url, headers=headers, timeout=POLL_TIMEOUT)
        except httpx.HTTPError as e:
            raise TransientError(f"Tải ảnh job MaiVideo {job_id} lỗi ({type(e).__name__}); lần sau tải lại.") from e
        if r.status_code != 200 or len(r.content) < MIN_IMAGE_BYTES:
            raise TransientError(f"Tải ảnh job MaiVideo {job_id} lỗi (HTTP {r.status_code}, {len(r.content)} byte); "
                                 "lần sau tải lại.")
        path = await _paid.write_atomic(req.out_dir, r.content, _paid.sniff_ext(r.content))
        return path, file_url


def _job_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    value = data.get("job_id") or job.get("job_id") or job.get("id")
    return str(value) if value else None


def _json_dict(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError as e:
        raise _Unconfirmed("trả lời không phải JSON") from e
    if not isinstance(data, dict):
        raise _Unconfirmed("trả lời không phải object JSON")
    return data


def _images(data: dict) -> list[dict]:
    images = data.get("images")
    if not images and isinstance(data.get("job"), dict):
        images = data["job"].get("items")
    return [i for i in images or [] if isinstance(i, dict)]


def _image_urls(data: dict) -> list[str]:
    return [str(u) for u in (i.get("file_url") or i.get("url") for i in _images(data)) if u]


def _first_error(data: dict) -> str | None:
    for img in _images(data):
        if img.get("error"):
            return str(img["error"])[:300]
    err = data.get("error") or data.get("message")
    return str(err)[:300] if err else None


def _reason(e: Exception) -> str:
    return str(e) if isinstance(e, _Unconfirmed) else type(e).__name__
