"""w1_followups

Đề xuất schema gom từ báo cáo W1 (Task S):

- `key` của mọi bảng ★ W0 là duy nhất **trong các dòng chưa xoá** (index unique một phần `WHERE deleted_at IS NULL`)
  thay cho ràng buộc unique trên cả bảng → xoá mềm xong tạo lại cùng key được.
- Dòng toàn cục (`project_id IS NULL`) của `preset` (kind, key), `asset_kind` (key), `voice_profile` (key), `sound`
  (key) cũng duy nhất (SQLite coi NULL là khác nhau nên unique thường không chặn). Trùng có sẵn → giữ dòng mới nhất,
  xoá mềm phần còn lại.
- Cột mới (trước nằm trong JSON): `clip.connection/model` (từ `settings`), `clip.axis_side/cut_in/cut_out` (từ
  `custom`), `take.seed/error_code/verdict_reason` (từ `inputs`) — dữ liệu được chuyển và gỡ khỏi JSON.
- `render_chunk.meta` + unique (sequence_id, variant, fingerprint) (trùng có sẵn → giữ dòng mới nhất).
- `key` cho `dialogue_line`, `timeline_item`, `sound`: dòng có sẵn nhận key tất định `l<id>` / `t<id>` / `s<id>`;
  dòng mới do model sinh (ngẫu nhiên, không đổi về sau) — khoá ổn định cho plan/apply.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20 12:00:00
"""
import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIVE = "deleted_at IS NULL"
GLOBAL_ALIVE = "project_id IS NULL AND deleted_at IS NULL"

# bảng, tên ràng buộc unique cũ (0004), cột, tên index mới
SCOPED = (
    ("project", "uq_project_key", ["key"], "ux_project_key"),
    ("group_node", "uq_group_node_project_id", ["project_id", "key"], "ux_group_node_key"),
    ("sequence", "uq_sequence_project_id", ["project_id", "key"], "ux_sequence_key"),
    ("scene", "uq_scene_sequence_id", ["sequence_id", "key"], "ux_scene_key"),
    ("clip", "uq_clip_sequence_id", ["sequence_id", "key"], "ux_clip_key"),
    ("asset_kind", "uq_asset_kind_project_id", ["project_id", "key"], "ux_asset_kind_key"),
    ("asset", "uq_asset_project_id", ["project_id", "key"], "ux_asset_key"),
    ("asset_variant", "uq_asset_variant_asset_id", ["asset_id", "key"], "ux_asset_variant_key"),
    ("voice_profile", "uq_voice_profile_project_id", ["project_id", "key"], "ux_voice_profile_key"),
    ("preset", "uq_preset_kind", ["kind", "key", "project_id"], "ux_preset_key"),
    ("bible_item", "uq_bible_item_project_id", ["project_id", "type", "key"], "ux_bible_item_key"),
)
# bảng, cột phân biệt của dòng toàn cục, tên index
GLOBAL = (
    ("asset_kind", ["key"], "ux_asset_kind_global_key"),
    ("voice_profile", ["key"], "ux_voice_profile_global_key"),
    ("preset", ["kind", "key"], "ux_preset_global_key"),
    ("sound", ["key"], "ux_sound_global_key"),
)
NEW_KEYS = (  # bảng, tiền tố key tất định, cột phạm vi, tên index
    ("dialogue_line", "l", ["clip_id", "key"], "ux_dialogue_line_key"),
    ("timeline_item", "t", ["sequence_id", "key"], "ux_timeline_item_key"),
    ("sound", "s", ["project_id", "key"], "ux_sound_key"),
)
CLIP_FROM_SETTINGS = ("connection", "model")
CLIP_FROM_CUSTOM = ("axis_side", "cut_in", "cut_out")


