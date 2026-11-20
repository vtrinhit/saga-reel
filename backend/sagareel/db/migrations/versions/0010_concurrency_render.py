"""concurrency_render

Luồng theo loại provider: khoá cũ `jobs.ffmpeg_concurrency` (số việc ffmpeg song song) được thay bằng
`concurrency.render` (trần loại Dựng phim, 1–20).

- `global_settings.data` / `project.settings` có `jobs.ffmpeg_concurrency`: chép giá trị (ép 1..20) sang
  `concurrency.render` nếu khoá mới chưa có, rồi bỏ khoá cũ (không còn trong settings schema).
- Hạ cấp: chép ngược `concurrency.render` → `jobs.ffmpeg_concurrency` (bỏ các khoá `concurrency.*`).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21 00:30:00
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "jobs.ffmpeg_concurrency"
NEW = "concurrency.render"
TABLES = (("global_settings", "data"), ("project", "settings"))


def _clamp(v) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return max(1, min(20, int(v)))
    except (TypeError, ValueError):
        return None


def _rewrite(conn, table: str, column: str, fn) -> None:
    for id_, raw in conn.execute(sa.text(f"SELECT id, {column} FROM {table}")).all():
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        new = fn(dict(data))
        if new != data:
            conn.execute(sa.text(f"UPDATE {table} SET {column} = :d WHERE id = :i"),
                         {"d": json.dumps(new, ensure_ascii=False), "i": id_})


def _up(data: dict) -> dict:
    if OLD in data:
        old = _clamp(data.pop(OLD))
        if NEW not in data and old is not None:
            data[NEW] = old
    return data


def _down(data: dict) -> dict:
    render = data.get(NEW)
    data = {k: v for k, v in data.items() if not k.startswith("concurrency.")}
    if render is not None:
        data[OLD] = render
    return data


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in TABLES:
        _rewrite(conn, table, column, _up)


def downgrade() -> None:
    conn = op.get_bind()
    for table, column in TABLES:
        _rewrite(conn, table, column, _down)
