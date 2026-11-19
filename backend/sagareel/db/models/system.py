"""Cài đặt toàn cục và template prompt."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, Plain, Tracked


class GlobalSettings(Plain, Base):
    """Tầng cài đặt toàn cục — một dòng duy nhất, id=1."""

    __tablename__ = "global_settings"

    data: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class Template(Tracked, Base):
    __tablename__ = "template"

    key: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str]
    body: Mapped[str] = mapped_column(Text)
    locked: Mapped[bool] = mapped_column(default=False)
    builtin_hash: Mapped[str] = mapped_column(default="")
