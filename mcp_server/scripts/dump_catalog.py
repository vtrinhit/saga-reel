r"""Ghi catalog op hiện tại ra `mcp_server/tests/fixtures/catalog.json` (fixture cho test MCP).

Chạy bằng venv CHÍNH (có fastapi/sqlalchemy), từ gốc repo:

    $env:PYTHONPATH="backend"; .venv\Scripts\python mcp_server/scripts/dump_catalog.py
"""

from __future__ import annotations

import json
from pathlib import Path

from sagareel import ops
from sagareel.core.ops import catalog

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "catalog.json"


def main() -> None:
    ops.load_all()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Đã ghi {OUT}")


if __name__ == "__main__":
    main()
