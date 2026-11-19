"""Fixture chung cho test MCP (chạy trong .venv-mcp, từ thư mục mcp_server/).

`fixtures/catalog.json` chụp từ `sagareel.core.ops.catalog()`; tạo lại bằng venv chính, từ gốc repo:
    $env:PYTHONPATH="backend"; .venv\\Scripts\\python mcp_server/scripts/dump_catalog.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
APP = "http://127.0.0.1:8990"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def catalog() -> list[dict]:
    return json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Không đụng token thật: data dir tạm, không SAGAREEL_TOKEN/URL từ môi trường."""
    monkeypatch.delenv("SAGAREEL_TOKEN", raising=False)
    monkeypatch.delenv("SAGAREEL_URL", raising=False)
    monkeypatch.setenv("SAGAREEL_DATA_DIR", str(tmp_path / "data"))