def _load(raw):
    if raw is None or raw == "":
        return {}
    try:
        v = json.loads(raw)
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _dump(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def _soft_delete_global_dupes(conn, table: str, cols: list[str]) -> None:
    """Giữ dòng toàn cục chưa xoá mới nhất (id lớn nhất) cho mỗi bộ `cols`; xoá mềm phần còn lại."""
    rows = conn.execute(sa.text(
        f"SELECT id FROM {table} t WHERE {GLOBAL_ALIVE} AND id < ("
        f"SELECT max(id) FROM {table} u WHERE u.project_id IS NULL AND u.deleted_at IS NULL AND "
        + " AND ".join(f"u.{c} = t.{c}" for c in cols) + ")"
    )).scalars().all()
    if rows:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")  # định dạng DateTime của SQLAlchemy
        conn.execute(sa.text(f"UPDATE {table} SET deleted_at = :now WHERE id IN ({','.join(map(str, rows))})"),
                     {"now": now})


def upgrade() -> None:
    conn = op.get_bind()

    # --- Cột mới ----------------------------------------------------------------------------------------------------
    with op.batch_alter_table('clip', schema=None) as b:
        for col in (*CLIP_FROM_SETTINGS, *CLIP_FROM_CUSTOM):
            b.add_column(sa.Column(col, sa.String(), nullable=False, server_default=""))
    with op.batch_alter_table('take', schema=None) as b:
        b.add_column(sa.Column('seed', sa.Integer(), nullable=True))
        b.add_column(sa.Column('error_code', sa.String(), nullable=False, server_default=""))
        b.add_column(sa.Column('verdict_reason', sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table('render_chunk', schema=None) as b:
        b.add_column(sa.Column('meta', sa.Text(), nullable=False, server_default="{}"))
    for table, _, _, _ in NEW_KEYS:
        with op.batch_alter_table(table, schema=None) as b:
            b.add_column(sa.Column('key', sa.String(), nullable=False, server_default=""))

    # --- Chuyển dữ liệu từ JSON ------------------------------------------------------------------------------------
    for cid, settings, custom in conn.execute(sa.text("SELECT id, settings, custom FROM clip")).all():
        st, cu = _load(settings), _load(custom)
        vals = {}
        for k in CLIP_FROM_SETTINGS:
            if k in st:
                v = st.pop(k)
                vals[k] = "" if v is None else str(v)
        for k in CLIP_FROM_CUSTOM:
            if k in cu:
                v = cu.pop(k)
                vals[k] = "" if v is None else str(v)
        if vals:
            sets = ", ".join(f"{k} = :{k}" for k in vals)
            conn.execute(sa.text(f"UPDATE clip SET {sets}, settings = :st, custom = :cu WHERE id = :id"),
                         {**vals, "st": _dump(st), "cu": _dump(cu), "id": cid})
    for tid, inputs in conn.execute(sa.text("SELECT id, inputs FROM take")).all():
        d = _load(inputs)
        if not any(k in d for k in ("seed", "error_code", "verdict_reason")):
            continue
        seed = d.pop("seed", None)
        try:
            seed = int(seed) if seed is not None else None
        except (TypeError, ValueError):
            seed = None
        conn.execute(sa.text("UPDATE take SET seed = :seed, error_code = :ec, verdict_reason = :vr, inputs = :inp "
                             "WHERE id = :id"),
                     {"seed": seed, "ec": str(d.pop("error_code", "") or ""),
                      "vr": str(d.pop("verdict_reason", "") or ""), "inp": _dump(d), "id": tid})
    for table, prefix, _, _ in NEW_KEYS:
        conn.execute(sa.text(f"UPDATE {table} SET key = '{prefix}' || id WHERE key = ''"))

    # render_chunk trùng (sequence_id, variant, fingerprint) → giữ dòng mới nhất.
    conn.execute(sa.text(
        "DELETE FROM render_chunk WHERE id NOT IN (SELECT max(id) FROM render_chunk "
        "GROUP BY sequence_id, variant, fingerprint)"))
    for table, cols, _ in GLOBAL:
        _soft_delete_global_dupes(conn, table, cols)

    # --- Unique một phần thay unique cả bảng ------------------------------------------------------------------------
    for table, old, _, _ in SCOPED:
        with op.batch_alter_table(table, schema=None) as b:
            b.drop_constraint(old, type_="unique")
    for table, _, cols, name in SCOPED:
        op.create_index(name, table, cols, unique=True, sqlite_where=sa.text(ALIVE))
    for table, cols, name in GLOBAL:
        op.create_index(name, table, cols, unique=True, sqlite_where=sa.text(GLOBAL_ALIVE))
    for table, _, cols, name in NEW_KEYS:
        op.create_index(name, table, cols, unique=True, sqlite_where=sa.text(ALIVE))
    op.create_index("ux_render_chunk_fingerprint", "render_chunk", ["sequence_id", "variant", "fingerprint"],
                    unique=True)


def downgrade() -> None:
    op.drop_index("ux_render_chunk_fingerprint", table_name="render_chunk")
    for table, _, _, name in NEW_KEYS:
        op.drop_index(name, table_name=table)
    for table, _, name in GLOBAL:
        op.drop_index(name, table_name=table)
    for table, _, _, name in SCOPED:
        op.drop_index(name, table_name=table)
    for table, old, cols, _ in SCOPED:
        with op.batch_alter_table(table, schema=None) as b:
            b.create_unique_constraint(old, cols)
    conn = op.get_bind()
    for tid, inputs, seed, ec, vr in conn.execute(sa.text(
            "SELECT id, inputs, seed, error_code, verdict_reason FROM take")).all():
        d = _load(inputs)
        if seed is not None:
            d["seed"] = seed
        if ec:
            d["error_code"] = ec
        if vr:
            d["verdict_reason"] = vr
        conn.execute(sa.text("UPDATE take SET inputs = :inp WHERE id = :id"), {"inp": _dump(d), "id": tid})
    for row in conn.execute(sa.text(
            "SELECT id, settings, custom, connection, model, axis_side, cut_in, cut_out FROM clip")).all():
        st, cu = _load(row[1]), _load(row[2])
        for k, v in zip(CLIP_FROM_SETTINGS, row[3:5]):
            if v:
                st[k] = v
        for k, v in zip(CLIP_FROM_CUSTOM, row[5:8]):
            if v:
                cu[k] = v
        conn.execute(sa.text("UPDATE clip SET settings = :st, custom = :cu WHERE id = :id"),
                     {"st": _dump(st), "cu": _dump(cu), "id": row[0]})
    for table, _, _, _ in NEW_KEYS:
        with op.batch_alter_table(table, schema=None) as b:
            b.drop_column('key')
    with op.batch_alter_table('render_chunk', schema=None) as b:
        b.drop_column('meta')
    with op.batch_alter_table('take', schema=None) as b:
        for col in ('verdict_reason', 'error_code', 'seed'):
            b.drop_column(col)
    with op.batch_alter_table('clip', schema=None) as b:
        for col in (*CLIP_FROM_CUSTOM, *CLIP_FROM_SETTINGS):
            b.drop_column(col)
