"""project_prompts

Prompt là **dữ liệu của project**; "mẫu" là project mẫu nhân bản được (không còn bộ template chung của tool).

- Bảng mới `project_prompt(project_id, key, body)` (theo dõi lịch sử; duy nhất (project_id, key) trong dòng chưa xoá).
  Project cũ được chép bộ prompt từ dự án mẫu **một lần** lúc khởi động (`services/samples.backfill_once`) — migration
  không đọc file dữ liệu mẫu.
- `project`: thêm `genre` (số liệu thể loại, JSON), `workflows` (quy trình, JSON), `narrator_voice`, `is_sample`; bỏ
  `style_pack_id`. `genre` chép từ preset genre khớp `genre_key` (của project thắng toàn cục), `workflows` chép mọi
  preset workflow (toàn cục + của project), `narrator_voice` lấy từ `settings.narrator_voice_desc` (khoá đó bị bỏ).
- Xoá preset loại `genre`, `workflow`, `style_pack` (thành dữ liệu project) và bảng `template` (prompt chung của tool).
  Revision cũ của bảng template vẫn đọc được trong lịch sử; hoàn tác chúng báo `unknown_entity_type`.
- Xoá công việc LLM cũ đã được `story_*` thay thế.

Hạ cấp: thêm lại cột/bảng (rỗng), bỏ bảng `project_prompt` và các cột mới (dữ liệu mới bị mất).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-21 12:00:00
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_TASKS = ("write_bible", "outline", "write_script", "split_clips", "image_prompts", "motion_prompts",
                "dialogue", "memory_summarize")
REMOVED_KINDS = ("genre", "workflow", "style_pack")


def _load(raw):
    try:
        v = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def upgrade() -> None:
    op.create_table('project_prompt',
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], name=op.f('fk_project_prompt_project_id_project')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_project_prompt'))
    )
    with op.batch_alter_table('project_prompt', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_prompt_project_id'), ['project_id'], unique=False)
        batch_op.create_index('ux_project_prompt_key', ['project_id', 'key'], unique=True,
                              sqlite_where=sa.text('deleted_at IS NULL'))

    # ADD/DROP COLUMN trực tiếp (SQLite ≥ 3.35), KHÔNG batch: dựng lại bảng project làm mất collation NOCASE của `key`
    # và index duy nhất một phần ux_project_key.
    op.add_column('project', sa.Column('genre', sa.Text(), nullable=False, server_default='{}'))
    op.add_column('project', sa.Column('workflows', sa.Text(), nullable=False, server_default='{}'))
    op.add_column('project', sa.Column('narrator_voice', sa.Text(), nullable=False, server_default=''))
    op.add_column('project', sa.Column('is_sample', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("ALTER TABLE project DROP COLUMN style_pack_id")

    conn = op.get_bind()
    presets = conn.execute(sa.text(
        "SELECT kind, key, name, data, project_id FROM preset WHERE deleted_at IS NULL AND kind IN ('genre', 'workflow')"
        " ORDER BY project_id IS NOT NULL, id")).all()  # toàn cục trước, của project sau (ghi đè)
    for pid, genre_key, settings in conn.execute(sa.text("SELECT id, genre_key, settings FROM project")).all():
        genre, workflows = {}, {}
        for kind, key, name, data, ppid in presets:
            if ppid is not None and ppid != pid:
                continue
            if kind == 'genre' and key == genre_key:
                genre = _load(data)
            elif kind == 'workflow':
                workflows[key] = {"name": name, **_load(data)}
        st = _load(settings)
        voice = st.pop("narrator_voice_desc", "")
        conn.execute(sa.text("UPDATE project SET genre = :g, workflows = :w, narrator_voice = :v, settings = :s "
                             "WHERE id = :i"),
                     {"g": json.dumps(genre, ensure_ascii=False), "w": json.dumps(workflows, ensure_ascii=False),
                      "v": str(voice or ""), "s": json.dumps(st, ensure_ascii=False), "i": pid})

    conn.execute(sa.text("DELETE FROM preset WHERE kind IN ('genre', 'workflow', 'style_pack')"))
    conn.execute(sa.text("DELETE FROM llm_task WHERE key IN ("
                         + ", ".join(f"'{k}'" for k in LEGACY_TASKS) + ")"))
    op.drop_table('template')


def downgrade() -> None:
    op.create_table('template',
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('locked', sa.Boolean(), nullable=False),
    sa.Column('builtin_hash', sa.String(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_template')),
    sa.UniqueConstraint('key', name=op.f('uq_template_key'))
    )
    op.add_column('project', sa.Column('style_pack_id', sa.Integer(), nullable=True))
    for col in ('is_sample', 'narrator_voice', 'workflows', 'genre'):
        op.execute(f"ALTER TABLE project DROP COLUMN {col}")
    with op.batch_alter_table('project_prompt', schema=None) as batch_op:
        batch_op.drop_index('ux_project_prompt_key', sqlite_where=sa.text('deleted_at IS NULL'))
        batch_op.drop_index(batch_op.f('ix_project_prompt_project_id'))
    op.drop_table('project_prompt')
