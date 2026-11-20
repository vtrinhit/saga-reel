"""perf_indexes

Chỉ thêm chỉ mục (M8, hiệu năng ở quy mô 1000 clip / 200 tập / 50 job), không đổi cột:

- `job(project_id, id)`: `job.list` theo dự án, mới nhất trước.
- `spend(job_id, kind)`: tra chi tiêu của một job.
- `change_set(project_id, id)`: `history.list` theo dự án, mới nhất trước.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20 20:00:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_job_project_id_id', 'job', ['project_id', 'id'], unique=False)
    op.create_index('ix_spend_job_kind', 'spend', ['job_id', 'kind'], unique=False)
    op.create_index('ix_change_set_project_id_id', 'change_set', ['project_id', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_change_set_project_id_id', table_name='change_set')
    op.drop_index('ix_spend_job_kind', table_name='spend')
    op.drop_index('ix_job_project_id_id', table_name='job')
