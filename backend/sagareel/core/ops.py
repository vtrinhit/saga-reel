"""Operation registry — trái tim của "một lõi, ba cửa".

Mỗi thao tác được khai báo một lần bằng `@op(...)`; REST (`api/routes_ops.py`), CLI và MCP đều sinh từ `REGISTRY`
/ `catalog()`. Handler nhận `(ctx: OpContext, p: Params)`:

- `def` (đồng bộ): chạy trong thread qua `anyio.to_thread.run_sync`, được dùng DB trực tiếp.
- `async def`: được await thẳng trên event loop, tự lo truy cập DB qua `to_thread`.
"""

from __future__ import annotations

import functools
import inspect
import logging
import re
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

import anyio
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Forbidden, validation_failed

McpKind = Literal["tool", "call"]

logger = logging.getLogger("sagareel.ops")

# Tên op đang chạy trong ngữ cảnh hiện tại (UnitOfWork dùng để đặt tên change set). Được chép sang thread worker.
current_op: ContextVar[str | None] = ContextVar("sagareel_current_op", default=None)


class CommittedChange(NamedTuple):
    change_set_id: int
    project_id: int | None
    entities: tuple[tuple[str, int], ...]
    kinds: tuple[tuple[str, int, str], ...] = ()  # (loại, id, created|updated|deleted|moved)


# Change set đã commit trong op đang chạy: `execute` đặt một list mới, `UnitOfWork` append vào (cùng object list,
# kể cả từ thread worker vì context được chép), sau khi op xong `execute` phát một `entity.changed`.
committed_changes: ContextVar[list[CommittedChange] | None] = ContextVar("sagareel_committed_changes", default=None)

def last_change_set_id() -> int | None:
    """Id change set commit gần nhất trong op đang chạy (None nếu chưa có / không chạy trong op).

    Op ghi trả kèm `change_set_id` để UI hoàn tác đúng thao tác (`history.undo(change_set_id)`)."""
    changes = committed_changes.get()
    return changes[-1].change_set_id if changes else None


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


@dataclass(frozen=True)
class OpSpec:
    name: str
    group: str
    summary: str
    params: type[BaseModel]
    result: type[BaseModel] | None
    handler: Callable[..., Any]
    is_async: bool
    read_only: bool
    destructive: bool
    idempotent: bool
    costly: bool
    long_running: bool
    mcp: McpKind
    viewer: bool


REGISTRY: dict[str, OpSpec] = {}


