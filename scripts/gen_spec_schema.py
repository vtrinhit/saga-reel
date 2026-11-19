r"""Sinh `docs/schema/project-spec.schema.json` từ mô hình pydantic của file khai báo project.

Chạy từ gốc repo bằng venv chính:

    $env:PYTHONPATH="backend"; .venv\Scripts\python scripts/gen_spec_schema.py
"""

from __future__ import annotations

from pathlib import Path

from sagareel.services.spec.models import schema_text

OUT = Path(__file__).resolve().parents[1] / "docs" / "schema" / "project-spec.schema.json"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(schema_text(), encoding="utf-8", newline="\n")
    print(f"Đã ghi {OUT}")


if __name__ == "__main__":
    main()
