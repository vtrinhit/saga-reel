"""`openai_images`: tạo ảnh qua API tương thích OpenAI Images.

- Không có ảnh tham chiếu: `POST {base}/images/generations`, JSON `{model, prompt, n: 1, size}`.
- Có ảnh tham chiếu: `POST {base}/images/edits`, multipart (`model`, `prompt`, `n`, `size`, mỗi ảnh một phần `image[]`
  theo đúng thứ tự).
- Kết quả `data[0].b64_json` hoặc `data[0].url` (tải về; khoá chỉ gửi khi URL cùng máy chủ với `base_url`).
- Sức khoẻ: `GET {base}/models`.

API này đồng bộ (không có mã job) nhưng vẫn có thể tính tiền khi trả lời bị mất: dùng cùng dấu `submitting` như
MaiVideo (`_paid.py`) — mập mờ → `JobUnknown("openai_images_job_unknown")`, không bao giờ tự gửi lại.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
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
    ProviderUnavailable,
    http_health_status,
)
from sagareel.providers.image import _paid
from sagareel.providers.registry import register_adapter

DEFAULT_MODEL = "gpt-image-1"
MAX_REFS = 16
# Kích thước theo tỉ lệ (bộ kích thước của gpt-image-1); options.size ghi đè cho máy chủ dùng bộ khác.
SIZES = {"1:1": "1024x1024", "9:16": "1024x1536", "4:5": "1024x1536", "3:4": "1024x1536", "16:9": "1536x1024"}
SUBMIT_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=120.0, pool=30.0)
FETCH_TIMEOUT = httpx.Timeout(60.0)
HEALTH_TIMEOUT = httpx.Timeout(15.0)

UNKNOWN_HINT = (
    "Kiểm tra lịch sử sử dụng/hoá đơn của provider trước: nếu đã bị tính tiền mà mất ảnh thì gọi "
    "job.clear_attention(action=fail); muốn tạo lại thì gọi job.clear_attention(action=resubmit) "
    "(có thể tốn thêm một lượt)."
)


@register_adapter
class OpenAIImagesAdapter(ImageAdapter):
    adapter_id = "openai_images"
    label = "Ảnh qua API tương thích OpenAI"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("base_url", "Địa chỉ API (kết thúc bằng /v1)", "url", True, "https://api.openai.com/v1"),
        ConfigField("api_key", "Khoá API", "secret", False),
        ConfigField("default_model", "Model mặc định", "str", False, DEFAULT_MODEL),
        ConfigField("size", "Kích thước cố định", "str", False, "",
                    help="Bỏ trống = theo tỉ lệ (1024x1536, 1536x1024, 1024x1024)."),
        ConfigField("max_refs", "Số ảnh tham chiếu tối đa", "int", False, MAX_REFS),
    ]
    default_capabilities: ClassVar[dict] = {
        "aspects": list(SIZES),
        "max_refs": MAX_REFS,
    }

    @property
    def base(self) -> str:
        return (self.conn.base_url or "").strip().rstrip("/")

    @property
    def key(self) -> str:
        return (self.conn.api_key or "").strip()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    async def estimate(self, req: ImageRequest) -> dict:
        return {"units": 1, "kind": "image"}

    async def check_health(self) -> Health:
        if not self.base:
            return Health(status="not_configured", detail="Chưa nhập địa chỉ API.")
        try:
            async with self.client() as c:
                r = await c.get(f"{self.base}/models", headers=self._headers(), timeout=HEALTH_TIMEOUT)
        except httpx.HTTPError as e:
            return Health(status="dead", detail=f"Không kết nối được máy chủ ảnh ({type(e).__name__}).")
        if r.status_code in (401, 403):
            return Health(status="dead", detail=f"Máy chủ ảnh từ chối khoá API (HTTP {r.status_code}).")
        if not r.is_success:
            return Health(status=http_health_status(r.status_code),
                          detail=f"Máy chủ ảnh trả lỗi HTTP {r.status_code}: {_paid.error_detail(r, self.key)[:200]}")
        models: list[str] = []
        try:
            data = r.json()
            items = data.get("data") if isinstance(data, dict) else None
            models = [str(m["id"]) for m in items or [] if isinstance(m, dict) and m.get("id")]
        except ValueError:
            pass
        return Health(status="alive", detail=f"Máy chủ ảnh sẵn sàng ({len(models)} model).",
                      capabilities={**self.capabilities(), "models": models})

    def _size(self, aspect: str) -> str:
        return str(self.conn.options.get("size") or "") or SIZES.get(aspect, "1024x1024")

    async def generate(self, req: ImageRequest, run=None) -> ImageResult:
        if not self.base:
            raise ProviderUnavailable("Chưa nhập địa chỉ API tạo ảnh.",
                                      hint="Nhập địa chỉ trong Cài đặt → Provider rồi chạy lại.")
        limit = int(self.capabilities().get("max_refs") or MAX_REFS)
        if len(req.refs) > limit:
            raise CapabilityError(f"Kết nối '{self.conn.name}' nhận tối đa {limit} ảnh tham chiếu "
                                  f"(yêu cầu có {len(req.refs)}).", hint=f"Bớt ảnh tham chiếu xuống còn {limit}.")
        paid = _paid.PaidPost(run, provider="máy chủ ảnh", code="openai_images_job_unknown", hint=UNKNOWN_HINT,
                              secret=self.key)
        paid.check_resume()
        model = req.model or self.conn.default_model or DEFAULT_MODEL
        fields: dict[str, Any] = {"model": model, "prompt": req.prompt, "n": 1, "size": self._size(req.aspect)}
        meta = {"adapter": self.adapter_id, "model": model, "size": fields["size"],
                "endpoint": "edits" if req.refs else "generations", "prompt_received": req.prompt,
                "refs": len(req.refs)}
        if run is not None and run.external.get("state") == "completed":
            # Lần chạy trước đã tạo xong (và đã tính tiền) rồi dừng giữa chừng: không bao giờ gửi lại.
            done = Path(str(run.external.get("result_file") or ""))
            if run.external.get("result_file") and done.is_file():
                return ImageResult(paths=[done], meta={**meta, "resumed": True})
            raise paid.unknown("Lần chạy trước đã tạo ảnh (đã có thể bị tính tiền) nhưng file kết quả không còn; "
                               "không tự gửi lại.")
        refs = await anyio.to_thread.run_sync(_paid.read_refs, list(req.refs))
        endpoint = meta["endpoint"]
        fp = _paid.fingerprint({**fields, "endpoint": endpoint}, [data for _, data in refs])
        url = f"{self.base}/images/{endpoint}"
        if run is not None:
            run.progress(0.0, stage="Đang tạo ảnh")
        async with self.client() as c:
            if refs:
                files = [("image[]", (p.name, data, _paid.ref_mime(p, data))) for p, data in refs]
                form = {k: str(v) for k, v in fields.items()}  # nguyên văn; httpx mã hoá UTF-8

                def post() -> Any:
                    return c.post(url, data=form, files=files, headers=self._headers(), timeout=SUBMIT_TIMEOUT)
            else:

                def post() -> Any:
                    return c.post(url, json=fields, headers=self._headers(), timeout=SUBMIT_TIMEOUT)

            resp = await paid.send(fp, post)
            image = await self._image_bytes(c, resp, paid)
        path = await _paid.write_atomic(req.out_dir, image, _paid.sniff_ext(image))
        if run is not None:
            await run.save_external(None, state="completed", result_file=str(path))
            run.progress(100.0, stage="Đã tạo ảnh")
        return ImageResult(paths=[path], meta=meta)

    async def _image_bytes(self, c: httpx.AsyncClient, resp: httpx.Response, paid: _paid.PaidPost) -> bytes:
        """Ảnh từ trả lời 2xx. Trả lời hỏng/không tải được ảnh → giữ dấu, `JobUnknown` (đã có thể bị tính tiền)."""
        try:
            data = resp.json()
            item = data["data"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            raise await paid.keep(f"HTTP {resp.status_code}, trả lời không có ảnh") from None
        if isinstance(item, dict) and item.get("b64_json"):
            try:
                return base64.b64decode(item["b64_json"], validate=True)
            except (binascii.Error, ValueError):
                raise await paid.keep("b64_json không hợp lệ") from None
        url = item.get("url") if isinstance(item, dict) else None
        if not url:
            raise await paid.keep(f"HTTP {resp.status_code}, trả lời không có ảnh")
        url = _paid.absolute_url(str(url), self.base)
        headers = self._headers() if _paid.same_origin(url, self.base) else {}
        try:
            r = await c.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        except httpx.HTTPError as e:
            raise await paid.keep(f"tải ảnh lỗi {type(e).__name__}: {url}") from e
        if r.status_code != 200 or not r.content:
            raise await paid.keep(f"tải ảnh lỗi HTTP {r.status_code}: {url}")
        return r.content
