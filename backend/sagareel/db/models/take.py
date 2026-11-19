"""Take: mỗi lần tạo ảnh/video/âm thanh/sheet — không bao giờ ghi đè hay xoá (spec §10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, Plain, utcnow


class Take(Plain, Base):
    __tablename__ = "take"
    __table_args__ = (Index("ix_take_owner", "owner_type", "owner_id", "part"),)

    owner_type: Mapped[str]  # clip/variant/dialogue
    owner_id: Mapped[int]
    part: Mapped[str]  # image/video/audio/sheet
    view: Mapped[str] = mapped_column(default="")  # góc nhìn cho sheet
    media_id: Mapped[int | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(default="queued")  # queued/running/succeeded/failed/cancelled
    job_id: Mapped[int | None] = mapped_column(index=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    parent_take_id: Mapped[int | None]
    cost: Mapped[float] = mapped_column(default=0.0)
    elapsed_ms: Mapped[int | None]
    rating: Mapped[int | None]
    verdict: Mapped[str] = mapped_column(default="")  # keep/trim/fix_post/regenerate/reject
    verdict_reason: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int | None]  # seed tất định (project/tập/clip + biến thể) khi tạo
    error_code: Mapped[str] = mapped_column(default="")  # mã lỗi khi status=failed
    notes: Mapped[str] = mapped_column(Text, default="")
    auto_score: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    lang: Mapped[str] = mapped_column(default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
