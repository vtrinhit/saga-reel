"""security_fixes

Sửa theo rà soát bảo mật:

- `project.key`, `sequence.key`, `skill_file.path` so sánh **không phân biệt hoa thường** (cột `COLLATE NOCASE`):
  trên Windows `Demo` và `demo` trùng thư mục xuất / file skill. Index unique một phần dùng collation của cột.
- `skill_file.path`: unique cả bảng → unique một phần `WHERE deleted_at IS NULL` (hoàn tác tạo file rồi đồng bộ lại
  được, không vướng dòng đã xoá mềm).
- Dữ liệu có sẵn trùng khi bỏ qua hoa thường: project/tập → đổi key dòng mới hơn thành `<key>-<id>`; file skill →
  xoá mềm dòng cũ hơn (giữ dòng mới nhất).

Revision ID: 0008
Revises: 0006  (người điều phối nối lại sau 0007_perf_indexes nếu có)
Create Date: 2026-11-20 22:00:00
"""
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIVE = "deleted_at IS NULL"
NOCASE = sa.String(collation="NOCASE")


def _rename_case_dupes(conn, table: str, scope: str | None) -> None:
    """Dòng chưa xoá trùng key (bỏ qua hoa thường, cùng phạm vi) → giữ dòng id nhỏ nhất, đổi key dòng sau."""
    same_scope = f" AND u.{scope} = t.{scope}" if scope else ""
    rows = conn.execute(sa.text(
        f"SELECT id, key FROM {table} t WHERE {ALIVE} AND EXISTS (SELECT 1 FROM {table} u WHERE u.deleted_at IS NULL "
        f"AND lower(u.key) = lower(t.key) AND u.id < t.id{same_scope})")).all()
    for id_, key in rows:
        conn.execute(sa.text(f"UPDATE {table} SET key = :k WHERE id = :i"), {"k": f"{key}-{id_}", "i": id_})


def upgrade() -> None:
    conn = op.get_bind()
    _rename_case_dupes(conn, "project", None)
    _rename_case_dupes(conn, "sequence", "project_id")
    old = conn.execute(sa.text(
        "SELECT id FROM skill_file t WHERE deleted_at IS NULL AND EXISTS (SELECT 1 FROM skill_file u WHERE "
        "u.deleted_at IS NULL AND lower(u.path) = lower(t.path) AND u.id > t.id)")).scalars().all()
    if old:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        conn.execute(sa.text(f"UPDATE skill_file SET deleted_at = :n WHERE id IN ({','.join(map(str, old))})"),
                     {"n": now})

    op.drop_index("ux_project_key", table_name="project")
    with op.batch_alter_table("project", schema=None) as b:
        b.alter_column("key", existing_type=sa.String(), type_=NOCASE, existing_nullable=False)
    op.create_index("ux_project_key", "project", ["key"], unique=True, sqlite_where=sa.text(ALIVE))

    op.drop_index("ux_sequence_key", table_name="sequence")
    with op.batch_alter_table("sequence", schema=None) as b:
        b.alter_column("key", existing_type=sa.String(), type_=NOCASE, existing_nullable=False)
    op.create_index("ux_sequence_key", "sequence", ["project_id", "key"], unique=True, sqlite_where=sa.text(ALIVE))

    with op.batch_alter_table("skill_file", schema=None) as b:
        b.drop_constraint("uq_skill_file_path", type_="unique")
        b.alter_column("path", existing_type=sa.String(), type_=NOCASE, existing_nullable=False)
    op.create_index("ux_skill_file_path", "skill_file", ["path"], unique=True, sqlite_where=sa.text(ALIVE))


def downgrade() -> None:
    op.drop_index("ux_skill_file_path", table_name="skill_file")
    with op.batch_alter_table("skill_file", schema=None) as b:
        b.alter_column("path", existing_type=NOCASE, type_=sa.String(), existing_nullable=False)
        b.create_unique_constraint("uq_skill_file_path", ["path"])
    op.drop_index("ux_sequence_key", table_name="sequence")
    with op.batch_alter_table("sequence", schema=None) as b:
        b.alter_column("key", existing_type=NOCASE, type_=sa.String(), existing_nullable=False)
    op.create_index("ux_sequence_key", "sequence", ["project_id", "key"], unique=True, sqlite_where=sa.text(ALIVE))
    op.drop_index("ux_project_key", table_name="project")
    with op.batch_alter_table("project", schema=None) as b:
        b.alter_column("key", existing_type=NOCASE, type_=sa.String(), existing_nullable=False)
    op.create_index("ux_project_key", "project", ["key"], unique=True, sqlite_where=sa.text(ALIVE))
