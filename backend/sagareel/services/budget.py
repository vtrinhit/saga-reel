"""Ngân sách tạo ảnh/video (spec §18, bản M0: tổng toàn cục theo loại).

- `budget.enabled` (bool) bật/tắt; `budget.images_total` / `budget.videos_total` là trần số đơn vị; **0 = không giới
  hạn** (vd. video GrokTele của chủ dự án không giới hạn).
- Sổ `spend` ghi mọi lần tạo (kể cả adapter giả); tổng đã dùng **không tính** kết nối có adapter `fake_*`, và kiểm
  tra bỏ qua kết nối giả.
- Kiểm tra trước khi gọi provider (`check`), ghi sổ sau khi có kết quả (`record`) — tiền đã tiêu thì luôn ghi, kể cả
  khi lúc đó đã vượt trần. `check_and_record` gộp cả hai trong một transaction (dùng khi việc tạo không tốn tiền
  trước, hoặc để giữ chỗ).
- **Vượt trần có giới hạn (đã chấp nhận):** kiểm tra trước và ghi sau không giữ chỗ, nên các job chạy song song trên
  cùng lane có thể cùng qua `check`; tổng có thể vượt trần tối đa (concurrency của lane − 1) đơn vị.
"""

from __future__ import annotations

import functools
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError
from sagareel.core.resolver import Resolver, default_resolver
from sagareel.db.models import ProviderConnection, Spend
from sagareel.services.settings import global_layer

FAKE_PREFIX = "fake_"
TOTAL_KEYS = {"image": "budget.images_total", "video": "budget.videos_total"}
_LABELS = {"image": "ảnh", "video": "video"}


@functools.lru_cache(maxsize=1)
def _default_resolver() -> Resolver:
    return default_resolver()


def _setting(session: Session, key: str, resolver: Resolver | None) -> Any:
    r = resolver or _default_resolver()
    return r.resolve(key, [global_layer(session)]).value


def is_fake(session: Session, connection_id: int | None) -> bool:
    if connection_id is None:
        return False
    adapter = session.scalar(select(ProviderConnection.adapter).where(ProviderConnection.id == connection_id))
    return bool(adapter) and adapter.startswith(FAKE_PREFIX)


def spent(session: Session, kind: str) -> float:
    """Tổng đơn vị đã dùng của `kind`, không tính kết nối giả."""
    q = (
        select(func.coalesce(func.sum(Spend.units), 0.0))
        .select_from(Spend)
        .outerjoin(ProviderConnection, ProviderConnection.id == Spend.connection_id)
        .where(
            Spend.kind == kind,
            or_(ProviderConnection.adapter.is_(None), ProviderConnection.adapter.not_like("fake\\_%", escape="\\")),
        )
    )
    return float(session.scalar(q) or 0.0)


def status(session: Session, kind: str, *, resolver: Resolver | None = None) -> dict[str, Any]:
    """`{"enabled", "total", "spent", "remaining"}`; `total`/`remaining` là None khi không giới hạn."""
    enabled = bool(_setting(session, "budget.enabled", resolver))
    key = TOTAL_KEYS.get(kind)
    total = int(_setting(session, key, resolver)) if key else 0
    used = spent(session, kind)
    limited = enabled and total > 0
    return {
        "enabled": enabled,
        "total": total if limited else None,
        "spent": used,
        "remaining": max(0.0, total - used) if limited else None,
    }


def check(
    session: Session, kind: str, units: float, connection_id: int | None, *, resolver: Resolver | None = None
) -> None:
    """Ném `AppError("budget_exceeded", status=402)` nếu thêm `units` sẽ vượt trần. Kết nối giả luôn qua."""
    if units <= 0 or is_fake(session, connection_id):
        return
    st = status(session, kind, resolver=resolver)
    if st["total"] is None:
        return
    if st["spent"] + units > st["total"]:
        label = _LABELS.get(kind, kind)
        raise AppError(
            "budget_exceeded",
            f"Vượt ngân sách {label}: đã dùng {st['spent']:g}/{st['total']}, cần thêm {units:g}.",
            hint=f"Tăng {TOTAL_KEYS[kind]} (0 = không giới hạn) hoặc tắt budget.enabled trong Cài đặt.",
            status=402,
            extra={"kind": kind, "spent": st["spent"], "total": st["total"], "requested": units},
        )


def record(
    session: Session,
    kind: str,
    units: float,
    job_id: int | None,
    connection_id: int | None,
    *,
    cost: float | None = None,
) -> Spend:
    """Ghi một dòng `spend` (flush, người gọi commit)."""
    row = Spend(kind=kind, units=float(units), job_id=job_id, connection_id=connection_id, cost=cost)
    session.add(row)
    session.flush()
    return row


def check_and_record(
    session: Session,
    kind: str,
    units: int,
    job_id: int | None,
    connection_id: int | None,
    *,
    resolver: Resolver | None = None,
) -> None:
    """`check` rồi `record` trong cùng phiên (gọi trong phiên giữ khoá ghi để không hai job cùng lọt trần)."""
    check(session, kind, units, connection_id, resolver=resolver)
    record(session, kind, units, job_id, connection_id)
