"""Story Bible có bằng chứng (spec §23.9).

Bảng ảo FTS5 `bible_fts(summary, quote)` tạo trong migration 0004, không có model: content-less
(`contentless_delete=1`), rowid = `bible_item.id`, tokenizer `unicode61 remove_diacritics 2` (tìm không dấu vẫn ra).
Không có trigger — service tự đồng bộ (`INSERT`/`DELETE ... WHERE rowid = ?`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, Plain, Tracked

BIBLE_FTS_TABLE = "bible_fts"


class BibleItem(Tracked, Base):
    __tablename__ = "bible_item"
    __table_args__ = (UniqueConstraint("project_id", "type", "key"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    type: Mapped[str]  # character/location/relationship/event/storyline/fact/open_question
    key: Mapped[str]
    summary: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    status: Mapped[str] = mapped_column(default="pending")  # pending/confirmed/rejected
    locked: Mapped[bool] = mapped_column(default=False)
    story_order: Mapped[int | None]
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"), index=True)


class BibleEvidence(Plain, Base):
    __tablename__ = "bible_evidence"

    item_id: Mapped[int] = mapped_column(ForeignKey("bible_item.id", ondelete="CASCADE"), index=True)
    sequence_id: Mapped[int | None] = mapped_column(ForeignKey("sequence.id"), index=True)
    source: Mapped[str]  # script/novel/manual
    quote: Mapped[str] = mapped_column(Text)
    start: Mapped[int | None]
    end: Mapped[int | None]
