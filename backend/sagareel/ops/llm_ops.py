"""Thao tác nhóm `llm` (công việc LLM, định tuyến, chạy) và `template` (template prompt sửa được).

- Sửa công việc/template đi qua UnitOfWork → có lịch sử, hoàn tác được (`history.undo`).
- `llm.run` kiểm tra cấu hình **trước** khi tạo job: chưa có LLM → `llm_not_configured` (409) kèm gợi ý tự viết /
  nhập file / agent ngoài qua MCP; thiếu biến template → `template_variable_missing` (422).
- Template `locked` không cho actor `llm:*` sửa/khôi phục (spec §8).
"""

from __future__ import annotations

import asyncio
from typing import Any

import anyio
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core import templating
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Conflict, Forbidden, NotFound, ValidationFailed
from sagareel.core.ops import op
from sagareel.core.secrets import mask
from sagareel.db.models import LlmTask, Template
from sagareel.providers import registry
from sagareel.services import llm
from sagareel.services.jobs import JobFailed, NeedsAttention, TransientError
from sagareel.services.providers import ProviderService

MODELS_TIMEOUT_S = 30.0
_KEY = r"^[a-z][a-z0-9_]*$"


# --- Hình JSON ------------------------------------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def task_dict(row: LlmTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.key,
        "label": row.label,
        "description": row.description,
        "template_key": row.template_key,
        "needs_vision": row.needs_vision,
        "json_mode": row.json_mode,
        "route": llm.normalize_route(row.route),
        "fallbacks": [llm.normalize_route(f) for f in (row.fallbacks or [])],
        "updated_at": _iso(row.updated_at),
    }


def _modified(row: Template) -> bool:
    return bool(row.builtin_hash) and llm.body_hash(row.body) != row.builtin_hash


def _variables(body: str) -> list[str] | None:
    try:
        return templating.variables_of(body)
    except AppError:
        return None


def template_dict(row: Template, *, body: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "key": row.key,
        "kind": row.kind,
        "locked": row.locked,
        "modified": _modified(row),
        "has_builtin": llm.builtin_template(row.key) is not None,
        "variables": _variables(row.body),
        "updated_at": _iso(row.updated_at),
    }
    if body:
        out["body"] = row.body
    return out


# --- Kiểm tra -------------------------------------------------------------------------------------------------------


class RouteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection: str | int | None = Field(default=None, description="Tên (hoặc id) kết nối LLM")
    model: str | None = Field(default=None, description="Bỏ trống = model mặc định của kết nối")
    params: dict[str, Any] = Field(default_factory=dict, description="Tham số gửi kèm, vd. temperature, max_tokens")


def _checked_route(s: Session, spec: RouteSpec | None) -> dict[str, Any]:
    """Route để lưu: kết nối phải tồn tại và là LLM; lưu theo tên kết nối."""
    if spec is None or spec.connection in (None, ""):
        return {"connection": None, "model": spec.model if spec else None, "params": dict(spec.params) if spec else {}}
    row = llm.find_connection(s, spec.connection)
    if row is None:
        raise NotFound(f"Không tìm thấy kết nối provider '{spec.connection}'.", code="connection_not_found",
                       hint="Gọi provider.list kind=llm để xem các kết nối LLM.")
    if row.kind != "llm":
        raise AppError("capability_mismatch", f"Kết nối '{row.name}' là loại {row.kind}, không phải LLM.",
                       hint="Chọn một kết nối loại llm (provider.list kind=llm).", status=422)
    return {"connection": row.name, "model": spec.model or None, "params": dict(spec.params)}


def _checked_fallbacks(s: Session, specs: list[RouteSpec]) -> list[dict[str, Any]]:
    """Mỗi route dự phòng phải chỉ rõ kết nối (không có thì 422, không lặng lẽ bỏ)."""
    bad = [i for i, f in enumerate(specs) if f.connection in (None, "")]
    if bad:
        raise ValidationFailed(
            "Route dự phòng phải có kết nối.",
            hint="Điền connection cho mọi route trong fallbacks (provider.list kind=llm).",
            extra={"errors": [{"loc": ["fallbacks", i, "connection"], "msg": "Thiếu kết nối", "type": "missing"}
                              for i in bad]},
        )
    return [_checked_route(s, f) for f in specs]


def _check_template_exists(s: Session, key: str) -> None:
    llm.get_template(s, key)


def _effective_routes(s: Session, key: str) -> list[dict]:
    try:
        return [r.to_dict() for r in llm.routes(s, key)]
    except AppError:
        return []


def _deny_locked_for_llm(ctx: OpContext, row: Template) -> None:
    if row.locked and ctx.actor.startswith("llm:"):
        raise Forbidden(f"Template '{row.key}' đang khoá, LLM không được sửa.", code="template_locked",
                        hint="Người dùng mở khoá template (locked=false) nếu muốn cho LLM sửa.")


