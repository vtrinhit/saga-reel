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
from sagareel.services.jobs import provider_holds_job
from sagareel.services.settings import global_layer

FAKE_PREFIX = "fake_"
TOTAL_KEYS = {"image": "budget.images_total", "video": "budget.videos_total"}
PROJECT_TOTAL_KEYS = {"image": "images_total", "video": "videos_total"}  # khoá trong `project.budget`
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


def project_spent(session: Session, kind: str, project_id: int) -> float:
    """Tổng đơn vị đã dùng của `kind` trong dự án (cột `spend.project_id`), không tính kết nối giả."""
    q = (
        select(func.coalesce(func.sum(Spend.units), 0.0))
        .select_from(Spend)
        .outerjoin(ProviderConnection, ProviderConnection.id == Spend.connection_id)
        .where(
            Spend.kind == kind, Spend.project_id == project_id,
            or_(ProviderConnection.adapter.is_(None), ProviderConnection.adapter.not_like("fake\\_%", escape="\\")),
        )
    )
    return float(session.scalar(q) or 0.0)


def project_status(session: Session, kind: str, project_id: int) -> dict[str, Any]:
    """Trần riêng của dự án: `project.budget.images_total` / `videos_total` (0 hoặc thiếu = không giới hạn;
    `project.budget.enabled = false` tắt). Áp độc lập với `budget.enabled` toàn cục."""
    from sagareel.db.models import Project

    p = session.get(Project, project_id)
    cfg = dict(p.budget or {}) if p is not None else {}
    key = PROJECT_TOTAL_KEYS.get(kind)
    try:
        total = int(cfg.get(key) or 0) if key else 0
    except (TypeError, ValueError):
        total = 0
    limited = bool(cfg.get("enabled", True)) and total > 0
    used = project_spent(session, kind, project_id)
    return {"project_id": project_id, "total": total if limited else None, "spent": used,
            "remaining": max(0.0, total - used) if limited else None}


def status(session: Session, kind: str, *, resolver: Resolver | None = None,
           project_id: int | None = None) -> dict[str, Any]:
    """`{"enabled", "total", "spent", "remaining"}`; `total`/`remaining` là None khi không giới hạn. Có `project_id`
    → thêm `project` (trần riêng của dự án, `project_status`)."""
    enabled = bool(_setting(session, "budget.enabled", resolver))
    key = TOTAL_KEYS.get(kind)
    total = int(_setting(session, key, resolver)) if key else 0
    used = spent(session, kind)
    limited = enabled and total > 0
    out: dict[str, Any] = {
        "enabled": enabled,
        "total": total if limited else None,
        "spent": used,
        "remaining": max(0.0, total - used) if limited else None,
    }
    if project_id is not None:
        out["project"] = project_status(session, kind, project_id)
    return out


def _exceeded(kind: str, spent_: float, total: int, units: float, hint: str, scope: str,
              project_id: int | None = None) -> AppError:
    label = _LABELS.get(kind, kind)
    where = " của dự án" if scope == "project" else ""
    extra: dict[str, Any] = {"kind": kind, "spent": spent_, "total": total, "requested": units, "scope": scope}
    if project_id is not None:
        extra["project_id"] = project_id
    return AppError(
        "budget_exceeded",
        f"Vượt ngân sách {label}{where}: đã dùng {spent_:g}/{total}, cần thêm {units:g}.",
        hint=hint, status=402, extra=extra,
    )


def check(
    session: Session, kind: str, units: float, connection_id: int | None, *, resolver: Resolver | None = None,
    project_id: int | None = None,
) -> None:
    """Ném `AppError("budget_exceeded", status=402)` nếu thêm `units` sẽ vượt trần toàn cục hoặc trần của dự án
    (`project_id`). Kết nối giả luôn qua."""
    if units <= 0 or is_fake(session, connection_id):
        return
    st = status(session, kind, resolver=resolver)
    if st["total"] is not None and st["spent"] + units > st["total"]:
        raise _exceeded(kind, st["spent"], st["total"], units,
                        f"Tăng {TOTAL_KEYS[kind]} (0 = không giới hạn) hoặc tắt budget.enabled trong Cài đặt.",
                        "global")
    if project_id is not None:
        ps = project_status(session, kind, project_id)
        if ps["total"] is not None and ps["spent"] + units > ps["total"]:
            raise _exceeded(kind, ps["spent"], ps["total"], units,
                            f"Tăng ngân sách dự án (project.budget.{PROJECT_TOTAL_KEYS[kind]}; 0 = không giới hạn).",
                            "project", project_id)


def record(
    session: Session,
    kind: str,
    units: float,
    job_id: int | None,
    connection_id: int | None,
    *,
    cost: float | None = None,
    project_id: int | None = None,
) -> Spend:
    """Ghi một dòng `spend` (flush, người gọi commit). `project_id` bỏ trống → lấy từ job."""
    if project_id is None and job_id is not None:
        project_id = _job_project(session, job_id)
    row = Spend(kind=kind, units=float(units), job_id=job_id, connection_id=connection_id, cost=cost,
                project_id=project_id)
    session.add(row)
    session.flush()
    return row


def _job_project(session: Session, job_id: int) -> int | None:
    return session.scalar(select(Job.project_id).where(Job.id == job_id))


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

    Provider đã giữ job (`jobs.provider_holds_job`: `external_id`, hoặc `state` khác None/`submitting` như
    `completed` của adapter đồng bộ): tiền đã/sẽ bị tính → không xoá dòng cũ của job (trả lại dòng đó để `finalize`
    chốt khi xong), không kiểm tra trần; chưa có dòng nào thì ghi mới."""
    job = session.get(Job, job_id)
    if job is not None and provider_holds_job(job):
        row = session.scalars(
            select(Spend).where(Spend.job_id == job_id, Spend.kind == kind).order_by(Spend.id)
        ).first()
        return row if row is not None else record(session, kind, units, job_id, connection_id)
    release(session, job_id)
    project_id = job.project_id if job is not None else None
    check(session, kind, units, connection_id, resolver=resolver, project_id=project_id)
    return record(session, kind, units, job_id, connection_id, project_id=project_id)


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
