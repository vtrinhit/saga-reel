from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sagareel.app import create_app
from sagareel.config import AppSettings, load_settings


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
