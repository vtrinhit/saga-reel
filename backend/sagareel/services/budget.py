"""Ngân sách tạo ảnh/video (spec §18, bản M0: tổng toàn cục theo loại).

- `budget.enabled` (bool) bật/tắt; `budget.images_total` / `budget.videos_total` là trần số đơn vị; **0 = không giới
  hạn** (vd. video GrokTele của chủ dự án không giới hạn).
- Sổ `spend` ghi mọi lần tạo (kể cả adapter giả); tổng đã dùng **không tính** kết nối có adapter `fake_*`, và kiểm
  tra bỏ qua kết nối giả.
- **Giữ chỗ (job tạo ảnh/video):** trước khi gọi provider, `reserve` kiểm tra trần và chèn dòng `spend` của job trong
  **một** phiên ngắn giữ khoá ghi (`db.session(immediate=True)`) — các job chạy song song thấy chỗ đã giữ của nhau nên
  tổng **không bao giờ vượt trần**. Thành công → `finalize` (chỉnh `units` theo kết quả thật); chỉ khi chắc chắn
  provider không nhận job (lỗi trước/không có `external_id`, hoặc người dùng huỷ job chưa được nhận) → `release` xoá
  chỗ đã giữ — tắt app hay lỗi sau khi provider đã nhận đều giữ nguyên (xem `services/provider_jobs._paid`). Dòng giữ
  chỗ và dòng chốt giống nhau (cùng được tính); khoá là `job_id`, và `reserve` lại cho cùng job (thử lại sau lỗi tạm
  thời) thay chỗ cũ thay vì giữ thêm — trừ khi job đã có `external_id` (giữ dòng cũ, không kiểm tra trần).
- `check` (không ghi) dùng để chặn sớm ở op; `check_and_record` = `check` + `record` trong cùng phiên.
"""

from __future__ import annotations

import functools
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from sagareel.core.errors import AppError
from sagareel.core.resolver import Resolver, default_resolver
from sagareel.db.models import Job, ProviderConnection, Spend
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


def release(session: Session, job_id: int) -> int:
    """Xoá mọi dòng `spend` của job (chỗ đã giữ). Trả số dòng đã xoá."""
    rows = session.scalars(select(Spend).where(Spend.job_id == job_id)).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def detach(session: Session, job_id: int) -> int:
    """Tách mọi dòng `spend` khỏi job (`job_id = NULL`, dòng vẫn được tính) — dùng khi job sắp chạy một lần gửi MỚI
    mà các dòng cũ thuộc lần đã/có thể đã trả tiền: `reserve`/`release` của lần mới không bao giờ xoá được chúng.
    Trả số dòng đã tách."""
    rows = session.scalars(select(Spend).where(Spend.job_id == job_id)).all()
    for row in rows:
        row.job_id = None
    session.flush()
    return len(rows)


def reserve(
    session: Session,
    kind: str,
    units: float,
    job_id: int,
    connection_id: int | None,
    *,
    resolver: Resolver | None = None,
) -> Spend:
    """Giữ chỗ cho job: bỏ chỗ cũ của chính job này, `check`, rồi chèn dòng `spend`. Gọi trong phiên giữ khoá ghi
    (`db.session(immediate=True)`) để hai job không cùng lọt trần; vượt trần → `budget_exceeded` (không ghi gì).

    Job đã được provider nhận (`job.external_id`): tiền đã/sẽ bị tính → không xoá dòng cũ của job (trả lại dòng đó để
    `finalize` chốt khi xong), không kiểm tra trần; chưa có dòng nào thì ghi mới."""
    accepted = session.scalar(select(Job.external_id).where(Job.id == job_id))
    if accepted:
        row = session.scalars(
            select(Spend).where(Spend.job_id == job_id, Spend.kind == kind).order_by(Spend.id)
        ).first()
        return row if row is not None else record(session, kind, units, job_id, connection_id)
    release(session, job_id)
    check(session, kind, units, connection_id, resolver=resolver)
    return record(session, kind, units, job_id, connection_id)


def finalize(
    session: Session,
    job_id: int,
    units: float,
    *,
    kind: str,
    connection_id: int | None,
    cost: float | None = None,
) -> Spend:
    """Chốt chỗ đã giữ của job theo kết quả thật. Tiền đã tiêu thì luôn ghi: không còn chỗ giữ → chèn dòng mới,
    không kiểm tra trần."""
    row = session.scalars(select(Spend).where(Spend.job_id == job_id, Spend.kind == kind).order_by(Spend.id)).first()
    if row is None:
        return record(session, kind, units, job_id, connection_id, cost=cost)
    row.units = float(units)
    if cost is not None:
        row.cost = cost
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