def op(
    name: str,
    *,
    summary: str,
    params: type[BaseModel],
    result: type[BaseModel] | None = None,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    costly: bool = False,
    long_running: bool = False,
    mcp: McpKind = "call",
    viewer: bool | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Đăng ký handler vào `REGISTRY`. `viewer` (viewer được gọi) mặc định bằng `read_only`."""
    if not _NAME_RE.match(name):
        raise ValueError(f"Tên op không hợp lệ: {name!r} (cần dạng 'nhom.hanh_dong', chữ thường)")
    if not (isinstance(params, type) and issubclass(params, BaseModel)):
        raise TypeError(f"params của op {name!r} phải là lớp con của pydantic.BaseModel")
    if result is not None and not (isinstance(result, type) and issubclass(result, BaseModel)):
        raise TypeError(f"result của op {name!r} phải là lớp con của pydantic.BaseModel hoặc None")
    if mcp not in ("tool", "call"):
        raise ValueError(f"mcp của op {name!r} phải là 'tool' hoặc 'call'")

    def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        if name in REGISTRY:
            raise ValueError(f"Op {name!r} đã được đăng ký")
        REGISTRY[name] = OpSpec(
            name=name,
            group=name.split(".", 1)[0],
            summary=summary,
            params=params,
            result=result,
            handler=handler,
            is_async=inspect.iscoroutinefunction(handler),
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
            costly=costly,
            long_running=long_running,
            mcp=mcp,
            viewer=read_only if viewer is None else viewer,
        )
        return handler

    return decorator


def catalog() -> list[dict]:
    """Danh mục op (sắp theo tên) cho `GET /api/ops`, CLI và MCP."""
    return [
        {
            "name": s.name,
            "group": s.group,
            "summary": s.summary,
            "read_only": s.read_only,
            "destructive": s.destructive,
            "idempotent": s.idempotent,
            "costly": s.costly,
            "long_running": s.long_running,
            "mcp": s.mcp,
            "viewer": s.viewer,
            "params_schema": s.params.model_json_schema(),
            "result_schema": s.result.model_json_schema() if s.result is not None else None,
        }
        for s in sorted(REGISTRY.values(), key=lambda s: s.name)
    ]


def unknown_op(name: str) -> AppError:
    return AppError(
        "unknown_op",
        f"Không có thao tác nào tên '{name}'.",
        hint="Gọi GET /api/ops để xem danh sách thao tác.",
        status=404,
    )


def get_spec(name: str) -> OpSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise unknown_op(name)
    return spec


def check_role(spec: OpSpec, ctx: OpContext) -> None:
    if ctx.role != "owner" and not spec.viewer:
        raise Forbidden(f"Vai trò '{ctx.role}' không được phép thực hiện thao tác '{spec.name}'.")


def parse_params(spec: OpSpec, payload: Any) -> BaseModel:
    try:
        return spec.params.model_validate(payload)
    except ValidationError as e:
        raise validation_failed(e.errors()) from None


async def execute(spec: OpSpec, params: BaseModel, ctx: OpContext) -> dict | list | None:
    """Chạy op với params đã validate (REST dùng trực tiếp vì FastAPI đã validate body)."""
    check_role(spec, ctx)
    token = current_op.set(spec.name)
    changes: list[CommittedChange] = []
    changes_token = committed_changes.set(changes)
    try:
        if spec.is_async:
            value = await spec.handler(ctx, params)
        else:
            # to_thread.run_sync chép contextvars sang thread worker → handler thấy current_op.
            value = await anyio.to_thread.run_sync(functools.partial(spec.handler, ctx, params))
    finally:
        committed_changes.reset(changes_token)
        current_op.reset(token)
        # Phát cả khi handler lỗi sau khi đã commit: dữ liệu đã thật sự đổi.
        if changes:
            _publish_entity_changed(ctx, changes)
    return _dump(spec, value)


def _publish_entity_changed(ctx: OpContext, changes: list[CommittedChange]) -> None:
    """Một `entity.changed` cho cả op: `change_set` là id (một UoW) hoặc list id; project lấy từ các UoW.

    Chạy trong `finally` của `execute` → không bao giờ ném lỗi (không được che kết quả/lỗi gốc của handler).
    """
    try:
        ids = [c.change_set_id for c in changes]
        entities = list(dict.fromkeys(e for c in changes for e in c.entities))
        kinds: dict[str, str] = {}
        for c in changes:
            for et, eid, kind in c.kinds:
                prev = kinds.get(f"{et}:{eid}")
                # created rồi sửa vẫn là created; deleted là kết cuối cùng; còn lại lần ghi sau thắng.
                kinds[f"{et}:{eid}"] = "created" if prev == "created" and kind in ("updated", "moved") else kind
        projects = {c.project_id for c in changes}
        project_id = projects.pop() if len(projects) == 1 else None
        ctx.services.bus.publish(
            "entity.changed",
            {"change_set": ids[0] if len(ids) == 1 else ids, "entities": [list(e) for e in entities],
             "kinds": kinds},
            project_id=project_id,
        )
    except Exception:
        logger.exception("Không phát được entity.changed cho change set %s", [c.change_set_id for c in changes])


async def invoke(name: str, payload: dict, ctx: OpContext) -> dict | list | None:
    """Điểm vào chung: tra op, kiểm role, validate payload, chạy handler, trả JSON-able."""
    spec = get_spec(name)
    check_role(spec, ctx)
    return await execute(spec, parse_params(spec, payload), ctx)


def _dump(spec: OpSpec, value: Any) -> dict | list | None:
    if value is None:
        return None
    if spec.result is not None and not isinstance(value, BaseModel):
        value = spec.result.model_validate(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return to_jsonable_python(value)
