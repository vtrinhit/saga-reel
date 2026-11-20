"""Cài đặt theo tầng: đọc tầng global, ghi global (có lịch sử), giải thích giá trị hiệu lực (spec §5.4).

`global_settings` là bảng `Plain` một dòng (id=1); cột `data` chỉ chứa khoá có trong settings schema (ghi khoá lạ
hoặc khoá `_*` → `unknown_setting`). Dữ liệu nội bộ của ứng dụng nằm ở bảng `system_state`
(`services.system_state`), không bao giờ ở đây.

Lịch sử: `set_global` theo dõi dòng qua `UnitOfWork.track` → revision `("global_settings", 1)` với snapshot cả dòng
(`id`, `created_at`, `data`), nên `history.undo/redo` áp dụng được như mọi thực thể khác. Change set luôn có
`project_id=None`: cài đặt toàn cục không thuộc project nào, kể cả khi được đổi từ trong một project.

M0 chỉ có tầng default + global + run; các tầng preset/project/sequence/scene/clip được thêm khi có bảng tương ứng.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from sagareel.core import secrets
from sagareel.core.context import OpContext
from sagareel.core.ops import current_op
from sagareel.core.resolver import INTERNAL_PREFIX, Layer, Resolved, SettingDef, unknown_setting
from sagareel.core.uow import UnitOfWork
from sagareel.db.models import GlobalSettings

GLOBAL_ID = 1
OP_SET_GLOBAL = "settings.set_global"


def global_layer(session: Session) -> Layer:
    """Tầng global từ dòng `global_settings` id=1; chưa có dòng → tầng rỗng."""
    g = session.get(GlobalSettings, GLOBAL_ID)
    return Layer("global", GLOBAL_ID, dict(g.data) if g is not None else {})


def schema(ctx: OpContext) -> list[SettingDef]:
    return list(ctx.services.resolver.schema.values())


def _check_keys(ctx: OpContext, values: dict[str, Any]) -> None:
    """Mọi khoá phải có trong schema; khoá `_*` báo rõ là khoá nội bộ."""
    for key in values:
        if key.startswith(INTERNAL_PREFIX):
            raise unknown_setting(key, f"'{key}' là khoá nội bộ, không ghi được qua cài đặt.")
        ctx.services.resolver.definition(key)


def run_layer_values(ctx: OpContext, run_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Giá trị tầng run = `ctx.run_overrides` gộp `run_overrides` (tham số thắng), đã kiểm tra như khi ghi."""
    run = {**ctx.run_overrides, **(run_overrides or {})}
    _check_keys(ctx, run)
    ctx.services.resolver.validate_values(run)
    return run


def set_global(ctx: OpContext, values: dict[str, Any]) -> None:
    """Ghi đè các khoá ở tầng global (gộp vào dữ liệu hiện có). Giá trị `None` = bỏ ghi đè (trở về mặc định).

    Kiểm tra toàn bộ trước khi mở UnitOfWork: khoá lạ/nội bộ → `unknown_setting`, sai kiểu/ngoài lựa chọn →
    `validation_failed`, khi đó không ghi gì.
    """
    _check_keys(ctx, values)
    ctx.services.resolver.validate_values({k: v for k, v in values.items() if v is not None})
    if not values:
        return

    u = UnitOfWork(
        ctx.services.db,
        actor=ctx.actor,
        op_name=current_op.get() or OP_SET_GLOBAL,
        project_id=None,  # cài đặt toàn cục không thuộc project nào
        summary=f"Đổi cài đặt toàn cục: {', '.join(values)}",
    )
    with u as s:
        g = s.get(GlobalSettings, GLOBAL_ID)
        if g is None:
            # Tạo dòng rỗng trước khi theo dõi: snapshot "trước" là `data == {}` chứ không phải "không có dòng",
            # nên hoàn tác lần ghi đầu chỉ làm trống dữ liệu thay vì xoá cả dòng.
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
    # Cấu hình truy cập (`remote.*`) của middleware bảo mật được cache 5 s: đổi là có hiệu lực ngay.
    ctx.services.auth_cache.invalidate()
    # Trần luồng (`concurrency.*`): worker đọc mới mỗi nhịp; đánh thức ngay + phát
    # `queue.updated` để việc đang chờ chạy liền và giao diện cập nhật.
    from sagareel.services import concurrency

    if any(k in concurrency.WATCHED_KEYS for k in values):
        concurrency.notify(ctx.services)


def explain(ctx: OpContext, run_overrides: dict[str, Any] | None = None) -> list[Resolved]:
    """Mọi khoá trong schema với giá trị hiệu lực và tầng nguồn (default → global → run).

    Tầng run xem `run_layer_values`. Giá trị của khoá `secret` bị che.
    """
    resolver = ctx.services.resolver
    run = run_layer_values(ctx, run_overrides)
    with ctx.services.db.session() as s:
        layers = [global_layer(s)]
    if run:
        layers.append(Layer("run", None, run))
    rows = resolver.explain(layers)
    for row in rows:
        if resolver.schema[row.key].secret and isinstance(row.value, str):
            row.value = secrets.mask(row.value)
    return rows
