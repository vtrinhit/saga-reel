"""Phần chung của adapter âm thanh qua API tương thích OpenAI (`openai_tts`, `openai_stt`).

- URL: `{base_url}/<đường dẫn>` (base_url thường kết thúc bằng `/v1`).
- Sức khoẻ: `GET {base}/models` (404 vẫn đi tiếp tới yêu cầu thử; 429/5xx → `unknown`; 401/403 → `dead`), rồi (tuỳ chọn `health_speech`, **mặc định bật**) một yêu cầu thật cực ngắn — TTS 1 ký
  tự, STT một WAV im lặng tự tạo — vì `/models` của LLM proxy vẫn trả 200 khi chưa có backend TTS/STT. Máy chủ trả
  "No credentials" → `not_configured` (không bao giờ `alive`, không phải `dead`). Chủ máy tắt tuỳ chọn được nếu không
  muốn tốn tiền cho mỗi lần kiểm tra.
- Gửi yêu cầu tính tiền trong job: ghi dấu `state="submitting"` ngay trước POST; provider chắc chắn **không** nhận
  (không kết nối được, 408/429/502/503/52x, 4xx) → xoá dấu rồi ném lỗi; không rõ (mất kết nối sau khi gửi, 500/504) →
  giữ dấu và ném `JobUnknown` (`<adapter>_job_unknown`) để người dùng quyết định. Lần chạy sau thấy dấu → không gửi lại.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from sagareel.core.secrets import mask
from sagareel.providers.base import (
    Adapter,
    ConfigField,
    Health,
    JobUnknown,
    ProviderJobError,
    ProviderUnavailable,
    http_health_status,
)
from sagareel.services.jobs import TransientError

if TYPE_CHECKING:
    from sagareel.services.worker import JobRun

NO_CREDENTIALS = "No credentials"
SUBMITTING = "submitting"
# Gateway trả các mã này khi yêu cầu chưa tới được máy chủ thật → chắc chắn chưa xử lý (cùng 429 "bận" và 408
# "Request Timeout": máy chủ bỏ yêu cầu trước khi nhận đủ, chưa xử lý).
NOT_ACCEPTED = frozenset({408, 429, 502, 503, 521, 522, 523, 525, 526, 530})
# Lỗi truyền tải xảy ra trước khi yêu cầu được gửi đủ → máy chủ chưa xử lý.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.WriteError, httpx.WriteTimeout,
            httpx.UnsupportedProtocol)

HEALTH_SPEECH_FIELD = ConfigField(
    "health_speech", "Kiểm tra bằng một yêu cầu thật", "bool", False, True,
    help="Khi kiểm tra, gửi thêm một yêu cầu cực ngắn (có thể tốn một ít tiền) để biết máy chủ thật sự đọc/nghe được. "
         "Tắt: chỉ đọc danh sách model (không phát hiện được máy chủ chưa cấu hình TTS/STT).",
)


def health_speech(options: dict) -> bool:
    """Giá trị hiệu lực của `health_speech` (thiếu → mặc định của trường)."""
    value = options.get("health_speech")
    return bool(HEALTH_SPEECH_FIELD.default if value is None else value)


def error_message(r: httpx.Response) -> str:
    """Thông báo lỗi của provider (`error.message` nếu có), cắt ngắn."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:300]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str):
            return err[:300]
        if body.get("message"):
            return str(body["message"])[:300]
    return r.text[:300]


def no_credentials(r: httpx.Response) -> bool:
    return 400 <= r.status_code < 500 and NO_CREDENTIALS.lower() in r.text.lower()


