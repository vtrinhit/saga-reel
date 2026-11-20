"""Preset (mọi loại), nghiên cứu kênh mẫu, file skill."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONList, Tracked


class Preset(Tracked, Base):
    """`kind`: genre/platform/subtitle_style/voice/transition/workflow/channel_brand/style_pack/motion.

    `project_id` null = toàn cục. (kind, key) duy nhất trong các dòng chưa xoá của project; dòng toàn cục có index riêng.
    """

    __tablename__ = "preset"
    __table_args__ = (
        Index("ux_preset_key", "kind", "key", "project_id", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ux_preset_global_key", "kind", "key", unique=True, sqlite_where=text("project_id IS NULL AND deleted_at IS NULL")),
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True)
    kind: Mapped[str]
    key: Mapped[str]
    name: Mapped[str]
    data: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    builtin: Mapped[bool] = mapped_column(default=False)
    builtin_hash: Mapped[str] = mapped_column(default="")


class ChannelStudy(Tracked, Base):
    __tablename__ = "channel_study"

    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True)
    title: Mapped[str]
    source: Mapped[str]  # upload/url_list
    media_ids: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    captions: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(default="pending")
    result: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class SkillFile(Tracked, Base):
    __tablename__ = "skill_file"
    # Duy nhất trong các dòng chưa xoá, không phân biệt hoa thường (Windows; migration 0008).
    __table_args__ = (Index("ux_skill_file_path", "path", unique=True, sqlite_where=text("deleted_at IS NULL")),)

    path: Mapped[str] = mapped_column(String(collation="NOCASE"))
    body: Mapped[str] = mapped_column(Text, default="")
