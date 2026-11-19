"""Thao tác nhóm `provider`: adapter, kết nối (CRUD, khoá API luôn bị che), xoá hai bước, kiểm tra sức khoẻ, chạy thử.

Mỗi `provider.try_*` tạo một job cùng tên trên lane của kết nối (`conn:<id>`) và trả `{"job_id"}`; `dry_run=True`
(ảnh/video/TTS) chỉ trả `{"estimate": {...}}`. Op ném `AppError` (không ném lỗi job); kết nối đang tắt vẫn tạo
job — job thất bại rõ ràng với `provider_unavailable` (handler ở `services/provider_jobs.py`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Conflict, ValidationFailed
from sagareel.core.ops import op
from sagareel.core.secrets import mask
from sagareel.db.base import utcnow
from sagareel.db.models import Job, ProviderConnection
from sagareel.providers import registry
from sagareel.providers.base import (
    Adapter,
    ConfigField,
    Connection,
    ImageRequest,
    LLMRequest,
    STTRequest,
    TTSRequest,
    VideoRequest,
)
from sagareel.services import budget, confirm, jobs, media
from sagareel.services import provider_jobs as pj
from sagareel.services.providers import ProviderService, get_row
from sagareel.services.settings import global_layer

Kind = Literal["image", "video", "llm", "tts", "stt"]
COLUMN_FIELDS = ("base_url", "api_key", "default_model")
OP_DELETE = "provider.delete"
_DRY_DIR = Path("dry_run")  # đường dẫn giả cho request ước tính (không ghi gì)


def _ps(ctx: OpContext) -> ProviderService:
    return pj.provider_service(ctx.services)


def conn_dict(row: ProviderConnection) -> dict[str, Any]:
    """Hình JSON của kết nối. Khoá API chỉ trả dạng che (`••••` + 4 ký tự cuối)."""
    return {
        "id": row.id,
        "kind": row.kind,
        "adapter": row.adapter,
        "name": row.name,
        "base_url": row.base_url,
        "api_key": mask(row.api_key or ""),
        "has_api_key": bool(row.api_key),
        "default_model": row.default_model,
        "options": dict(row.options or {}),
        "concurrency": row.concurrency,
        "enabled": row.enabled,
        "health": dict(row.health or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _adapter_cls(adapter_id: str, kind: str) -> type[Adapter]:
    cls = registry.get(adapter_id)
    if cls.kind != kind:
        raise AppError(
            "adapter_kind_mismatch",
            f"Adapter '{adapter_id}' thuộc loại {cls.kind}, không phải {kind}.",
            hint="Gọi provider.adapters để xem loại của từng adapter.",
            status=422,
        )
    return cls


_TYPE_CHECKS = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "str": lambda v: True,  # trường "str" có thể mang dữ liệu có cấu trúc (vd. replies của fake_llm)
    "url": lambda v: isinstance(v, str),
    "secret": lambda v: isinstance(v, str),
    "enum": lambda v: True,
}


def _check_options(cls: type[Adapter], options: dict[str, Any]) -> None:
    """Kiểm tra kiểu/lựa chọn các khoá `options` mà adapter khai báo trong `config_fields`; khoá lạ được giữ."""
    fields: dict[str, ConfigField] = {f.name: f for f in cls.config_fields if f.name not in COLUMN_FIELDS}
    errors = []
    for key, value in options.items():
        f = fields.get(key)
        if f is None or value is None:
            continue
        if not _TYPE_CHECKS[f.type](value) or (f.type == "enum" and f.choices and value not in f.choices):
            allowed = f" (một trong {f.choices})" if f.choices else ""
            errors.append({"loc": ["options", key], "msg": f"'{f.label}' phải có kiểu {f.type}{allowed}.",
                           "type": "option_invalid"})
    if errors:
        raise ValidationFailed("Tuỳ chọn kết nối không hợp lệ.", hint="Xem `extra.errors`.", extra={"errors": errors})


def _check_name_free(s: Session, name: str, exclude_id: int | None = None) -> None:
    q = select(ProviderConnection).where(ProviderConnection.name == name)
    if exclude_id is not None:
        q = q.where(ProviderConnection.id != exclude_id)
    other = s.scalars(q).first()
    if other is None:
        return
    if other.deleted_at is not None:
        raise Conflict(
            f"Tên '{name}' đang thuộc kết nối #{other.id} đã xoá.",
            hint="Chọn tên khác, hoặc hoàn tác lần xoá (history.undo) để dùng lại kết nối cũ.",
        )
    raise Conflict(f"Đã có kết nối tên '{name}'.", hint="Chọn tên khác.")


def _blank(name: str) -> str:
    if not name.strip():
        raise ValidationFailed("Tên kết nối không được để trống.",
                               extra={"errors": [{"loc": ["name"], "msg": "Tên trống", "type": "value_error"}]})
    return name


# --- Adapter ------------------------------------------------------------------------------------------------------


class EmptyParams(BaseModel):
    pass


@op("provider.adapters", summary="Danh sách adapter provider (loại, nhãn, trường cấu hình, năng lực mặc định)",
    params=EmptyParams, read_only=True, idempotent=True, mcp="tool")
def provider_adapters(ctx: OpContext, p: EmptyParams) -> dict:
    return {
        "items": [
            {
                "id": cls.adapter_id,
                "kind": cls.kind,
                "label": cls.label,
                "config_fields": [f.to_dict() for f in cls.config_fields],
                "default_capabilities": dict(cls.default_capabilities),
            }
            for cls in registry.all()
        ]
    }


# --- Đọc kết nối ----------------------------------------------------------------------------------------------------


class ProviderListParams(BaseModel):
    kind: Kind | None = None
    include_disabled: bool = True


@op("provider.list", summary="Danh sách kết nối provider (khoá API bị che) kèm sức khoẻ lần kiểm tra gần nhất",
    params=ProviderListParams, read_only=True, idempotent=True, viewer=True, mcp="tool")
def provider_list(ctx: OpContext, p: ProviderListParams) -> dict:
    q = select(ProviderConnection).where(ProviderConnection.deleted_at.is_(None))
    if p.kind is not None:
        q = q.where(ProviderConnection.kind == p.kind)
    if not p.include_disabled:
        q = q.where(ProviderConnection.enabled.is_(True))
    with ctx.services.db.session() as s:
        rows = s.scalars(q.order_by(ProviderConnection.kind, ProviderConnection.id)).all()
        return {"items": [conn_dict(r) for r in rows]}


class ProviderIdParams(BaseModel):
    id: int


@op("provider.get", summary="Chi tiết một kết nối provider (khoá API bị che)", params=ProviderIdParams,
    read_only=True, idempotent=True)
def provider_get(ctx: OpContext, p: ProviderIdParams) -> dict:
    with ctx.services.db.session() as s:
        return conn_dict(get_row(s, p.id))


# --- Ghi kết nối ----------------------------------------------------------------------------------------------------


class ProviderCreateParams(BaseModel):
    kind: Kind
    adapter: str
    name: str = Field(min_length=1, max_length=80)
    base_url: str = ""
    api_key: str = Field(default="", description="Bí mật: lưu nguyên, không bao giờ trả lại (chỉ dạng che)")
    default_model: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = Field(default=2, ge=1, le=32)
    enabled: bool = True


@op("provider.create", summary="Thêm kết nối provider", params=ProviderCreateParams)
def provider_create(ctx: OpContext, p: ProviderCreateParams) -> dict:
    cls = _adapter_cls(p.adapter, p.kind)
    _blank(p.name)
    _check_options(cls, p.options)
    with ctx.uow(f"Thêm kết nối {p.name}") as s:
        _check_name_free(s, p.name)
        row = ProviderConnection(
            kind=p.kind, adapter=p.adapter, name=p.name, base_url=p.base_url, api_key=p.api_key,
            default_model=p.default_model, options=dict(p.options), concurrency=p.concurrency, enabled=p.enabled,
            health={},
        )
        s.add(row)
        s.flush()
        return conn_dict(row)


class ProviderUpdateFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = None
    api_key: str | None = Field(default=None, description="Rỗng hoặc null = giữ nguyên khoá hiện có")
    default_model: str | None = None
    options: dict[str, Any] | None = Field(default=None, description="Thay toàn bộ options")
    concurrency: int | None = Field(default=None, ge=1, le=32)
    enabled: bool | None = None


class ProviderUpdateParams(BaseModel):
    id: int
    fields: ProviderUpdateFields


@op("provider.update", summary="Sửa kết nối provider (api_key rỗng/null = giữ nguyên)", params=ProviderUpdateParams,
    idempotent=True)
def provider_update(ctx: OpContext, p: ProviderUpdateParams) -> dict:
    values = {k: v for k, v in p.fields.model_dump(exclude_unset=True).items() if v is not None}
    if values.get("api_key") == "":
        del values["api_key"]
    if "name" in values:
        _blank(values["name"])
    with ctx.uow("Sửa kết nối provider") as s:
        row = get_row(s, p.id)
        if "options" in values:
            _check_options(registry.get(row.adapter), values["options"])
        if "name" in values and values["name"] != row.name:
            _check_name_free(s, values["name"], exclude_id=row.id)
        for key, value in values.items():
            setattr(row, key, value)
        s.flush()
        return conn_dict(row)


# --- Xoá hai bước -----------------------------------------------------------------------------------------------------


def _impact(ctx: OpContext, s: Session, row: ProviderConnection) -> dict[str, Any]:
    active = s.scalar(
        select(func.count()).select_from(Job).where(Job.lane == f"conn:{row.id}", Job.status.not_in(jobs.TERMINAL))
    )
    layer = global_layer(s)
    resolver = ctx.services.resolver
    used_by = [k for k in resolver.schema if k.endswith(".connection")
               and resolver.resolve(k, [layer]).value == row.name]
    return {
        "connection": {"id": row.id, "name": row.name, "kind": row.kind, "adapter": row.adapter},
        "active_jobs": int(active or 0),
        "used_by_settings": used_by,
    }


@op("provider.delete_preview", summary="Xem tác động của việc xoá kết nối và lấy confirm_token (10 phút)",
    params=ProviderIdParams, read_only=True, idempotent=True, viewer=False)
def provider_delete_preview(ctx: OpContext, p: ProviderIdParams) -> dict:
    with ctx.services.db.session() as s:
        row = get_row(s, p.id)
        impact = _impact(ctx, s, row)
        token = confirm.make_token(s, OP_DELETE, [row.id], impact)
    return {"impact": impact, "confirm_token": token, "expires_in_s": confirm.TTL_S}


class ProviderDeleteParams(BaseModel):
    id: int
    confirm_token: str | None = Field(default=None, description="Lấy từ provider.delete_preview")


@op("provider.delete", summary="Xoá (mềm) kết nối provider — cần confirm_token từ provider.delete_preview",
    params=ProviderDeleteParams, destructive=True)
def provider_delete(ctx: OpContext, p: ProviderDeleteParams) -> dict:
    with ctx.uow("Xoá kết nối provider") as s:
        row = get_row(s, p.id)
        confirm.verify(s, OP_DELETE, [row.id], _impact(ctx, s, row), p.confirm_token)
        row.deleted_at = utcnow()
        name = row.name
    return {"deleted": p.id, "name": name}


# --- Sức khoẻ ---------------------------------------------------------------------------------------------------------


class ProviderCheckParams(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=200, description="Bỏ trống = mọi kết nối đang bật")


@op("provider.check", summary="Kiểm tra sức khoẻ kết nối provider (mặc định mọi kết nối đang bật); ghi kết quả",
    params=ProviderCheckParams, idempotent=True, mcp="tool")
async def provider_check(ctx: OpContext, p: ProviderCheckParams) -> dict:
    def targets() -> list[tuple[int, str]]:
        with ctx.services.db.session() as s:
            if p.ids is None:
                rows = s.scalars(select(ProviderConnection).where(
                    ProviderConnection.deleted_at.is_(None), ProviderConnection.enabled.is_(True)
                ).order_by(ProviderConnection.id)).all()
            else:
                rows = [get_row(s, i) for i in dict.fromkeys(p.ids)]
            return [(r.id, r.name) for r in rows]

    ps = _ps(ctx)
    items = await anyio.to_thread.run_sync(targets)
    healths = await asyncio.gather(*(ps.check(cid) for cid, _ in items))
    return {"items": [{"id": cid, "name": name, "health": h.to_dict()} for (cid, name), h in zip(items, healths,
                                                                                                    strict=True)]}


# --- Chạy thử ---------------------------------------------------------------------------------------------------------

_DEFAULT_KEYS = {"image": "image.connection", "video": "video.connection", "tts": "tts.connection"}
STT_NOT_CONFIGURED_HINT = "Thêm kết nối STT qua API trong Settings, hoặc dán lời thoại thủ công"


def _target(ctx: OpContext, kind: str, connection: int | str | None) -> tuple[Connection, bool, Adapter]:
    """(kết nối, đang bật, adapter) cho op chạy thử. Không tìm thấy → 404; sai loại → `capability_mismatch`."""
    ps = _ps(ctx)
    if connection is None:
        key = _DEFAULT_KEYS.get(kind)
        connection = ps.setting(key) if key else None
        if connection is None:
            with ctx.services.db.session() as s:
                connection = s.scalar(select(ProviderConnection.id).where(
                    ProviderConnection.kind == kind, ProviderConnection.deleted_at.is_(None),
                    ProviderConnection.enabled.is_(True),
                ).order_by(ProviderConnection.id).limit(1))
        if connection is None and kind == "stt":
            raise AppError("stt_not_configured", "Chưa có kết nối nhận dạng lời (STT) nào đang bật.",
                           hint=STT_NOT_CONFIGURED_HINT, status=409)
        if connection is None:
            raise AppError("no_connection", f"Chưa có kết nối {kind} nào.",
                           hint="Thêm kết nối trong Cài đặt → Provider (provider.create).", status=422)
    conn, enabled = ps.find(connection)
    if conn.kind != kind:
        raise AppError(
            "capability_mismatch",
            f"Kết nối '{conn.name}' là loại {conn.kind}, không dùng để chạy thử {kind} được.",
            hint=f"Chọn một kết nối loại {kind} (provider.list kind={kind}).",
            status=422,
        )
    return conn, enabled, registry.get(conn.adapter)(conn)


def _require_capability(adapter: Adapter, key: str, value: Any, label: str) -> None:
    allowed = adapter.capabilities().get(key)
    if isinstance(allowed, list) and value not in allowed:
        raise AppError(
            "capability_mismatch",
            f"Kết nối '{adapter.conn.name}' không hỗ trợ {label} {value}.",
            hint=f"Chọn một trong: {', '.join(map(str, allowed))}.",
            status=422,
            extra={"capability": key, "allowed": allowed},
        )


async def _dispatch(
    ctx: OpContext, job_type: str, conn: Connection, enabled: bool, adapter: Adapter, req: Any, params: dict,
    *, dry_run: bool, budget_kind: str | None,
) -> dict:
    estimate = {**await adapter.estimate(req), "kind": adapter.kind, "connection": conn.name,
                "connection_id": conn.id, "adapter": adapter.adapter_id, "enabled": enabled}

    def run() -> dict:
        if budget_kind is not None:
            with ctx.services.db.session() as s:
                if dry_run:
                    estimate["budget"] = budget.status(s, budget_kind, resolver=ctx.services.resolver)
                else:  # chặn sớm khi đã biết vượt ngân sách (job kiểm tra lại trước khi gọi provider)
                    budget.check(s, budget_kind, estimate.get("units", 1), conn.id, resolver=ctx.services.resolver)
        if dry_run:
            return {"estimate": estimate}
        attempts = {"max_attempts": pj.MAX_ATTEMPTS[job_type]} if job_type in pj.MAX_ATTEMPTS else {}
        data = jobs.submit(ctx.services, job_type, {"connection_id": conn.id, **params},
                           lane=ProviderService.lane(conn), project_id=ctx.project_id, cost_estimate=estimate,
                           **attempts)
        return {"job_id": data["id"]}

    return await anyio.to_thread.run_sync(run)


_TRY = {"costly": True, "long_running": True, "mcp": "tool"}
_CONNECTION = Field(default=None, description="Tên hoặc id kết nối; bỏ trống = kết nối mặc định trong Cài đặt")


class TryImageParams(BaseModel):
    connection: int | str | None = _CONNECTION
    prompt: str = Field(min_length=1, description="Gửi nguyên văn tới provider")
    aspect: str | None = None
    resolution: str | None = None
    model: str | None = None
    dry_run: bool = False


@op("provider.try_image", summary="Chạy thử tạo ảnh trên một kết nối (job; dry_run chỉ ước tính)",
    params=TryImageParams, **_TRY)
async def provider_try_image(ctx: OpContext, p: TryImageParams) -> dict:
    def prepare() -> tuple:
        conn, enabled, adapter = _target(ctx, "image", p.connection)
        ps = _ps(ctx)
        aspect = p.aspect or ps.setting("image.aspect")
        resolution = p.resolution or ps.setting("image.resolution")
        _require_capability(adapter, "aspects", aspect, "tỉ lệ")
        return conn, enabled, adapter, aspect, resolution

    conn, enabled, adapter, aspect, resolution = await anyio.to_thread.run_sync(prepare)
    model = p.model or conn.default_model or None
    req = ImageRequest(prompt=p.prompt, refs=[], aspect=aspect, resolution=resolution, model=model, quality=None,
                       out_dir=_DRY_DIR)
    params = {"prompt": p.prompt, "aspect": aspect, "resolution": resolution, "model": model}
    return await _dispatch(ctx, pj.TRY_IMAGE, conn, enabled, adapter, req, params, dry_run=p.dry_run,
                           budget_kind="image")


def _nearest_aspect(ctx: OpContext, width: int | None, height: int | None) -> str:
    """Tỉ lệ trong `image.aspect` (choices của schema) gần nhất với kích thước ảnh."""
    d = ctx.services.resolver.definition("image.aspect")
    if not width or not height:
        return str(d.default)

    def ratio(a: str) -> float:
        w, h = a.split(":")
        return float(w) / float(h)

    return min(d.choices or [d.default], key=lambda a: abs(ratio(a) - width / height))


def _media_info(ctx: OpContext, media_id: int, kind: str) -> tuple[Path, int | None, int | None]:
    with ctx.services.db.session() as s:
        m = media.get(s, media_id)
        if m.kind != kind:
            raise AppError("media_kind_mismatch", f"Media #{media_id} là {m.kind}, cần {kind}.", status=422)
        return Path(m.path), m.width, m.height


class TryVideoParams(BaseModel):
    connection: int | str | None = _CONNECTION
    image_media_id: int
    prompt: str = Field(min_length=1, description="Gửi nguyên văn tới provider")
    duration: int | None = Field(default=None, description="Giây; mặc định theo cài đặt video.duration")
    resolution: str | None = None
    dry_run: bool = False


@op("provider.try_video", summary="Chạy thử tạo video từ một ảnh trên một kết nối (job; dry_run chỉ ước tính)",
    params=TryVideoParams, **_TRY)
async def provider_try_video(ctx: OpContext, p: TryVideoParams) -> dict:
    def prepare() -> tuple:
        conn, enabled, adapter = _target(ctx, "video", p.connection)
        _, w, h = _media_info(ctx, p.image_media_id, "image")
        ps = _ps(ctx)
        duration = p.duration if p.duration is not None else int(ps.setting("video.duration"))
        resolution = p.resolution or ps.setting("video.resolution")
        _require_capability(adapter, "durations", duration, "thời lượng")
        _require_capability(adapter, "resolutions", resolution, "độ phân giải")
        return conn, enabled, adapter, duration, resolution, _nearest_aspect(ctx, w, h)

    conn, enabled, adapter, duration, resolution, aspect = await anyio.to_thread.run_sync(prepare)
    req = VideoRequest(image=_DRY_DIR / "image", prompt=p.prompt, duration_s=duration, resolution=resolution,
                       aspect=aspect, out_path=_DRY_DIR / "video.mp4")
    params = {"image_media_id": p.image_media_id, "prompt": p.prompt, "duration": duration,
              "resolution": resolution, "aspect": aspect}
    return await _dispatch(ctx, pj.TRY_VIDEO, conn, enabled, adapter, req, params, dry_run=p.dry_run,
                           budget_kind="video")


class TryTTSParams(BaseModel):
    connection: int | str | None = _CONNECTION
    text: str = Field(min_length=1)
    voice: str | None = Field(default=None, description="Mặc định theo cài đặt tts.voice")
    dry_run: bool = False


@op("provider.try_tts", summary="Chạy thử đọc một câu trên một kết nối TTS (job; dry_run chỉ ước tính)",
    params=TryTTSParams, **_TRY)
async def provider_try_tts(ctx: OpContext, p: TryTTSParams) -> dict:
    def prepare() -> tuple:
        conn, enabled, adapter = _target(ctx, "tts", p.connection)
        return conn, enabled, adapter, p.voice or _ps(ctx).setting("tts.voice")

    conn, enabled, adapter, voice = await anyio.to_thread.run_sync(prepare)
    req = TTSRequest(text=p.text, voice=voice, out_path=_DRY_DIR / "tts.mp3")
    return await _dispatch(ctx, pj.TRY_TTS, conn, enabled, adapter, req, {"text": p.text, "voice": voice},
                           dry_run=p.dry_run, budget_kind=None)


class TrySTTParams(BaseModel):
    connection: int | str | None = _CONNECTION
    audio_media_id: int
    lang: str | None = None


@op("provider.try_stt", summary="Chạy thử nhận dạng lời từ một file âm thanh trên một kết nối STT (job)",
    params=TrySTTParams, **_TRY)
async def provider_try_stt(ctx: OpContext, p: TrySTTParams) -> dict:
    def prepare() -> tuple:
        conn, enabled, adapter = _target(ctx, "stt", p.connection)
        _media_info(ctx, p.audio_media_id, "audio")
        return conn, enabled, adapter

    conn, enabled, adapter = await anyio.to_thread.run_sync(prepare)
    req = STTRequest(audio=_DRY_DIR / "audio", lang=p.lang, word_timestamps=True)
    return await _dispatch(ctx, pj.TRY_STT, conn, enabled, adapter, req,
                           {"audio_media_id": p.audio_media_id, "lang": p.lang}, dry_run=False, budget_kind=None)


class TryLLMParams(BaseModel):
    connection: int | str | None = _CONNECTION
    model: str | None = None
    prompt: str = Field(min_length=1, description="Gửi nguyên văn tới LLM")
    image_media_id: int | None = None


@op("provider.try_llm", summary="Chạy thử một câu hỏi tới LLM (job; có thể kèm một ảnh)", params=TryLLMParams,
    **_TRY)
async def provider_try_llm(ctx: OpContext, p: TryLLMParams) -> dict:
    def prepare() -> tuple:
        conn, enabled, adapter = _target(ctx, "llm", p.connection)
        if p.image_media_id is not None:
            _media_info(ctx, p.image_media_id, "image")
        return conn, enabled, adapter

    conn, enabled, adapter = await anyio.to_thread.run_sync(prepare)
    model = p.model or conn.default_model or None
    req = LLMRequest(messages=[{"role": "user", "content": p.prompt}], model=model, params={}, json_mode=False,
                     images=[_DRY_DIR / "image"] if p.image_media_id is not None else [])
    return await _dispatch(ctx, pj.TRY_LLM, conn, enabled, adapter, req,
                           {"prompt": p.prompt, "model": model, "image_media_id": p.image_media_id},
                           dry_run=False, budget_kind=None)

