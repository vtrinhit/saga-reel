"""Mật khẩu, phiên đăng nhập và token API (spec §5.7). Chỉ lưu hash, không lưu bản rõ."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import false
from sqlalchemy.orm import Mapped, mapped_column

from sagareel.db.base import Base, Plain


class AuthSecret(Plain, Base):
    __tablename__ = "auth_secret"

    role: Mapped[str] = mapped_column(unique=True)  # owner/viewer
    password_hash: Mapped[str]


class AuthSession(Plain, Base):
    __tablename__ = "session"

    token_hash: Mapped[str] = mapped_column(unique=True)
    role: Mapped[str]
    expires_at: Mapped[datetime]
    remember: Mapped[bool] = mapped_column(default=False)
    ip: Mapped[str] = mapped_column(default="")
    user_agent: Mapped[str] = mapped_column(default="")
    revoked_at: Mapped[datetime | None]


class ApiToken(Plain, Base):
    __tablename__ = "api_token"

    name: Mapped[str]
    token_hash: Mapped[str] = mapped_column(unique=True)
    role: Mapped[str]
    expires_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    # Chỉ hợp lệ trên request cục bộ thật (`security.is_local`): token agent cục bộ trong `data/local-agent.token`.
    local_only: Mapped[bool] = mapped_column(default=False, server_default=false())
