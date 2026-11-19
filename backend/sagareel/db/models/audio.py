"""Âm thanh: item trên track nhạc/sfx/title (neo vào clip, spec §23.1) và kho âm thanh."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONList, Tracked, new_key


class TimelineItem(Tracked, Base):
    __tablename__ = "timeline_item"
    __table_args__ = (Index("ux_timeline_item_key", "sequence_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),)

    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id"), index=True)
    key: Mapped[str] = mapped_column(default=lambda: new_key("t"))  # ổn định cho plan/apply
    track: Mapped[str]  # music/sfx/title
    anchor_clip_id: Mapped[int | None] = mapped_column(ForeignKey("clip.id"), index=True)
    offset_ms: Mapped[int] = mapped_column(default=0)
    end_anchor: Mapped[dict[str, Any] | None] = mapped_column(JSONDict)  # {clip_id} | {sequence_end: true} | null
    duration_ms: Mapped[int | None]
    media_id: Mapped[int | None] = mapped_column(index=True)
    sound_id: Mapped[int | None] = mapped_column(ForeignKey("sound.id"), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)  # volume, fade, duck, loop


class Sound(Tracked, Base):
    __tablename__ = "sound"
    __table_args__ = (
        Index("ux_sound_key", "project_id", "key", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ux_sound_global_key", "key", unique=True, sqlite_where=text("project_id IS NULL AND deleted_at IS NULL")),
    )

    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True)
    key: Mapped[str] = mapped_column(default=lambda: new_key("s"))  # ổn định cho plan/apply
    media_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str]  # music/sfx/ambience
    title: Mapped[str]
    tags: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    source: Mapped[str] = mapped_column(default="")
    license: Mapped[str] = mapped_column(default="")
    attribution: Mapped[str] = mapped_column(default="")
