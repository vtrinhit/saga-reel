"""Dùng chung cho adapter ảnh tốn tiền: gửi POST một lần duy nhất, phân loại kết quả, ghi file an toàn.

Quy tắc chống tạo trùng (hàng đợi job, Task 7): ngay trước POST tốn tiền ghi dấu `state="submitting"` (bền trước khi
gửi). Chỉ khi chắc chắn provider **chưa** nhận request mới xoá dấu rồi ném lỗi; mọi trường hợp mập mờ giữ dấu và ném
`JobUnknown` → job `needs_attention`, người dùng quyết định (không bao giờ tự gửi lại).

| Kết quả POST | Dấu | Lỗi |
|---|---|---|
| `ConnectError` / `ConnectTimeout` / `PoolTimeout` (chưa gửi) | xoá | `TransientError` |
| HTTP 502/503/521/522/523/525/526/530 (cổng/tunnel, origin chưa nhận) | xoá | `TransientError` |
| HTTP 429 | xoá | `TransientError(retry_after=Retry-After)` |
| HTTP 4xx khác | xoá | `ProviderJobError("rejected")` |
| Đã gửi nhưng không có trả lời (ReadTimeout, RemoteProtocolError…), 5xx khác (500, 504, 520, 524…) | giữ | `JobUnknown` |
"""

from __future__ import annotations

import base64
import contextlib
import email.utils
import hashlib
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import httpx

from sagareel.providers.base import JobUnknown, ProviderJobError
from sagareel.services.jobs import TransientError

# Lỗi xảy ra trước khi thân request rời máy: provider chưa thấy POST.
UNSENT_ERRORS: tuple[type[Exception], ...] = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Cổng/tunnel trả lời thay origin (502/503, Cloudflare 521–523 origin không tới được, 525/526 TLS tới origin, 530
# tunnel). 504, 520, 524 KHÔNG ở đây: origin có thể đã nhận request → "không rõ".
GATEWAY_STATUSES = frozenset({502, 503, 521, 522, 523, 525, 526, 530})

SUBMITTING = "submitting"

_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif", ".bmp": "image/bmp"}


def ref_mime(path: Path, data: bytes) -> str:
    """MIME của ảnh tham chiếu: theo nội dung nếu nhận ra, ngược lại theo đuôi file (mặc định PNG)."""
    ext = sniff_ext(data, default="")
    return _MIME_BY_EXT.get(ext or path.suffix.lower(), "image/png")


def sniff_ext(data: bytes, default: str = ".png") -> str:
    """Đuôi file theo magic bytes (PNG/JPEG/WEBP/GIF); không nhận ra → `default`."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return default


def read_refs(refs: list[Path]) -> list[tuple[Path, bytes]]:
    """Đọc byte các ảnh tham chiếu (đồng bộ — gọi qua `anyio.to_thread`). Thiếu file → `ProviderJobError`."""
    out = []
    for p in refs:
        path = Path(p)
        try:
            out.append((path, path.read_bytes()))
        except OSError as e:
            raise ProviderJobError("bad_reference", f"Không đọc được ảnh tham chiếu {path.name}: {e.strerror}.") from e
    return out


def data_url(path: Path, data: bytes) -> str:
    return f"data:{ref_mime(path, data)};base64,{base64.b64encode(data).decode('ascii')}"


def fingerprint(payload: dict, ref_bytes: list[bytes]) -> str:
    """sha256 của payload (không kèm ảnh) + sha256 từng ảnh tham chiếu theo thứ tự. Không chứa khoá API."""
    blob = json.dumps({"payload": payload, "refs": [hashlib.sha256(b).hexdigest() for b in ref_bytes]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def retry_after_s(resp: httpx.Response) -> float | None:
    """`Retry-After` (giây hoặc ngày HTTP) → giây; không có/không đọc được → None."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    with contextlib.suppress(ValueError):
        return max(0.0, float(raw))
    with contextlib.suppress(TypeError, ValueError, IndexError):
        when = email.utils.parsedate_to_datetime(raw)
        return max(0.0, when.timestamp() - time.time())
    return None


def health_status_for(status_code: int) -> str:
    """Trạng thái sức khoẻ khi provider trả HTTP lỗi: 429 (hết lượt tạm thời) và 5xx/cổng → `unknown` (có thể chỉ
    là tạm thời, không kết luận được); còn lại (401/403 sai khoá, 4xx khác) → `dead`."""
    return "unknown" if status_code == 429 or status_code >= 500 else "dead"


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "••••") if secret else text


def error_detail(resp: httpx.Response, secret: str = "") -> str:
    """Lý do lỗi provider gửi kèm (message/error/detail của JSON, hoặc đầu thân trả lời), đã che khoá, ≤ 300 ký tự."""
    detail: Any = None
    with contextlib.suppress(ValueError):
        data = resp.json()
        if isinstance(data, dict):
            detail = data.get("message") or data.get("error") or data.get("detail")
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
    if not detail:
        detail = resp.text
    return redact(str(detail).strip(), secret)[:300]