# --- Công việc LLM --------------------------------------------------------------------------------------------------


class EmptyParams(BaseModel):
    pass


@op("llm.task_list", summary="Danh sách công việc LLM, định tuyến (kết nối + model + dự phòng) và route hiệu lực",
    params=EmptyParams, read_only=True, idempotent=True, viewer=True, mcp="tool")
def llm_task_list(ctx: OpContext, p: EmptyParams) -> dict:
    with ctx.services.db.session() as s:
        rows = s.scalars(select(LlmTask).where(LlmTask.deleted_at.is_(None)).order_by(LlmTask.id)).all()
        return {"items": [{**task_dict(r), "effective_routes": _effective_routes(s, r.key)} for r in rows]}


class TaskUpdateParams(BaseModel):
    key: str
    route: RouteSpec | None = Field(default=None, description="null = về mặc định (kết nối LLM đang bật đầu tiên)")
    fallbacks: list[RouteSpec] | None = Field(default=None, max_length=10, description="Thay toàn bộ chuỗi dự phòng")
    label: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    template_key: str | None = None


@op("llm.task_update", summary="Sửa công việc LLM: định tuyến, chuỗi dự phòng, nhãn, template (có hiệu lực ngay)",
    params=TaskUpdateParams, idempotent=True)
def llm_task_update(ctx: OpContext, p: TaskUpdateParams) -> dict:
    fields = p.model_fields_set
    with ctx.uow(f"Sửa công việc LLM {p.key}") as s:
        row = llm.get_task(s, p.key)
        if "route" in fields:
            row.route = _checked_route(s, p.route)
        if "fallbacks" in fields:
            row.fallbacks = _checked_fallbacks(s, p.fallbacks or [])
        if p.label is not None:
            row.label = p.label
        if p.description is not None:
            row.description = p.description
        if p.template_key is not None:
            _check_template_exists(s, p.template_key)
            row.template_key = p.template_key
        s.flush()
        return task_dict(row)


class TaskCreateParams(BaseModel):
    key: str = Field(pattern=_KEY, max_length=64, description="snake_case, duy nhất")
    label: str = Field(min_length=1, max_length=120)
    description: str = ""
    template_key: str
    needs_vision: bool = False
    json_mode: bool = False
    route: RouteSpec | None = None
    fallbacks: list[RouteSpec] = Field(default_factory=list, max_length=10)


@op("llm.task_create", summary="Thêm công việc LLM mới (dùng một template có sẵn)", params=TaskCreateParams)
def llm_task_create(ctx: OpContext, p: TaskCreateParams) -> dict:
    with ctx.uow(f"Thêm công việc LLM {p.key}") as s:
        other = s.scalars(select(LlmTask).where(LlmTask.key == p.key)).first()
        if other is not None:
            hint = ("Công việc này đã bị xoá; hoàn tác lần xoá (history.undo) để dùng lại." if other.deleted_at
                    else "Chọn key khác hoặc sửa công việc hiện có (llm.task_update).")
            raise Conflict(f"Đã có công việc LLM '{p.key}'.", hint=hint)
        _check_template_exists(s, p.template_key)
        row = LlmTask(key=p.key, label=p.label, description=p.description, template_key=p.template_key,
                      needs_vision=p.needs_vision, json_mode=p.json_mode, route=_checked_route(s, p.route),
                      fallbacks=_checked_fallbacks(s, p.fallbacks))
        s.add(row)
        s.flush()
        return task_dict(row)


class ModelsParams(BaseModel):
    connection: int | str = Field(description="Tên hoặc id kết nối LLM")


@op("llm.models", summary="Danh sách model của một kết nối LLM (hỏi provider, không tốn tiền)", params=ModelsParams,
    read_only=True, idempotent=True)
async def llm_models(ctx: OpContext, p: ModelsParams) -> dict:
    ps = ctx.services.providers or ProviderService(ctx.services)
    conn, _enabled = await anyio.to_thread.run_sync(ps.find, p.connection)
    if conn.kind != "llm":
        raise AppError("capability_mismatch", f"Kết nối '{conn.name}' là loại {conn.kind}, không phải LLM.",
                       hint="Chọn một kết nối loại llm.", status=422)
    adapter = registry.get(conn.adapter)(conn)
    try:
        models = await asyncio.wait_for(adapter.list_models(), MODELS_TIMEOUT_S)
    except TimeoutError:
        raise AppError("llm_models_failed", f"Hết thời gian chờ khi hỏi model của '{conn.name}'.", status=502,
                       hint="Kiểm tra kết nối (provider.check).") from None
    except (JobFailed, NeedsAttention, TransientError) as e:
        msg = e.msg.replace(conn.api_key, mask(conn.api_key)) if conn.api_key else e.msg
        raise AppError("llm_models_failed", f"Không lấy được danh sách model của '{conn.name}': {msg}",
                       status=502, hint="Kiểm tra địa chỉ API và khoá API của kết nối.") from None
    return {"connection": conn.name, "models": models}


