"""Lớp nền cho adapter provider (spec §7.1): cấu hình, kết nối, sức khoẻ, request/result theo năng lực, lỗi chuẩn.

Một adapter là một lớp con của `ImageAdapter | VideoAdapter | LLMAdapter | TTSAdapter | STTAdapter`, khai báo
`adapter_id`, `label`, `config_fields`, `default_capabilities` và gắn `@register_adapter` (`providers.registry`).

Lỗi chuẩn là lớp con của lỗi job (`services.jobs`) nên worker xử lý chúng như mọi lỗi job khác:

| Lỗi | Trạng thái job | `error_code` |
|---|---|---|
| `ProviderUnavailable(msg, hint)` | `failed` | `provider_unavailable` |
| `ProviderJobError(kind, msg, hint)` | `failed` | `provider_<kind>` |
| `CapabilityError(msg, hint)` | `failed` | `capability_mismatch` |
| `JobUnknown(msg, hint, code=...)` | `needs_attention` | do adapter đặt, vd. `maivideo_job_unknown` |
| `TransientError(msg, retry_after)` | `queued` (thử lại) | — |

Các lỗi này chỉ dùng **bên trong job**; op (REST/CLI/MCP) ném `AppError`.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import httpx

from sagareel.services.jobs import JobFailed, NeedsAttention, TransientError

if TYPE_CHECKING:
    from sagareel.services.worker import JobRun

Kind = Literal["image", "video", "llm", "tts", "stt"]
KINDS: tuple[str, ...] = ("image", "video", "llm", "tts", "stt")
FieldType = Literal["str", "secret", "int", "bool", "enum", "url"]
HealthStatus = Literal["alive", "dead", "not_configured", "unknown"]

__all__ = [
    "KINDS", "Adapter", "CapabilityError", "ConfigField", "Connection", "Health", "ImageAdapter", "ImageRequest",
    "ImageResult", "JobUnknown", "LLMAdapter", "LLMRequest", "LLMResult", "ProviderJobError", "ProviderUnavailable",
    "STTAdapter", "STTRequest", "TTSAdapter", "TTSRequest", "TTSResult", "Transcript", "TransientError",
    "VideoAdapter", "VideoRequest", "VideoResult",
]


# --- Lỗi chuẩn ------------------------------------------------------------------------------------------------------


class ProviderUnavailable(JobFailed):
    """Provider/kết nối không dùng được lúc này (tắt, chưa cấu hình, hết tài khoản…). Không tự thử lại."""

    def __init__(self, msg: str, hint: str | None = None) -> None:
        super().__init__("provider_unavailable", msg, hint)


class ProviderJobError(JobFailed):
    """Provider từ chối/thất bại có lý do rõ (`kind` ngắn, snake_case, vd. `rejected`, `content_policy`)."""

    def __init__(self, kind: str, msg: str, hint: str | None = None) -> None:
        super().__init__(f"provider_{kind}", msg, hint)
        self.kind = kind


class CapabilityError(JobFailed):
    """Yêu cầu vượt năng lực của provider (vd. 15 s cho provider chỉ làm 6 s, 9 ảnh tham chiếu cho provider 1 ảnh)."""

    def __init__(self, msg: str, hint: str | None = None) -> None:
        super().__init__("capability_mismatch", msg, hint)


class JobUnknown(NeedsAttention):
    """Không biết job phía provider đã được tạo hay chưa (vd. mất kết nối sau khi gửi) → người dùng quyết định.

    `code` do adapter đặt, dạng `<adapter>_job_unknown` (vd. `maivideo_job_unknown`).
    """

    def __init__(self, msg: str, hint: str | None = None, *, code: str = "provider_job_unknown") -> None:
        super().__init__(code, msg, hint)


# --- Cấu hình, kết nối, sức khoẻ ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigField:
    """Một trường trong form cấu hình kết nối.

    `name` là `base_url` / `api_key` / `default_model` (cột của kết nối) hoặc một khoá trong `options`.
    """

    name: str
    label: str
    type: FieldType
    required: bool
    default: Any = None
    choices: list | None = None
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Connection:
    """Một kết nối provider đọc từ DB (bản sao thuần, không gắn session). `api_key` không bao giờ hiện trong repr."""

    id: int
    name: str
    adapter: str
    kind: str
    base_url: str
    api_key: str = field(repr=False)
    default_model: str
    options: dict = field(default_factory=dict)
    concurrency: int = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(kw_only=True)
class Health:
    status: HealthStatus
    detail: str
    capabilities: dict = field(default_factory=dict)
    checked_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- Request / result -----------------------------------------------------------------------------------------------


@dataclass(kw_only=True)
class ImageRequest:
    prompt: str  # nguyên văn — adapter không được strip/chuẩn hoá
    refs: list[Path]
    aspect: str
    resolution: str
    model: str | None
    quality: str | None
    out_dir: Path


@dataclass(kw_only=True)
class ImageResult:
    paths: list[Path]
    meta: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class VideoRequest:
    image: Path
    prompt: str
    duration_s: int
    resolution: str
    aspect: str
    out_path: Path


@dataclass(kw_only=True)
class VideoResult:
    path: Path
    meta: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class LLMRequest:
    messages: list[dict]
    model: str | None
    params: dict = field(default_factory=dict)
    json_mode: bool = False
    images: list[Path] = field(default_factory=list)


@dataclass(kw_only=True)
class LLMResult:
    text: str
    model: str
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None  # "stop" | "length" (bị cắt vì hết token) | … — None nếu provider không báo


@dataclass(kw_only=True)
class TTSRequest:
    text: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    lang: str = "vi"
    out_path: Path
    word_timings: bool = True


@dataclass(kw_only=True)
class TTSResult:
    path: Path
    duration_ms: int
    words: list[tuple[str, int, int]] = field(default_factory=list)  # (từ, bắt đầu ms, kết thúc ms)


@dataclass(kw_only=True)
class STTRequest:
    audio: Path
    lang: str | None = None
    word_timestamps: bool = True


@dataclass(kw_only=True)
class Transcript:
    text: str
    words: list[tuple[str, int, int]] = field(default_factory=list)


# --- Adapter ----------------------------------------------------------------------------------------------------------


class Adapter(ABC):
    """Gốc của mọi adapter. Một instance phục vụ một kết nối (`self.conn`); dựng rẻ, không gọi mạng trong `__init__`.

    `http`: client dùng chung do người gọi đưa (test dùng `respx`); không có thì `client()` tự mở client tạm.
    """

    adapter_id: ClassVar[str]
    kind: ClassVar[str]
    label: ClassVar[str]
    config_fields: ClassVar[list[ConfigField]] = []
    default_capabilities: ClassVar[dict] = {}
    http_timeout: ClassVar[float] = 60.0

    def __init__(self, conn: Connection, http: httpx.AsyncClient | None = None) -> None:
        self.conn = conn
        self.http = http

    @abstractmethod
    async def check_health(self) -> Health:
        """Kiểm tra rẻ (không tốn tiền): `alive` / `dead` / `not_configured` (thiếu URL/khoá) / `unknown`."""

    async def estimate(self, req: Any) -> dict:
        """Ước tính chi phí của một request: số đơn vị (ảnh/video/giây…), tiền nếu biết."""
        return {"units": 1}

    def capabilities(self) -> dict:
        """Năng lực hiệu lực: `default_capabilities` của lớp, ghi đè bởi `options` cùng khoá của kết nối."""
        caps = dict(self.default_capabilities)
        for key in caps:
            if key in self.conn.options:
                caps[key] = self.conn.options[key]
        return caps

    @contextlib.asynccontextmanager
    async def client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Client HTTP: `self.http` nếu có (không đóng), ngược lại client tạm theo `base_url` của kết nối."""
        if self.http is not None:
            yield self.http
            return
        async with httpx.AsyncClient(base_url=self.conn.base_url, timeout=self.http_timeout) as c:
            yield c


