"""Cài đặt theo tầng: đọc tầng global, ghi global (có lịch sử), giải thích giá trị hiệu lực (spec §5.4).

`global_settings` là bảng `Plain` một dòng (id=1), cột `data` chứa cả khoá cài đặt lẫn dữ liệu nội bộ của ứng dụng
(khoá bắt đầu bằng `_`, vd. `_app_secret`). Khoá nội bộ không bao giờ được trả ra hay ghi qua module này; mọi khoá
khác không do lần ghi đụng tới đều được giữ nguyên.

Lịch sử: `set_global` theo dõi dòng qua `UnitOfWork.track` → revision `("global_settings", 1)` với snapshot cả dòng
(`id`, `created_at`, `data`) trong change set của op, nên `history.undo/redo` áp dụng được như mọi thực thể khác.

M0 chỉ có tầng default + global + run; các tầng preset/project/sequence/scene/clip được thêm khi có bảng tương ứng.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from sagareel.core import secrets
from sagareel.core.context import OpContext
from sagareel.core.ops import current_op
from sagareel.core.resolver import INTERNAL_PREFIX, Layer, Resolved, SettingDef, unknown_setting
from sagareel.db.models import GlobalSettings

GLOBAL_ID = 1
OP_SET_GLOBAL = "settings.set_global"


def _public(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if not k.startswith(INTERNAL_PREFIX)}


def global_layer(session: Session) -> Layer:
    """Tầng global từ dòng `global_settings` id=1 (bỏ khoá nội bộ `_*`); chưa có dòng → tầng rỗng."""
    g = session.get(GlobalSettings, GLOBAL_ID)
    return Layer("global", GLOBAL_ID, _public(dict(g.data)) if g is not None else {})


def schema(ctx: OpContext) -> list[SettingDef]:
    return list(ctx.services.resolver.schema.values())


def _check_keys(values: dict[str, Any]) -> None:
    for key in values:
        if key.startswith(INTERNAL_PREFIX):
            raise unknown_setting(key, f"'{key}' là khoá nội bộ, không ghi được qua cài đặt.")


def set_global(ctx: OpContext, values: dict[str, Any]) -> None:
    """Ghi đè các khoá ở tầng global (gộp vào dữ liệu hiện có). Giá trị `None` = bỏ ghi đè (trở về mặc định).

    Kiểm tra toàn bộ trước khi mở UnitOfWork: khoá lạ/nội bộ → `unknown_setting`, sai kiểu/ngoài lựa chọn →
    `validation_failed`, khi đó không ghi gì.
    """
    _check_keys(values)
    resolver = ctx.services.resolver
    for key in values:
        resolver.definition(key)
    resolver.validate_values({k: v for k, v in values.items() if v is not None})
    if not values:
        return

    u = ctx.uow(f"Đổi cài đặt toàn cục: {', '.join(values)}", op_name=current_op.get() or OP_SET_GLOBAL)
    with u as s:
        g = s.get(GlobalSettings, GLOBAL_ID)
        if g is None:
            # Tạo dòng rỗng trước khi theo dõi: snapshot "trước" là `data == {}` chứ không phải "không có dòng",
            # nên hoàn tác lần ghi đầu chỉ làm trống dữ liệu thay vì xoá cả dòng (cùng khoá nội bộ ghi sau đó).
            g = GlobalSettings(id=GLOBAL_ID, data={})
            s.add(g)
            s.flush()
        u.track(g)
        data = dict(g.data)
        for key, value in values.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        if data != g.data:
            g.data = data


def explain(ctx: OpContext, run_overrides: dict[str, Any] | None = None) -> list[Resolved]:
    """Mọi khoá trong schema với giá trị hiệu lực và tầng nguồn (default → global → run).

    Tầng run = `ctx.run_overrides` gộp với `run_overrides` (tham số thắng); được kiểm tra như khi ghi.
    Giá trị của khoá `secret` bị che.
    """
    resolver = ctx.services.resolver
    run = {**ctx.run_overrides, **(run_overrides or {})}
    _check_keys(run)
    resolver.validate_values(run)
    with ctx.services.db.session() as s:
        layers = [global_layer(s)]
    if run:
        layers.append(Layer("run", None, run))
    rows = resolver.explain(layers)
    for row in rows:
        if resolver.schema[row.key].secret and isinstance(row.value, str):
            row.value = secrets.mask(row.value)
    return rows
