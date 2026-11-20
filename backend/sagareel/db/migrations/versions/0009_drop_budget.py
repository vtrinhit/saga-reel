"""drop_budget

Bỏ tính năng ngân sách (trần lượt ảnh/video):

- Bỏ cột `project.budget` (trần riêng của dự án).
- Xoá các khoá cài đặt `budget.*` còn sót trong `global_settings.data` và `project.settings` (khoá không còn trong
  settings schema).
- Giữ bảng `spend` (sổ lượt đã tạo, chỉ để thống kê).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-20 23:30:00
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _strip_budget_keys(conn, table: str, column: str) -> None:
    for id_, raw in conn.execute(sa.text(f"SELECT id, {column} FROM {table}")).all():
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        kept = {k: v for k, v in data.items() if not k.startswith("budget.")}
        if len(kept) != len(data):
            conn.execute(sa.text(f"UPDATE {table} SET {column} = :d WHERE id = :i"),
                         {"d": json.dumps(kept, ensure_ascii=False), "i": id_})


def upgrade() -> None:
    conn = op.get_bind()
    _strip_budget_keys(conn, "global_settings", "data")
    _strip_budget_keys(conn, "project", "settings")
    # Không dùng batch: batch dựng lại bảng qua reflection làm mất `COLLATE NOCASE` của `project.key` (0008).
    # SQLite ≥ 3.35 bỏ cột trực tiếp được (cột không có index/ràng buộc).
    op.drop_column("project", "budget")


def downgrade() -> None:
    op.add_column("project", sa.Column("budget", sa.Text(), nullable=False, server_default="{}"))
