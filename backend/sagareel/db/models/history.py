"""Lịch sử phiên bản: một op = một change_set, mỗi thực thể ★ đổi = một revision (spec §5.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, Plain


class ChangeSet(Plain, Base):
    __tablename__ = "change_set"
    __table_args__ = (Index("ix_change_set_project_id_id", "project_id", "id"),)  # history.list theo dự án (M8)

    op_name: Mapped[str]
    actor: Mapped[str]
    project_id: Mapped[int | None] = mapped_column(index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    undone_at: Mapped[datetime | None]
    undone_by: Mapped[int | None]  # id change_set của thao tác hoàn tác


class Revision(Plain, Base):
    __tablename__ = "revision"
    __table_args__ = (Index("ix_revision_entity", "entity_type", "entity_id"),)

    change_set_id: Mapped[int] = mapped_column(ForeignKey("change_set.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str]  # = tên bảng
    entity_id: Mapped[int]
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONDict)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONDict)