def same_origin(url: str, base: str) -> bool:
    a, b = httpx.URL(url), httpx.URL(base)
    return (a.scheme, a.host, a.port) == (b.scheme, b.host, b.port)


def absolute_url(url: str, base: str) -> str:
    """URL tương đối của provider (vd. `/static/exports/a.png`) → ghép với `base`."""
    if url.startswith(("http://", "https://")):
        return url
    return f"{base.rstrip('/')}/{url.lstrip('/')}"


async def write_atomic(out_dir: Path, data: bytes, ext: str) -> Path:
    """Ghi `out_dir/<uuid><ext>` qua file tạm rồi `os.replace` (không bao giờ để lại ảnh ghi dở)."""

    def work() -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{uuid.uuid4().hex}{ext}"
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    return await anyio.to_thread.run_sync(work)


class PaidPost:
    """Một lần POST tốn tiền có dấu `submitting` (xem docstring module). `run` có thể None (gọi trực tiếp)."""

    def __init__(self, run: Any, *, provider: str, code: str, hint: str, secret: str) -> None:
        self.run = run
        self.provider = provider  # tên hiển thị, vd. "MaiVideo"
        self.code = code  # mã needs_attention, vd. "maivideo_job_unknown"
        self.hint = hint
        self.secret = secret

    def check_resume(self) -> None:
        """Lần chạy trước đã đánh dấu `submitting` mà chưa có id → không được gửi lại."""
        run = self.run
        if run is not None and not run.external_id and run.external.get("state") == SUBMITTING:
            reason = run.external.get("post_error")
            raise self.unknown(f"Lần gửi trước tới {self.provider} bị gián đoạn trước khi nhận được mã job"
                               + (f" ({reason})" if reason else "") + "; chưa rõ provider đã tạo (và tính tiền) hay chưa.")

    def unknown(self, msg: str) -> JobUnknown:
        return JobUnknown(msg, hint=self.hint, code=self.code)

    async def mark(self, fp: str) -> None:
        if self.run is not None:
            await self.run.save_external(None, state=SUBMITTING, fingerprint=fp)

    async def clear(self) -> None:
        if self.run is not None:
            await self.run.save_external(None, state=None, fingerprint=None, post_error=None)

    async def keep(self, reason: str) -> JobUnknown:
        """Giữ dấu, ghi lý do để người quyết định xem; trả lỗi `JobUnknown` để ném."""
        reason = redact(reason, self.secret)[:300]
        if self.run is not None:
            with contextlib.suppress(Exception):  # chỉ là ghi chú; dấu `submitting` đã bền từ trước
                await self.run.save_external(None, post_error=reason)
        return self.unknown(f"Đã gửi yêu cầu tới {self.provider} nhưng không nhận được kết quả rõ ràng ({reason}); "
                            "chưa rõ provider đã tạo (và tính tiền) hay chưa.")

    async def send(self, fp: str, do_post: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        """Đánh dấu → POST → phân loại. Trả response 2xx; mọi trường hợp khác ném lỗi theo bảng ở đầu module."""
        await self.mark(fp)  # JobStateLost (job bị huỷ/đặt lại) → dừng tại đây, chưa gửi gì
        try:
            resp = await do_post()
        except UNSENT_ERRORS as e:
            await self.clear()
            raise TransientError(f"Không kết nối được {self.provider} ({type(e).__name__}); yêu cầu chưa được gửi, "
                                 "sẽ thử lại.") from e
        except httpx.TransportError as e:
            raise await self.keep(f"{type(e).__name__}") from e
        except Exception as e:  # lỗi bất ngờ khi đang gửi: coi như mập mờ, không bao giờ tự gửi lại
            raise await self.keep(f"lỗi bất ngờ {type(e).__name__}") from e
        status = resp.status_code
        if resp.is_success:
            return resp
        detail = error_detail(resp, self.secret)
        if status == 429:
            await self.clear()
            raise TransientError(f"{self.provider} đang giới hạn tần suất (HTTP 429); sẽ thử lại.",
                                 retry_after=retry_after_s(resp))
        if status in GATEWAY_STATUSES:
            await self.clear()
            raise TransientError(f"{self.provider} chưa nhận yêu cầu (lỗi cổng/tunnel HTTP {status}); sẽ thử lại.")
        if 400 <= status < 500:
            await self.clear()
            hint = "Kiểm tra khoá API trong Cài đặt → Provider." if status in (401, 403) else \
                "Kiểm tra prompt, model, độ phân giải, tỉ lệ và ảnh tham chiếu rồi chạy lại."
            raise ProviderJobError("rejected", f"{self.provider} từ chối yêu cầu (HTTP {status}): {detail}", hint=hint)
        raise await self.keep(f"HTTP {status}")
