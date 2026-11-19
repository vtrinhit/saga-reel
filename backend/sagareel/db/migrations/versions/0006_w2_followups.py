"""w2_followups

Đề xuất schema gom từ báo cáo W2 (Task BF):

- `api_token.suspended`: tạm dừng agent (token bị từ chối 403 `token_suspended`, khác thu hồi: bật lại được).
- `take.selected_by`: actor đã chọn take (sheet: chỉ một take được chọn mỗi (look, góc)).
- `spend.project_id`: ngân sách theo dự án (dòng có sẵn lấy từ `job.project_id`).
- `media.trashed_at` / `media.purged_at`: thùng rác lưu trữ (`data/trash/`), dọn hai bước.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('api_token', schema=None) as b:
        b.add_column(sa.Column('suspended', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('take', schema=None) as b:
        b.add_column(sa.Column('selected_by', sa.String(), nullable=False, server_default=""))
    with op.batch_alter_table('spend', schema=None) as b:
        b.add_column(sa.Column('project_id', sa.Integer(), nullable=True))
        b.create_index('ix_spend_project_id', ['project_id'], unique=False)
    with op.batch_alter_table('media', schema=None) as b:
        b.add_column(sa.Column('trashed_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('purged_at', sa.DateTime(), nullable=True))
    op.get_bind().execute(sa.text(
        "UPDATE spend SET project_id = (SELECT job.project_id FROM job WHERE job.id = spend.job_id) "
        "WHERE project_id IS NULL AND job_id IS NOT NULL"))


def downgrade() -> None:
    with op.batch_alter_table('media', schema=None) as b:
        b.drop_column('purged_at')
        b.drop_column('trashed_at')
    with op.batch_alter_table('spend', schema=None) as b:
        b.drop_index('ix_spend_project_id')
        b.drop_column('project_id')
    with op.batch_alter_table('take', schema=None) as b:
        b.drop_column('selected_by')
    with op.batch_alter_table('api_token', schema=None) as b:
        b.drop_column('suspended')
