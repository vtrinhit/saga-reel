"""api_token.local_only

Cột `local_only` (bool, mặc định false): token chỉ hợp lệ trên request cục bộ thật — dùng cho token agent cục bộ
(`data/local-agent.token`, Task 8b). Dòng `local-agent` có sẵn trước migration được đánh dấu luôn.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19 19:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('api_token', schema=None) as batch_op:
        batch_op.add_column(sa.Column('local_only', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE api_token SET local_only = 1 WHERE name = 'local-agent'")


def downgrade() -> None:
    with op.batch_alter_table('api_token', schema=None) as batch_op:
        batch_op.drop_column('local_only')
