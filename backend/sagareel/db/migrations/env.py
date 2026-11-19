"""Môi trường Alembic của SagaReel.

Hai cách chạy:
- Lập trình (`Database.migrate()`): connection có sẵn được truyền qua `config.attributes["connection"]`.
- Tay (`alembic -c alembic.ini ...` ở thư mục gốc): URL lấy từ `sqlalchemy.url` nếu có, không thì từ
  `load_settings().db_path` (tôn trọng `SAGAREEL_DATA_DIR`).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

import sagareel.db.models  # noqa: F401 — nạp đủ model vào Base.metadata
from sagareel.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from sagareel.config import load_settings
    from sagareel.db.session import sqlite_url

    return sqlite_url(load_settings().db_path)


def _configure(**kwargs) -> None:
    from sagareel.db.session import include_name  # bỏ qua bảng ảo FTS5 không có model

    context.configure(target_metadata=target_metadata, render_as_batch=True, include_name=include_name, **kwargs)


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(_url(), poolclass=NullPool, hide_parameters=True)  # lỗi SQL không in tham số (khoá API)
    try:
        with engine.connect() as conn:
            _configure(connection=conn)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