class OpenAIAudioMixin(Adapter):
    """Đặt trước lớp kind trong danh sách kế thừa: `class X(OpenAIAudioMixin, TTSAdapter)`."""

    service_label: ClassVar[str] = "âm thanh"  # "đọc lời" / "nhận dạng lời", cho thông báo

    def _url(self, path: str) -> str:
        return f"{self.conn.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.conn.api_key}"} if self.conn.api_key else {}

    def _msg(self, r: httpx.Response) -> str:
        """Thông báo lỗi của provider, che khoá API nếu máy chủ lỡ lặp lại nó."""
        text = error_message(r)
        return text.replace(self.conn.api_key, mask(self.conn.api_key)) if self.conn.api_key else text

    def _job_unknown_code(self) -> str:
        return f"{self.adapter_id}_job_unknown"

    # --- Sức khoẻ -----------------------------------------------------------------------------------------------

    async def _probe_request(self, c: httpx.AsyncClient) -> httpx.Response:
        """Yêu cầu thật cực ngắn cho `health_speech` (lớp con cài đặt)."""
        raise NotImplementedError

    async def check_health(self) -> Health:
        if not self.conn.base_url:
            return Health(status="not_configured", detail="Chưa nhập địa chỉ máy chủ (base_url).")
        try:
            async with self.client() as c:
                r = await c.get(self._url("models"), headers=self._headers())
                if r.status_code in (401, 403):
                    return Health(status="dead", detail=f"Máy chủ từ chối khoá API (HTTP {r.status_code}).")
                # 404: máy chủ không có /models (nhiều máy chủ chỉ có TTS/STT) — yêu cầu thử mới quyết định.
                if not r.is_success and r.status_code != 404:
                    return Health(status=http_health_status(r.status_code),
                                  detail=f"Máy chủ trả lỗi HTTP {r.status_code}: {self._msg(r)}")
                if not health_speech(self.conn.options):
                    if r.status_code == 404:
                        return Health(status="unknown",
                                      detail="Máy chủ không có /models (HTTP 404); bật “Kiểm tra bằng một yêu cầu "
                                             "thật” để biết máy chủ có dùng được không.")
                    return Health(status="alive", detail="Máy chủ trả lời (chưa thử gửi yêu cầu thật).",
                                  capabilities=self.capabilities())
                p = await self._probe_request(c)
        except httpx.HTTPError as e:
            return Health(status="dead", detail=f"Không kết nối được máy chủ ({type(e).__name__}).")
        if no_credentials(p):
            return Health(status="not_configured",
                          detail=f"Máy chủ chưa cấu hình dịch vụ {self.service_label}: {self._msg(p)}")
        if not p.is_success:
            return Health(status=http_health_status(p.status_code),
                          detail=f"Yêu cầu thử lỗi HTTP {p.status_code}: {self._msg(p)}")
        return Health(status="alive", detail=f"Đã thử {self.service_label} thành công.",
                      capabilities=self.capabilities())

    # --- Gửi yêu cầu tính tiền trong job --------------------------------------------------------------------------

    async def _paid_post(self, run: JobRun | None, send: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]],
                         ) -> httpx.Response:
        """POST có dấu "submitting"; trả response thành công (đã xoá dấu) hoặc ném lỗi job phù hợp."""
        label = self.service_label
        if run is not None:
            if run.external.get("state") == SUBMITTING and not run.external_id:
                raise JobUnknown(
                    f"Lần chạy trước đã gửi yêu cầu {label} nhưng không biết máy chủ đã nhận chưa.",
                    hint="Kiểm tra phía provider; chọn gửi lại nếu chắc chắn chưa bị tính tiền.",
                    code=self._job_unknown_code(),
                )
            await run.save_external(state=SUBMITTING)
        try:
            async with self.client() as c:
                r = await send(c)
        except NOT_SENT as e:
            await _clear(run)
            raise TransientError(f"Không kết nối được máy chủ {label} ({type(e).__name__}).") from e
        except httpx.HTTPError as e:
            raise JobUnknown(
                f"Mất kết nối sau khi gửi yêu cầu {label} ({type(e).__name__}); không rõ máy chủ đã xử lý chưa.",
                hint="Kiểm tra phía provider; chọn gửi lại nếu chắc chắn chưa bị tính tiền.",
                code=self._job_unknown_code(),
            ) from e
        if r.is_success:
            await _clear(run)
            return r
        msg = self._msg(r)
        if r.status_code in NOT_ACCEPTED:
            await _clear(run)
            raise TransientError(f"Máy chủ {label} đang bận (HTTP {r.status_code}): {msg}",
                                 retry_after=_retry_after(r))
        if 400 <= r.status_code < 500:
            await _clear(run)
            if no_credentials(r):
                raise ProviderUnavailable(
                    f"Máy chủ chưa cấu hình dịch vụ {label}: {msg}",
                    hint="Thêm thông tin xác thực cho dịch vụ này ở máy chủ/proxy, hoặc chọn kết nối khác.",
                )
            if r.status_code in (401, 403):
                raise ProviderUnavailable(f"Máy chủ từ chối khoá API (HTTP {r.status_code}).",
                                          hint="Kiểm tra khoá API của kết nối trong Cài đặt → Provider.")
            raise ProviderJobError("rejected", f"Máy chủ từ chối yêu cầu {label} (HTTP {r.status_code}): {msg}",
                                   hint="Kiểm tra model, giọng và tham số của kết nối.")
        raise JobUnknown(
            f"Máy chủ {label} lỗi HTTP {r.status_code} sau khi nhận yêu cầu: {msg}",
            hint="Kiểm tra phía provider; chọn gửi lại nếu chắc chắn chưa bị tính tiền.",
            code=self._job_unknown_code(),
        )


async def _clear(run: JobRun | None) -> None:
    if run is not None:
        await run.save_external(state=None)


def _retry_after(r: httpx.Response) -> float | None:
    raw: Any = r.headers.get("retry-after")
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except ValueError:
        return None
