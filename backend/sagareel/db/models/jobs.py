"""Hàng đợi job, nhật ký job và sổ chi tiêu (spec §5.5, §18)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, Plain, utcnow


class Job(Plain, Base):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_status_lane_priority", "status", "lane", "priority"),
        Index("ix_job_project_id_id", "project_id", "id"),  # job.list theo dự án, mới nhất trước (M8)
    )

    type: Mapped[str]
    # queued · running · waiting_review · waiting_input · paused · succeeded · failed · cancelled · interrupted
    # · needs_attention
    status: Mapped[str] = mapped_column(default="queued")
    priority: Mapped[int] = mapped_column(default=0)
    lane: Mapped[str]  # id kết nối provider, `ffmpeg`, `llm:<conn>`
    project_id: Mapped[int | None]
    sequence_id: Mapped[int | None]
    clip_id: Mapped[int | None]
    parent_id: Mapped[int | None]  # job lô
    params: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    progress: Mapped[float] = mapped_column(default=0.0)
    stage: Mapped[str] = mapped_column(default="")
    eta_at: Mapped[datetime | None]
    client_ref: Mapped[str | None] = mapped_column(index=True)
    external_id: Mapped[str | None]
    external_state: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    attempt: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    next_attempt_at: Mapped[datetime | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    cost_estimate: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    cost_actual: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class JobLog(Plain, Base):
    __tablename__ = "job_log"

    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), index=True)
    level: Mapped[str]
    message: Mapped[str] = mapped_column(Text)


class Spend(Plain, Base):
    """Sổ lượt đã tạo (ảnh/video có tiền, một dòng mỗi lần tạo thành công) — chỉ để thống kê; thời điểm ghi = `created_at`."""

    __tablename__ = "spend"
    __table_args__ = (Index("ix_spend_job_kind", "job_id", "kind"),)  # tra chi tiêu theo job (M8)

    connection_id: Mapped[int | None]
    kind: Mapped[str]
    units: Mapped[float] = mapped_column(default=0.0)
    cost: Mapped[float | None]
    job_id: Mapped[int | None]
    project_id: Mapped[int | None] = mapped_column(index=True)  # thống kê theo dự án
