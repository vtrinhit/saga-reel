from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sagareel.app import create_app
from sagareel.config import AppSettings, load_settings
from sagareel.db.session import Database


@pytest.fixture
def settings(tmp_path) -> AppSettings:
    # env_file trỏ tới file tạm không tồn tại: test không bao giờ đọc .env.local thật.
    return load_settings(
        data_dir=tmp_path / "data",
        env_file=tmp_path / ".env.local",
        outputs_dir=tmp_path / "outputs",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def db(tmp_path) -> Iterator[Database]:
    """Database đã migrate trên tmp_path; dispose engine sau test (Windows giữ handle SQLite/WAL nếu không)."""
    database = Database(tmp_path / "x.db")
    database.migrate()
    try:
        yield database
    finally:
        database.dispose()