class ImageAdapter(Adapter):
    kind: ClassVar[str] = "image"

    @abstractmethod
    async def generate(self, req: ImageRequest, run: JobRun | None) -> ImageResult:
        """Tạo ảnh vào `req.out_dir`. `run` (có thể None khi gọi trực tiếp) cho log/progress/save_external/cancelled."""


class VideoAdapter(Adapter):
    kind: ClassVar[str] = "video"

    @abstractmethod
    async def generate(self, req: VideoRequest, run: JobRun | None) -> VideoResult:
        """Tạo video tại `req.out_path`."""

    async def cancel(self, external_id: str) -> None:
        """Huỷ job phía provider (mặc định: không làm gì)."""
        return None


class LLMAdapter(Adapter):
    kind: ClassVar[str] = "llm"

    @abstractmethod
    async def chat(self, req: LLMRequest, on_delta: Callable[[str], None] | None = None) -> LLMResult:
        """Gọi chat; nếu `on_delta` có thì gọi với từng đoạn chữ theo thứ tự (nối lại == `text`)."""

    @abstractmethod
    async def list_models(self) -> list[str]: ...


class TTSAdapter(Adapter):
    kind: ClassVar[str] = "tts"

    @abstractmethod
    async def synthesize(self, req: TTSRequest, run: JobRun | None) -> TTSResult:
        """Đọc `req.text` ra `req.out_path`."""

    @abstractmethod
    async def voices(self, lang: str | None = None) -> list[dict]:
        """Danh sách giọng `{"id", "label", "lang", ...}`, lọc theo `lang` nếu có."""


class STTAdapter(Adapter):
    kind: ClassVar[str] = "stt"

    @abstractmethod
    async def transcribe(self, req: STTRequest, run: JobRun | None) -> Transcript: ...


BASE_CLASSES: tuple[type[Adapter], ...] = (ImageAdapter, VideoAdapter, LLMAdapter, TTSAdapter, STTAdapter)
