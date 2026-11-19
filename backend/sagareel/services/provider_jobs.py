"""Handler job "chạy thử" provider (`provider.try_*`), tạo bởi các op cùng tên trong `ops/provider_ops.py`.

`params` của job chỉ chứa `connection_id` (không bao giờ có khoá API) và các trường của request. Thứ tự trong handler
tạo media (ràng buộc UnitOfWork/khoá ghi SQLite):
1. một phiên ngắn giữ khoá ghi: giữ chỗ ngân sách (`budget.reserve` — kiểm tra trần + chèn dòng `spend` của job);
   bỏ qua khi provider có thể đã nhận job (`external_id`/`external_state.state`, vd. nối lại sau khi tắt app);
2. gọi provider — chỉ trả chỗ (`budget.release`) khi chắc chắn provider không nhận (kể cả người dùng huỷ); tắt app
   hoặc lỗi sau khi provider đã/có thể đã nhận → giữ chỗ (xem `_paid`);
3. `media.prepare` (băm/probe/đặt file) — **ngoài** mọi transaction;
4. một phiên ngắn giữ khoá ghi: `media.record` + `budget.finalize`;
5. `media.reconcile` sau commit.

Mọi handler đi qua `provider_job(...)`: lỗi nào thoát ra cũng được che khoá API của kết nối trước khi thành
`job.error_message`/log (lỗi không lường trước được thay bằng `ProviderCrash` mang thông báo đã che).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio

from sagareel.providers.base import (
    Adapter,
    CapabilityError,
    ImageRequest,
    LLMRequest,
    STTRequest,
    TTSRequest,
    VideoRequest,
)
from sagareel.core.errors import AppError
from sagareel.services import budget, media
from sagareel.services.jobs import JobFailed, NeedsAttention, TransientError, job_handler
from sagareel.services.providers import ProviderService, redact_secret

if TYPE_CHECKING:
    from sagareel.container import Services
    from sagareel.services.worker import JobRun

TRY_IMAGE = "provider.try_image"
TRY_VIDEO = "provider.try_video"
TRY_TTS = "provider.try_tts"
TRY_STT = "provider.try_stt"
TRY_LLM = "provider.try_llm"
# Số lần thử theo loại job (thiếu → mặc định của hàng đợi). TTS: edge-tts NoAudioReceived thường hết sau vài phút;
# backoff 20·2^n ≤ 300 s cho 6 lần ≈ 20+40+80+160+300 s ≈ 10 phút.
MAX_ATTEMPTS: dict[str, int] = {TRY_TTS: 6}

logger = logging.getLogger("sagareel.providers")


class ProviderCrash(Exception):
    """Lỗi không lường trước trong job provider, thông báo đã che khoá API (thay cho lỗi gốc)."""


def provider_service(services: Services) -> ProviderService:
    return getattr(services, "providers", None) or ProviderService(services)


def _plain(value: Any) -> Any:
    """Bản JSON thuần (Path → str…) để lưu vào `job.result` / `media.meta`."""
    return json.loads(json.dumps(value, default=str))


async def _adapter(run: JobRun, kind: str) -> Adapter:
    adapter = await anyio.to_thread.run_sync(provider_service(run.services).adapter, run.params["connection_id"])
    if adapter.kind != kind:
        raise CapabilityError(f"Kết nối '{adapter.conn.name}' là loại {adapter.kind}, không tạo được {kind}.")
    return adapter


def _origin(adapter: Adapter) -> str:
    return "fake" if adapter.adapter_id.startswith(budget.FAKE_PREFIX) else "generated"


def _media_path(services: Services, media_id: int, kind: str) -> Path:
    with services.db.session() as s:
        m = media.get(s, media_id)
        if m.kind != kind:
            raise CapabilityError(f"Media #{media_id} là {m.kind}, cần {kind}.")
        return media.file_path(services.settings, m)


def _secret_of(services: Services, connection_id: Any) -> str:
    try:
        return provider_service(services).find(connection_id)[0].api_key
    except Exception:  # noqa: BLE001 — không đọc được thì không có gì để che
        return ""


def _redacted(e: Exception, secret: str) -> Exception:
    """Che `secret` trong lỗi: lỗi chuẩn được sửa tại chỗ; lỗi lạ → `ProviderCrash` (traceback đã che vào log)."""
    def r(text: str | None) -> str | None:
        return redact_secret(text, secret) if text is not None else None

    if isinstance(e, (JobFailed, NeedsAttention, TransientError)):
        e.msg = r(e.msg)
        if hasattr(e, "hint"):
            e.hint = r(e.hint)
        e.args = (e.msg,)
        return e
    if isinstance(e, AppError):
        e.detail, e.hint = r(e.detail), r(e.hint)
        e.args = (e.detail,)
        return e
    logger.error("Job provider lỗi không lường trước:\n%s", r("".join(traceback.format_exception(e))))
    return ProviderCrash(f"{type(e).__name__}: {r(str(e))}")


def provider_job(
    type_: str, *, resumable: bool = False
) -> Callable[[Callable[[JobRun], Awaitable[dict]]], Callable[[JobRun], Awaitable[dict]]]:
    """`job_handler(type_, resumable=...)` kèm che khoá API của kết nối (`params.connection_id`) trong mọi lỗi thoát ra.

    `resumable=True` cho handler mà adapter nối lại được từ `external_id`/dấu `submitting` mà không POST lại.
    """

    def decorator(fn: Callable[[JobRun], Awaitable[dict]]) -> Callable[[JobRun], Awaitable[dict]]:
        @functools.wraps(fn)
        async def wrapper(run: JobRun) -> dict:
            try:
                return await fn(run)
            except Exception as e:
                secret = await anyio.to_thread.run_sync(_secret_of, run.services, run.params.get("connection_id"))
                if not secret:
                    raise
                out = _redacted(e, secret)
                if out is e:
                    raise
                raise out from None

        job_handler(type_, resumable=resumable)(wrapper)
        return fn

    return decorator


def _reserve(services: Services, kind: str, units: float, job_id: int, connection_id: int) -> None:
    with services.db.session(immediate=True) as s:  # kiểm tra + giữ chỗ trong cùng khoá ghi
        budget.reserve(s, kind, units, job_id, connection_id, resolver=services.resolver)


def _release(services: Services, job_id: int) -> None:
    with services.db.session(immediate=True) as s:
        budget.release(s, job_id)


def provider_may_hold_job(run: JobRun) -> bool:
    """Provider có thể đã nhận (và tính tiền) job này: có `external_id` hoặc `external_state.state` khác rỗng
    (`submitting` — không rõ; `submitted`). Dấu đã xoá (`state=None`) = lần gửi gần nhất chắc chắn không được nhận."""
    return bool(run.external_id) or bool((run.external or {}).get("state"))


async def _settle(fut: asyncio.Future) -> None:
    """Chờ `fut` xong hẳn (kể cả khi bị huỷ thêm lần nữa); bỏ qua kết quả/lỗi của nó."""
    while not fut.done():
        with contextlib.suppress(BaseException):
            await asyncio.shield(fut)
    if not fut.cancelled():
        fut.exception()  # đã xem lỗi (nếu có): không cảnh báo "exception was never retrieved"


async def _paid(run: JobRun, adapter: Adapter, kind: str, units: float, generate: Callable[[], Awaitable[Any]]) -> Any:
    """Giữ chỗ ngân sách → gọi provider. Không bao giờ để tiền đã tiêu ra khỏi sổ:

    - provider có thể đã nhận job (`provider_may_hold_job`, vd. nối lại sau khi tắt app): không giữ chỗ lại (dòng cũ
      vẫn còn) và không bao giờ thất bại vì `budget_exceeded`;
    - chỉ trả chỗ khi chắc chắn provider không nhận (lỗi mà không có `external_id`/dấu `submitting`), kể cả khi người
      dùng huỷ; tắt app (`CancelledError` khi `run.shutting_down`, người dùng không huỷ) không trả chỗ — nối lại sau;
    - giữ chỗ chạy trong luồng riêng: bị huỷ giữa chừng thì chờ luồng ghi xong rồi mới trả (không sót dòng).
    """
    reserving: asyncio.Future | None = None
    try:
        if not provider_may_hold_job(run):
            reserving = asyncio.ensure_future(
                anyio.to_thread.run_sync(_reserve, run.services, kind, units, run.job_id, adapter.conn.id))
            await asyncio.shield(reserving)
        return await generate()
    except BaseException as e:
        if reserving is not None:
            await _settle(reserving)
        # Như worker: CancelledError khi worker đang tắt (và người dùng không huỷ) = tắt app → giữ chỗ; mọi CancelledError
        # khác (người dùng huỷ, handler tự ném) mà provider chưa nhận → trả chỗ.
        shutdown = isinstance(e, asyncio.CancelledError) and run.shutting_down and not run.cancelled.is_set()
        if not shutdown and not provider_may_hold_job(run):
            with contextlib.suppress(Exception):
                await asyncio.shield(anyio.to_thread.run_sync(_release, run.services, run.job_id))
        raise


async def _store(
    run: JobRun,
    adapter: Adapter,
    paths: list[Path],
    *,
    kind: str,
    meta: dict,
    spend_kind: str | None,
) -> list[int]:
    """prepare (ngoài transaction) → record + spend (phiên ngắn) → reconcile. Trả id media."""
    settings = run.services.settings
    prepared = []
    for p in paths:
        prepared.append(await anyio.to_thread.run_sync(functools.partial(
            media.prepare, p, kind=kind, media_dir=settings.media_dir, origin=_origin(adapter), meta=meta,
            data_dir=settings.data_dir,
        )))

    def write() -> list:
        with run.services.db.session(immediate=True) as s:  # expire_on_commit=False: dùng được sau commit
            rows = [media.record(s, pr) for pr in prepared]
            if spend_kind is not None:
                budget.finalize(s, run.job_id, len(rows), kind=spend_kind, connection_id=adapter.conn.id)
            return rows

    rows = await anyio.to_thread.run_sync(write)
    for pr, row in zip(prepared, rows, strict=True):
        await anyio.to_thread.run_sync(media.reconcile, pr, row)
    return [row.id for row in rows]


@provider_job(TRY_IMAGE, resumable=True)
async def try_image(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "image")
    req = ImageRequest(
        prompt=p["prompt"], refs=[], aspect=p["aspect"], resolution=p["resolution"], model=p.get("model"),
        quality=p.get("quality"), out_dir=run.workdir / "out",
    )
    units = (await adapter.estimate(req)).get("units", 1)
    run.progress(0.0, stage="Đang tạo ảnh")
    result = await _paid(run, adapter, "image", units, lambda: adapter.generate(req, run))
    meta = _plain({"prompt": p["prompt"], "connection_id": adapter.conn.id, "adapter": adapter.adapter_id,
                   "job_id": run.job_id, "provider_meta": result.meta})
    ids = await _store(run, adapter, result.paths, kind="image", meta=meta, spend_kind="image")
    return {"media_ids": ids, "meta": _plain(result.meta), "connection_id": adapter.conn.id,
            "adapter": adapter.adapter_id}


@provider_job(TRY_VIDEO, resumable=True)
async def try_video(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "video")
    image = await anyio.to_thread.run_sync(_media_path, run.services, p["image_media_id"], "image")
    req = VideoRequest(
        image=image, prompt=p["prompt"], duration_s=int(p["duration"]), resolution=p["resolution"],
        aspect=p["aspect"], out_path=run.workdir / "video.mp4",
    )
    units = (await adapter.estimate(req)).get("units", 1)
    run.progress(0.0, stage="Đang tạo video")

    async def generate() -> Any:
        try:
            return await adapter.generate(req, run)
        except asyncio.CancelledError:
            # Chỉ khi NGƯỜI DÙNG huỷ mới báo provider huỷ job phía họ (không chờ lâu, không che việc huỷ). Tắt app thì
            # để job phía provider chạy tiếp: lần khởi động sau nối lại đúng job đó.
            if run.cancelled.is_set() and run.external_id:
                with contextlib.suppress(Exception):
                    await asyncio.shield(asyncio.wait_for(adapter.cancel(run.external_id), 10))
            raise

    result = await _paid(run, adapter, "video", units, generate)
    meta = _plain({"prompt": p["prompt"], "image_media_id": p["image_media_id"], "connection_id": adapter.conn.id,
                   "adapter": adapter.adapter_id, "job_id": run.job_id, "provider_meta": result.meta})
    ids = await _store(run, adapter, [result.path], kind="video", meta=meta, spend_kind="video")
    return {"media_ids": ids, "meta": _plain(result.meta), "connection_id": adapter.conn.id,
            "adapter": adapter.adapter_id}


@provider_job(TRY_TTS)
async def try_tts(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "tts")
    req = TTSRequest(text=p["text"], voice=p["voice"], out_path=run.workdir / "tts.mp3")
    run.progress(0.0, stage="Đang đọc")
    result = await adapter.synthesize(req, run)
    meta = _plain({"text": p["text"], "voice": p["voice"], "connection_id": adapter.conn.id,
                   "adapter": adapter.adapter_id, "job_id": run.job_id, "words": result.words})
    ids = await _store(run, adapter, [result.path], kind="audio", meta=meta, spend_kind=None)
    return {"media_ids": ids, "duration_ms": result.duration_ms, "words": _plain(result.words),
            "connection_id": adapter.conn.id, "adapter": adapter.adapter_id}


@provider_job(TRY_STT)
async def try_stt(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "stt")
    audio = await anyio.to_thread.run_sync(_media_path, run.services, p["audio_media_id"], "audio")
    result = await adapter.transcribe(STTRequest(audio=audio, lang=p.get("lang"), word_timestamps=True), run)
    return {"text": result.text, "words": _plain(result.words), "connection_id": adapter.conn.id,
            "adapter": adapter.adapter_id}


@provider_job(TRY_LLM)
async def try_llm(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "llm")
    images = []
    if p.get("image_media_id") is not None:
        images.append(await anyio.to_thread.run_sync(_media_path, run.services, p["image_media_id"], "image"))
    req = LLMRequest(messages=[{"role": "user", "content": p["prompt"]}], model=p.get("model"), params={},
                     json_mode=False, images=images)
    received = 0

    def on_delta(chunk: str) -> None:
        nonlocal received
        received += len(chunk)
        run.progress(None, stage=f"Đang nhận chữ ({received} ký tự)")

    result = await adapter.chat(req, on_delta)
    return {"text": result.text, "model": result.model, "usage": _plain(result.usage),
            "connection_id": adapter.conn.id, "adapter": adapter.adapter_id}