class RunParams(BaseModel):
    task: str = Field(description="Key công việc LLM, vd. write_script")
    variables: dict[str, Any] = Field(default_factory=dict, description="Biến của template (thường có `input`)")
    override: RouteSpec | None = Field(default=None, description="Định tuyến riêng cho lần chạy này")
    images_media_ids: list[int] = Field(default_factory=list, max_length=16, description="Ảnh gửi kèm (vision)")


@op("llm.run", summary="Chạy một công việc LLM (job; stream llm.delta, kết quả {text, data, route, usage})",
    params=RunParams, costly=True, long_running=True, mcp="tool")
async def llm_run(ctx: OpContext, p: RunParams) -> dict:
    override = p.override.model_dump() if p.override is not None else None
    if override is not None and override.get("connection") is not None:
        override["connection"] = str(override["connection"])
    data = await anyio.to_thread.run_sync(lambda: llm.submit_run(
        ctx.services, p.task, dict(p.variables), override=override, images_media_ids=list(p.images_media_ids),
        project_id=ctx.project_id))
    return {"job_id": data["id"]}


# --- Template -------------------------------------------------------------------------------------------------------


class TemplateListParams(BaseModel):
    kind: str | None = None


@op("template.list", summary="Danh sách template prompt (không kèm nội dung; dùng template.get)",
    params=TemplateListParams, read_only=True, idempotent=True)
def template_list(ctx: OpContext, p: TemplateListParams) -> dict:
    q = select(Template).where(Template.deleted_at.is_(None))
    if p.kind is not None:
        q = q.where(Template.kind == p.kind)
    with ctx.services.db.session() as s:
        return {"items": [template_dict(r, body=False) for r in s.scalars(q.order_by(Template.key)).all()]}


class TemplateKeyParams(BaseModel):
    key: str


@op("template.get", summary="Nội dung một template prompt (kèm bản cài sẵn nếu có)", params=TemplateKeyParams,
    read_only=True, idempotent=True)
def template_get(ctx: OpContext, p: TemplateKeyParams) -> dict:
    with ctx.services.db.session() as s:
        row = llm.get_template(s, p.key)
        return {**template_dict(row), "builtin_body": llm.builtin_template(row.key)}


class TemplateUpdateParams(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.]*$", max_length=64)
    body: str = Field(description="Nội dung Jinja; lưu nguyên văn")
    locked: bool | None = Field(default=None, description="Khoá: LLM không được sửa")
    create_if_missing: bool = Field(default=False, description="Tạo template mới nếu chưa có key này")
    kind: str = Field(default=llm.TEMPLATE_KIND, description="Loại (chỉ dùng khi tạo mới)")


@op("template.update", summary="Sửa nội dung template prompt (create_if_missing để tạo mới); kiểm tra cú pháp",
    params=TemplateUpdateParams, idempotent=True)
def template_update(ctx: OpContext, p: TemplateUpdateParams) -> dict:
    templating.check_syntax(p.body)
    if p.locked is not None and ctx.actor.startswith("llm:"):
        raise Forbidden("LLM không được đổi trạng thái khoá của template.", code="template_locked")
    with ctx.uow(f"Sửa template {p.key}") as s:
        row = s.scalars(select(Template).where(Template.key == p.key)).first()
        if row is not None and row.deleted_at is not None:
            raise Conflict(f"Template '{p.key}' đã bị xoá.", hint="Hoàn tác lần xoá (history.undo) để dùng lại.")
        if row is None:
            if not p.create_if_missing:
                llm.get_template(s, p.key)  # ném template_not_found
            row = Template(key=p.key, kind=p.kind, body=p.body, locked=bool(p.locked), builtin_hash="")
            s.add(row)
        else:
            _deny_locked_for_llm(ctx, row)
            row.body = p.body
            if p.locked is not None:
                row.locked = p.locked
        s.flush()
        return template_dict(row)


@op("template.reset", summary="Đưa template về nội dung cài sẵn", params=TemplateKeyParams, idempotent=True)
def template_reset(ctx: OpContext, p: TemplateKeyParams) -> dict:
    builtin = llm.builtin_template(p.key)
    with ctx.uow(f"Khôi phục template {p.key}") as s:
        row = llm.get_template(s, p.key)
        _deny_locked_for_llm(ctx, row)
        if builtin is None:
            raise NotFound(f"Template '{p.key}' không có bản cài sẵn.", code="template_no_builtin",
                           hint="Chỉ template cài sẵn mới khôi phục được; dùng history.restore cho bản cũ.")
        row.body = builtin
        row.builtin_hash = llm.body_hash(builtin)
        s.flush()
        return template_dict(row)

