"""Handler job "chạy thử" provider (`provider.try_*`), tạo bởi các op cùng tên trong `ops/provider_ops.py`.

`params` của job chỉ chứa `connection_id` (không bao giờ có khoá API) và các trường của request. Thứ tự trong handler
tạo media (ràng buộc UnitOfWork/khoá ghi SQLite):
1. phiên ngắn: kiểm tra ngân sách (`budget.check`, không ghi);
2. gọi provider + `media.prepare` (băm/probe/đặt file) — **ngoài** mọi transaction;
3. một phiên ngắn giữ khoá ghi: `media.record` + `budget.record`;
4. `media.reconcile` sau commit.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
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
from sagareel.services import budget, media
from sagareel.services.jobs import job_handler
from sagareel.services.providers import ProviderService

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


def _budget_precheck(services: Services, kind: str, units: float, connection_id: int) -> None:
    with services.db.session() as s:
        budget.check(s, kind, units, connection_id, resolver=services.resolver)


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
                budget.record(s, spend_kind, len(rows), run.job_id, adapter.conn.id)
            return rows

    rows = await anyio.to_thread.run_sync(write)
    for pr, row in zip(prepared, rows, strict=True):
        await anyio.to_thread.run_sync(media.reconcile, pr, row)
    return [row.id for row in rows]


@job_handler(TRY_IMAGE)
async def try_image(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "image")
    req = ImageRequest(
        prompt=p["prompt"], refs=[], aspect=p["aspect"], resolution=p["resolution"], model=p.get("model"),
        quality=p.get("quality"), out_dir=run.workdir / "out",
    )
    units = (await adapter.estimate(req)).get("units", 1)
    await anyio.to_thread.run_sync(_budget_precheck, run.services, "image", units, adapter.conn.id)
    run.progress(0.0, stage="Đang tạo ảnh")
    result = await adapter.generate(req, run)
    meta = _plain({"prompt": p["prompt"], "connection_id": adapter.conn.id, "adapter": adapter.adapter_id,
                   "job_id": run.job_id, "provider_meta": result.meta})
    ids = await _store(run, adapter, result.paths, kind="image", meta=meta, spend_kind="image")
    return {"media_ids": ids, "meta": _plain(result.meta), "connection_id": adapter.conn.id,
            "adapter": adapter.adapter_id}


@job_handler(TRY_VIDEO)
async def try_video(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "video")
    image = await anyio.to_thread.run_sync(_media_path, run.services, p["image_media_id"], "image")
    req = VideoRequest(
        image=image, prompt=p["prompt"], duration_s=int(p["duration"]), resolution=p["resolution"],
        aspect=p["aspect"], out_path=run.workdir / "video.mp4",
    )
    units = (await adapter.estimate(req)).get("units", 1)
    await anyio.to_thread.run_sync(_budget_precheck, run.services, "video", units, adapter.conn.id)
    run.progress(0.0, stage="Đang tạo video")
    try:
        result = await adapter.generate(req, run)
    except asyncio.CancelledError:
        if run.external_id:  # báo provider huỷ job phía họ (không chờ lâu, không che việc huỷ)
            with contextlib.suppress(Exception):
                await asyncio.shield(asyncio.wait_for(adapter.cancel(run.external_id), 10))
        raise
    meta = _plain({"prompt": p["prompt"], "image_media_id": p["image_media_id"], "connection_id": adapter.conn.id,
                   "adapter": adapter.adapter_id, "job_id": run.job_id, "provider_meta": result.meta})
    ids = await _store(run, adapter, [result.path], kind="video", meta=meta, spend_kind="video")
    return {"media_ids": ids, "meta": _plain(result.meta), "connection_id": adapter.conn.id,
            "adapter": adapter.adapter_id}


@job_handler(TRY_TTS)
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


@job_handler(TRY_STT)
async def try_stt(run: JobRun) -> dict:
    p = run.params
    adapter = await _adapter(run, "stt")
    audio = await anyio.to_thread.run_sync(_media_path, run.services, p["audio_media_id"], "audio")
    result = await adapter.transcribe(STTRequest(audio=audio, lang=p.get("lang"), word_timestamps=True), run)
    return {"text": result.text, "words": _plain(result.words), "connection_id": adapter.conn.id,
            "adapter": adapter.adapter_id}


@job_handler(TRY_LLM)
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
