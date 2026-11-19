"""File media (ảnh, video, âm thanh) — spec §6."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, Plain


class Media(Plain, Base):
    __tablename__ = "media"

    project_id: Mapped[int | None]
    kind: Mapped[str]
    path: Mapped[str]  # tương đối so với data/
    mime: Mapped[str]
    width: Mapped[int | None]
    height: Mapped[int | None]
    duration_ms: Mapped[int | None]
    size: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str] = mapped_column(index=True)
    origin: Mapped[str]  # generated/upload/derived/fake
    meta: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    trashed_at: Mapped[datetime | None]  # file gốc đã chuyển vào data/trash/ (media.cleanup)
    purged_at: Mapped[datetime | None]  # file trong thùng rác đã xoá hẳn (trash.purge)
