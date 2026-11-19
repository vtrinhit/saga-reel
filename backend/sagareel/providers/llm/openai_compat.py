"""`openai_compat`: LLM qua API tương thích OpenAI (`/chat/completions`, `/models`) — proxy cục bộ, OpenRouter, vLLM…

- `chat`: `POST {base_url}/chat/completions`; có `on_delta` → `stream: true`, đọc SSE `data:` tới `[DONE]`.
- Ảnh (vision): mỗi ảnh thu nhỏ cạnh dài ≤ 1568 px, JPEG q85, gửi dạng `{"type": "image_url", "image_url": {"url":
  "data:image/jpeg;base64,…"}}` gắn vào tin nhắn `user` cuối — không bao giờ gửi đường dẫn file.
- `json_mode` → `response_format={"type": "json_object"}`; provider trả 400 (không hỗ trợ) → gửi lại một lần không có
  `response_format` và ghi log cảnh báo.
- Lỗi: 429/5xx/timeout/mất kết nối → `TransientError`; 401/403 → `ProviderUnavailable`; 4xx khác →
  `ProviderJobError("rejected")`. Thông báo lỗi không bao giờ chứa khoá API.
- Nội dung tin nhắn gửi nguyên văn; khoá nội bộ trong `params` (`template_key`, `task_key`) không gửi lên provider.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import anyio
import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from sagareel.core.secrets import mask
from sagareel.providers.base import (
    CapabilityError,
    ConfigField,
    Health,
    LLMAdapter,
    LLMRequest,
    LLMResult,
    ProviderJobError,
    ProviderUnavailable,
    TransientError,
)
from sagareel.providers.registry import register_adapter
from sagareel.services.jobs import JobFailed

logger = logging.getLogger("sagareel.providers")

MAX_IMAGE_EDGE = 1568
JPEG_QUALITY = 85
DEFAULT_TIMEOUT_S = 180
INTERNAL_PARAMS = frozenset({"template_key", "task_key"})
_ERR_MAX = 300


def image_data_url(path: Path) -> str:
    """Ảnh → data URL JPEG (cạnh dài ≤ `MAX_IMAGE_EDGE`, chất lượng 85). Ảnh có kênh alpha được đặt trên nền trắng."""
    try:
        with Image.open(path) as src:
            im = ImageOps.exif_transpose(src)
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.getchannel("A"))
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY)
    except (OSError, UnidentifiedImageError) as e:
        raise JobFailed("llm_image_unreadable", f"Không đọc được ảnh gửi kèm cho LLM: {type(e).__name__}.",
                        hint="Kiểm tra file ảnh (định dạng PNG/JPEG/WebP) rồi chạy lại.") from None
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class _BadRequest(Exception):
    """400 từ provider (nội bộ): để thử lại không có `response_format`."""

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


@register_adapter
class OpenAICompatAdapter(LLMAdapter):
    adapter_id = "openai_compat"
    label = "LLM tương thích OpenAI"
    config_fields: ClassVar[list[ConfigField]] = [
        ConfigField("base_url", "Địa chỉ API", "url", True, help="Gồm cả /v1, vd. http://localhost:20128/v1"),
        ConfigField("api_key", "Khoá API", "secret", False),
        ConfigField("default_model", "Model mặc định", "str", False,
                    help="Dùng khi công việc không chọn model riêng."),
        ConfigField("vision", "Hỗ trợ ảnh (vision)", "bool", False, True,
                    help="Tắt nếu model không đọc được ảnh; công việc cần ảnh sẽ bỏ qua kết nối này."),
        ConfigField("json_mode", "Hỗ trợ JSON mode", "bool", False, True),
        ConfigField("timeout_s", "Thời gian chờ tối đa (giây)", "int", False, DEFAULT_TIMEOUT_S),
    ]
    default_capabilities: ClassVar[dict] = {"vision": True, "json_mode": True, "stream": True}
    http_timeout: ClassVar[float] = float(DEFAULT_TIMEOUT_S)

    # --- Tiện ích ----------------------------------------------------------------------------------------------

    def _url(self, path: str) -> str:
        base = (self.conn.base_url or "").rstrip("/")
        if not base:
            raise ProviderUnavailable(
                f"Kết nối LLM '{self.conn.name}' chưa có địa chỉ API.",
                hint="Điền địa chỉ API (base_url, gồm /v1) trong Cài đặt → Provider.",
            )
        return f"{base}/{path}"

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.conn.api_key:
            h["Authorization"] = f"Bearer {self.conn.api_key}"
        return h

    def _timeout(self) -> httpx.Timeout:
        raw = self.conn.options.get("timeout_s")
        total = float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0 else \
            float(DEFAULT_TIMEOUT_S)
        return httpx.Timeout(total, connect=min(15.0, total))

    def _redact(self, text: str) -> str:
        key = self.conn.api_key
        return text.replace(key, mask(key)) if key else text

    def _provider_message(self, resp: httpx.Response) -> str:
        try:
            data = resp.json()
            err = data.get("error") if isinstance(data, dict) else None
            msg = err.get("message") if isinstance(err, dict) else (err if isinstance(err, str) else None)
            text = str(msg) if msg else resp.text
        except (ValueError, UnicodeDecodeError):
            text = resp.text
        return self._redact(text.strip())[:_ERR_MAX]

    def _status_error(self, resp: httpx.Response) -> Exception:
        code = resp.status_code
        detail = self._provider_message(resp)
        where = f"LLM '{self.conn.name}'"
        if code == 429:
            retry_after = None
            raw = resp.headers.get("retry-after")
            if raw:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None
            return TransientError(f"{where} đang giới hạn tốc độ (429): {detail}", retry_after=retry_after)
        if code >= 500 or code == 408:
            return TransientError(f"{where} lỗi phía máy chủ ({code}): {detail}")
        if code in (401, 403):
            return ProviderUnavailable(
                f"{where} từ chối xác thực ({code}): {detail}",
                hint="Kiểm tra khoá API của kết nối trong Cài đặt → Provider.",
            )
        return ProviderJobError("rejected", f"{where} từ chối yêu cầu ({code}): {detail}",
                                hint="Kiểm tra model và tham số của công việc LLM.")

    def _network_error(self, e: httpx.HTTPError) -> TransientError:
        if isinstance(e, httpx.TimeoutException):
            return TransientError(f"LLM '{self.conn.name}' quá thời gian chờ ({type(e).__name__}).")
        return TransientError(f"Không kết nối được LLM '{self.conn.name}': {type(e).__name__}: "
                              f"{self._redact(str(e))[:_ERR_MAX]}")

    async def _messages(self, req: LLMRequest) -> list[dict]:
        messages = [dict(m) for m in req.messages]
        if not req.images:
            return messages
        if self.capabilities().get("vision") is False:
            raise CapabilityError(
                f"Kết nối LLM '{self.conn.name}' không hỗ trợ ảnh (vision).",
                hint="Chọn kết nối/model đọc được ảnh cho công việc này.",
            )
        parts = [{"type": "image_url", "image_url": {"url": await anyio.to_thread.run_sync(image_data_url, p)}}
                 for p in req.images]
        idx = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)
        if idx is None:
            messages.append({"role": "user", "content": parts})
            return messages
        content = messages[idx].get("content")
        if isinstance(content, list):
            messages[idx]["content"] = [*content, *parts]
        else:
            text = "" if content is None else str(content)
            messages[idx]["content"] = [{"type": "text", "text": text}, *parts]
        return messages

    # --- Gửi ---------------------------------------------------------------------------------------------------

    async def _send(self, body: dict, on_delta: Callable[[str], None] | None) -> tuple[str, dict]:
        url = self._url("chat/completions")
        try:
            async with self.client() as c:
                if on_delta is None:
                    resp = await c.post(url, json=body, headers=self._headers(), timeout=self._timeout())
                    if resp.status_code >= 400:
                        err = self._status_error(resp)
                        raise _BadRequest(err) if resp.status_code == 400 else err
                    return self._parse_completion(resp)
                async with c.stream("POST", url, json={**body, "stream": True},
                                    headers={**self._headers(), "Accept": "text/event-stream"},
                                    timeout=self._timeout()) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        err = self._status_error(resp)
                        raise _BadRequest(err) if resp.status_code == 400 else err
                    return await self._read_stream(resp, on_delta)
        except httpx.HTTPError as e:
            raise self._network_error(e) from None

    def _parse_completion(self, resp: httpx.Response) -> tuple[str, dict]:
        try:
            data = resp.json()
            msg = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderJobError("bad_response", f"LLM '{self.conn.name}' trả về dữ liệu không đúng định dạng.",
                                   hint="Kiểm tra địa chỉ API có đúng chuẩn OpenAI không.") from None
        content = msg.get("content") if isinstance(msg, dict) else None
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ("" if content is None else str(content)), dict(usage)

    async def _read_stream(self, resp: httpx.Response, on_delta: Callable[[str], None]) -> tuple[str, dict]:
        parts: list[str] = []
        usage: dict = {}
        async for line in resp.aiter_lines():
            line = line.rstrip("\r")
            if not line.startswith("data:"):
                continue  # dòng trống, comment ":", event:, id:
            payload = line[5:].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                logger.warning("LLM '%s': bỏ qua dòng SSE không phải JSON", self.conn.name)
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                err = chunk["error"]
                msg = err.get("message") if isinstance(err, dict) else err
                raise ProviderJobError("stream_error", f"LLM '{self.conn.name}' báo lỗi giữa chừng: "
                                       f"{self._redact(str(msg))[:_ERR_MAX]}")
            if isinstance(chunk.get("usage"), dict):
                usage = dict(chunk["usage"])
            for choice in chunk.get("choices") or []:
                delta = (choice or {}).get("delta") or {}
                text = delta.get("content") if isinstance(delta, dict) else None
                if text:
                    parts.append(text)
                    on_delta(text)
        return "".join(parts), usage

    # --- API adapter --------------------------------------------------------------------------------------------

    async def chat(self, req: LLMRequest, on_delta: Callable[[str], None] | None = None) -> LLMResult:
        model = req.model or self.conn.default_model or None
        if not model:
            raise ProviderUnavailable(
                f"Kết nối LLM '{self.conn.name}' chưa có model.",
                hint="Đặt model mặc định cho kết nối, hoặc chọn model trong định tuyến của công việc.",
            )
        self._url("chat/completions")  # kiểm tra base_url trước khi xử lý ảnh
        body: dict[str, Any] = {k: v for k, v in req.params.items() if k not in INTERNAL_PARAMS}
        body["model"] = model
        body["messages"] = await self._messages(req)
        use_format = req.json_mode and self.capabilities().get("json_mode") is not False
        if use_format:
            body["response_format"] = {"type": "json_object"}
        try:
            text, usage = await self._send(body, on_delta)
        except _BadRequest as bad:
            if not use_format:
                raise bad.error from None
            logger.warning("LLM '%s' (model %s) từ chối response_format (400: %s); gửi lại không có response_format",
                           self.conn.name, model, bad.error)
            body.pop("response_format", None)
            try:
                text, usage = await self._send(body, on_delta)
            except _BadRequest as again:
                raise again.error from None
        return LLMResult(text=text, model=model, usage=usage)

    async def list_models(self) -> list[str]:
        url = self._url("models")
        try:
            async with self.client() as c:
                resp = await c.get(url, headers=self._headers(), timeout=self._timeout())
        except httpx.HTTPError as e:
            raise self._network_error(e) from None
        if resp.status_code >= 400:
            raise self._status_error(resp)
        try:
            data = resp.json()
            items = data.get("data") if isinstance(data, dict) else data
            return [str(m["id"]) if isinstance(m, dict) else str(m) for m in (items or [])]
        except (ValueError, KeyError, TypeError):
            raise ProviderJobError("bad_response", f"LLM '{self.conn.name}': danh sách model không đúng định dạng.") \
                from None

    async def check_health(self) -> Health:
        if not (self.conn.base_url or "").strip():
            return Health(status="not_configured", detail="Chưa điền địa chỉ API (base_url).")
        try:
            models = await self.list_models()
        except (TransientError, ProviderUnavailable, ProviderJobError) as e:
            return Health(status="dead", detail=self._redact(e.msg))
        if not models:
            return Health(status="dead", detail="Máy chủ LLM không trả về model nào.")
        return Health(status="alive", detail=f"{len(models)} model sẵn sàng.",
                      capabilities={**self.capabilities(), "models": models})

    async def estimate(self, req: LLMRequest) -> dict:
        chars = sum(len(m["content"]) if isinstance(m.get("content"), str) else 0 for m in req.messages)
        return {"units": 1, "prompt_chars": chars, "images": len(req.images)}
