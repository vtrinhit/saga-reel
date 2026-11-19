"""Route REST sinh tự động từ operation registry.

- `GET /api/ops` → `ops.catalog()`.
- Mỗi op một route `POST /api/ops/{name}`: body là params model (OpenAPI có schema đúng), `operation_id` = tên op,
  tag = nhóm. FastAPI validate body; lỗi validate được handler toàn app đổi thành `validation_failed`.
- Route bắt mọi tên còn lại (ngoài OpenAPI) trả `unknown_op` 404.
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel

from sagareel.core import ops, security
from sagareel.core.context import OpContext
from sagareel.core.errors import ErrorPayload

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorPayload, "description": "Dữ liệu đầu vào không hợp lệ (`validation_failed`)"},
    "default": {"model": ErrorPayload, "description": "Lỗi chuẩn `{ok: false, error_code, …}`"},
}


def context_from_request(request: Request) -> OpContext:
    """Dựng OpContext từ `request.state.principal` (do `SecurityMiddleware` đặt).

    Không có principal → 401 `auth_required` (không bao giờ mặc định là owner).
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise security.auth_required()
    return OpContext(
        services=request.app.state.services,
        actor=principal.actor,
        role=principal.role,
        via=principal.via,
        rate_key=getattr(request.state, "rate_key", None),
    )


def _make_endpoint(spec: ops.OpSpec):
    required = any(f.is_required() for f in spec.params.model_fields.values())

    async def endpoint(request: Request, body: BaseModel | None) -> Any:
        params = body if body is not None else ops.parse_params(spec, {})
        return await ops.execute(spec, params, context_from_request(request))

    # Annotation thật (không phải chuỗi) để FastAPI dựng body + OpenAPI từ đúng params model của op.
    # Params toàn trường tuỳ chọn → body được phép bỏ trống (coi như `{}`).
    endpoint.__signature__ = inspect.Signature([
        inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
        inspect.Parameter(
            "body",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=spec.params,
            default=Body(...) if required else Body(default=None),
        ),
    ])
    endpoint.__name__ = "op_" + spec.name.replace(".", "_")
    return endpoint


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/ops", operation_id="ops_catalog", tags=["ops"], summary="Danh mục thao tác")
    async def list_ops() -> list[dict]:
        return ops.catalog()

    for spec in sorted(ops.REGISTRY.values(), key=lambda s: s.name):
        # Khai báo 422 + default bằng ErrorPayload: FastAPI không tự thêm HTTPValidationError (sai hình) nữa.
        responses: dict[int | str, dict[str, Any]] = {**_ERROR_RESPONSES}
        if spec.result is not None:
            responses[200] = {"model": spec.result}
        router.add_api_route(
            f"/api/ops/{spec.name}",
            _make_endpoint(spec),
            methods=["POST"],
            operation_id=spec.name,
            tags=[spec.group],
            summary=spec.summary,
            responses=responses,
        )

    # Phải đứng sau các route op cụ thể: Starlette khớp route theo thứ tự.
    @router.post("/api/ops/{name}", include_in_schema=False)
    async def unknown_op(name: str) -> None:
        raise ops.unknown_op(name)

    return router
