"""Trạng thái nội bộ của ứng dụng (bảng `system_state`, khoá → giá trị JSON).

Dành cho dữ liệu máy tự quản (vd. `app_secret`, `seeded_env_at`) — không phải cài đặt người dùng. Bảng không bao giờ
được ghi lịch sử, nên giá trị không lọt vào revision và không bị `history.undo` đụng tới. Ghi bằng session thường
(hoặc session của một UnitOfWork — dòng này không được theo dõi); người gọi commit.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from sagareel.db.models import SystemState

APP_SECRET_KEY = "app_secret"


def _row(session: Session, key: str) -> SystemState | None:
    return session.scalars(select(SystemState).where(SystemState.key == key)).first()


def get(session: Session, key: str, default: Any = None) -> Any:
    row = _row(session, key)
    return default if row is None else row.value


def set(session: Session, key: str, value: Any) -> None:  # noqa: A001 — tên theo interface
    """Tạo hoặc thay giá trị của `key` (gán lại cả giá trị; cột không theo dõi sửa tại chỗ)."""
    row = _row(session, key)
    if row is None:
        session.add(SystemState(key=key, value=value))
    else:
        row.value = value
    session.flush()


def app_secret(session: Session) -> str:
    """Bí mật ngẫu nhiên của ứng dụng (32 byte, urlsafe base64), tạo một lần rồi giữ nguyên.

    Chèn kiểu `ON CONFLICT DO NOTHING` rồi đọc lại: hai lời gọi đồng thời vẫn nhận cùng một giá trị.
    """
    value = get(session, APP_SECRET_KEY)
    if value is None:
        session.execute(
            insert(SystemState)
            .values(key=APP_SECRET_KEY, value=_secrets.token_urlsafe(32))
            .on_conflict_do_nothing(index_elements=["key"])
        )
        value = get(session, APP_SECRET_KEY)
    return value
