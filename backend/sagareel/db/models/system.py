"""Cài đặt toàn cục và template prompt."""

from __future__ import annotations

from typing import Any

from datetime import datetime

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, JSONDict, JSONValueType, Plain, Tracked, utcnow


class GlobalSettings(Plain, Base):
    """Tầng cài đặt toàn cục — một dòng duy nhất, id=1. `data` chỉ chứa khoá có trong settings schema."""

    __tablename__ = "global_settings"

    data: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)


class SystemState(Plain, Base):
    """Trạng thái nội bộ của ứng dụng (vd. `app_secret`, `seeded_env_at`): khoá → giá trị JSON.

    Không bao giờ ghi lịch sử (không `Tracked`, không `uow.track`) nên không lọt vào revision và không bị hoàn tác.
    Truy cập qua `sagareel.services.system_state`.
    """

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(unique=True)
    value: Mapped[Any] = mapped_column(JSONValueType(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Template(Tracked, Base):
    __tablename__ = "template"

    key: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str]
    body: Mapped[str] = mapped_column(Text)
    locked: Mapped[bool] = mapped_column(default=False)
    builtin_hash: Mapped[str] = mapped_column(default="")
