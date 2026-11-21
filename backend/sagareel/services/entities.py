"""CRUD chung cho thực thể ★ M1–M7: đọc, liệt kê phân trang cursor, sửa có kiểm `stale_write`, xoá mềm hai bước.

Chỉ phục vụ các bảng trong `ENTITIES` (thực thể ★ mới từ M1). Bảng M0 (kết nối provider có khoá API, công việc LLM,
…) có op riêng và không đi qua đây. `to_dict` vẫn che mọi cột `info={"secret": True}` cho an toàn.

Mọi ghi đi qua `ctx.uow()` → có change set/revision, hoàn tác/làm lại được; change set gắn `project_id` của thực thể
(tự lần theo cha: clip → sequence → project) để Ctrl+Z trong project tìm thấy.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, inspect, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeDecorator

from sagareel.core import rank, secrets
from sagareel.core.context import OpContext
from sagareel.core.errors import (
    AppError,
    ConfirmRequired,
    Conflict,
    NotFound,
    ValidationFailed,
)
from sagareel.core.ops import current_op
from sagareel.db.base import Base, JSONDictType, JSONListType, utcnow
from sagareel.db.models import (
    Asset,
    AssetKind,
    AssetVariant,
    BibleItem,
    ChannelStudy,
    Clip,
    DialogueLine,
    GroupNode,
    Preset,
    Project,
    ProjectPrompt,
    Scene,
    Sequence,
    SkillFile,
    Sound,
    TimelineItem,
    VoiceProfile,
)
from sagareel.services import confirm

ENTITIES: dict[str, type[Base]] = {
    cls.__tablename__: cls
    for cls in (
        Project, GroupNode, Sequence, Scene, AssetKind, Asset, AssetVariant, Clip, DialogueLine, VoiceProfile,
        TimelineItem, Sound, Preset, BibleItem, ChannelStudy, SkillFile, ProjectPrompt,
    )
}

LABELS: dict[str, str] = {
    "project": "dự án",
    "group_node": "nhóm tập",
    "sequence": "tập",
    "scene": "cảnh",
    "asset_kind": "loại tài sản",
    "asset": "tài sản",
    "asset_variant": "look",
    "clip": "clip",
    "dialogue_line": "lời thoại",
    "voice_profile": "hồ sơ giọng",
    "timeline_item": "mục trên timeline",
    "sound": "âm thanh",
    "preset": "mẫu",
    "bible_item": "mục Story Bible",
    "channel_study": "phân tích kênh",
    "skill_file": "file skill",
    "project_prompt": "prompt",
}

# Cột không sửa được qua `patch`: khoá, dấu thời gian sổ sách, và project sở hữu (không chuyển thực thể giữa project).
READONLY_FIELDS = frozenset({"id", "created_at", "updated_at", "deleted_at", "project_id"})
# Thêm theo loại: `skill_file.path` quyết định file nào được ghi ra đĩa → chỉ đổi qua `skill.update` (kiểm đường dẫn).
READONLY_BY_TYPE: dict[str, frozenset[str]] = {"skill_file": frozenset({"path"})}
# Kiểm giá trị theo loại sau khi kiểm kiểu: `fn(session, obj, clean_values) -> clean_values` (vd. preset → validate_data).
PATCH_VALIDATORS: dict[str, Callable[[Session, Any, dict[str, Any]], dict[str, Any]]] = {}

# Cha để lần ra project_id: bảng → (cột trỏ cha, bảng cha).
_PARENT: dict[str, tuple[str, str]] = {
    "scene": ("sequence_id", "sequence"),
    "clip": ("sequence_id", "sequence"),
    "dialogue_line": ("clip_id", "clip"),
    "asset_variant": ("asset_id", "asset"),
    "timeline_item": ("sequence_id", "sequence"),
}

# Bổ sung mô tả tác động khi xoá theo loại (vd. project thêm số tài sản/tập/clip): `fn(session, rows) -> dict`.
ImpactFn = Callable[[Session, list[Any]], dict[str, Any]]
IMPACT_EXTRA: dict[str, ImpactFn] = {}

MAX_LIMIT = 500
MAX_DELETE = 1000


# --- Tra cứu ----------------------------------------------------------------------------------------------------


def model_of(type_: str) -> type[Base]:
    cls = ENTITIES.get(type_)
    if cls is None:
        raise AppError(
            "unknown_entity_type",
            f"Không có loại thực thể '{type_}'.",
            hint=f"Loại hợp lệ: {', '.join(sorted(ENTITIES))}.",
            status=404,
        )
    return cls


def label_of(type_: str) -> str:
    return LABELS.get(type_, type_)


def get(s: Session, type_: str, id_: int) -> Any:
    """Thực thể chưa xoá mềm; không có / đã xoá → `not_found` (404)."""
    cls = model_of(type_)
    obj = s.get(cls, id_)
    if obj is None or obj.deleted_at is not None:
        raise NotFound(f"Không tìm thấy {label_of(type_)} #{id_}.")
    return obj


def project_id_of(s: Session, obj: Any) -> int | None:
    """Project sở hữu thực thể (lần theo cha); None nếu là thực thể toàn cục."""
    table = inspect(obj).mapper.local_table.name
    if table == "project":
        return obj.id
    if hasattr(obj, "project_id"):
        return obj.project_id
    parent = _PARENT.get(table)
    if parent is None:
        return None
    col, ptype = parent
    pobj = s.get(ENTITIES[ptype], getattr(obj, col))
    return project_id_of(s, pobj) if pobj is not None else None


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def to_dict(obj: Any) -> dict[str, Any]:
    """Mọi cột của thực thể; cột bí mật che thành `••••` + 4 ký tự cuối; JSON trả nguyên (dict/list thường)."""
    out: dict[str, Any] = {}
    for attr in inspect(obj).mapper.column_attrs:
        value = getattr(obj, attr.key)
        if secrets.is_secret(secrets.column_of(attr)):
            out[attr.key] = secrets.mask(value or "")
        else:
            out[attr.key] = _plain(value)
    return out


# --- Kiểm giá trị -------------------------------------------------------------------------------------------------


def _columns(cls: type[Base]) -> dict[str, Any]:
    return {a.key: secrets.column_of(a) for a in inspect(cls).column_attrs}


def _expected_type(column) -> type | tuple[type, ...]:
    t = column.type
    if isinstance(t, TypeDecorator):
        if isinstance(t, JSONDictType):
            return dict
        if isinstance(t, JSONListType):
            return list
        t = t.impl_instance
    py = t.python_type
    if py is float:
        return (int, float)
    return py


def _coerce(name: str, column, value: Any) -> tuple[Any, dict | None]:
    if value is None:
        if column.nullable:
            return None, None
        return None, {"loc": ["values", name], "msg": f"Trường '{name}' không được để trống.", "type": "null"}
    expected = _expected_type(column)
    if expected is datetime and isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None, {"loc": ["values", name], "msg": f"Trường '{name}' phải là thời điểm ISO 8601.",
                          "type": "type_error"}
    ok = isinstance(value, expected) and not (isinstance(value, bool) and expected in (int, (int, float)))
    if not ok:
        tname = {dict: "object", list: "mảng", str: "chuỗi", int: "số nguyên", bool: "true/false",
                 datetime: "thời điểm"}.get(expected if isinstance(expected, type) else float, "số")
        return None, {"loc": ["values", name], "msg": f"Trường '{name}' phải có kiểu {tname}.", "type": "type_error"}
    if expected == (int, float):
        value = float(value)
    return value, None


def check_values(type_: str, values: dict[str, Any]) -> dict[str, Any]:
    """Kiểm tên cột + kiểu giá trị; trả bản đã chuẩn hoá. Lỗi: `unknown_field`/`readonly_field` (422) hoặc
    `validation_failed`."""
    cls = model_of(type_)
    if not values:
        raise ValidationFailed("Không có trường nào để sửa.", hint="Truyền ít nhất một cặp trường → giá trị.")
    cols = _columns(cls)
    unknown = sorted(k for k in values if k not in cols)
    if unknown:
        raise AppError("unknown_field", f"{label_of(type_).capitalize()} không có trường: {', '.join(unknown)}.",
                       hint=f"Trường hợp lệ: {', '.join(sorted(set(cols) - READONLY_FIELDS))}.", status=422,
                       extra={"fields": unknown})
    extra_ro = READONLY_BY_TYPE.get(type_, frozenset())
    readonly = sorted(k for k in values if k in READONLY_FIELDS or k in extra_ro or secrets.is_secret(cols[k]))
    if readonly:
        raise AppError("readonly_field", f"Không sửa được trường: {', '.join(readonly)}.", status=422,
                       extra={"fields": readonly})
    out: dict[str, Any] = {}
    errors = []
    for k, v in values.items():
        coerced, err = _coerce(k, cols[k], v)
        if err is None and k == "rank":
            try:
                rank.validate(coerced)
            except ValueError:
                err = {"loc": ["values", k], "msg": "rank không phải khoá thứ tự hợp lệ (dùng core.rank).",
                       "type": "rank_invalid"}
        if err:
            errors.append(err)
        else:
            out[k] = coerced
    if errors:
        raise ValidationFailed("Giá trị không hợp lệ.", hint="Xem `extra.errors`.", extra={"errors": errors})
    return out


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def stale_write(type_: str, id_: int, current: datetime) -> AppError:
    return AppError(
        "stale_write",
        f"{label_of(type_).capitalize()} #{id_} đã bị thay đổi bởi người/agent khác kể từ lúc bạn tải.",
        hint="Tải lại bản mới nhất rồi sửa lại.",
        status=409,
        extra={"current_updated_at": current.isoformat()},
    )


def check_fresh(type_: str, obj: Any, expected_updated_at: datetime | None) -> None:
    if expected_updated_at is not None and _utc(obj.updated_at) != _utc(expected_updated_at):
        raise stale_write(type_, obj.id, obj.updated_at)


def integrity_error(type_: str, e: IntegrityError) -> AppError:
    msg = str(e.orig)
    if "UNIQUE" in msg:
        return Conflict(f"Mã (key) này đã được dùng cho {label_of(type_)} khác.", code="key_taken",
                        hint="Chọn mã khác (mã là duy nhất trong phạm vi cha, trong các bản chưa xoá).")
    if "FOREIGN KEY" in msg:
        return AppError("invalid_reference", f"{label_of(type_).capitalize()} trỏ tới thực thể không tồn tại.",
                        hint="Kiểm tra các trường id tham chiếu.", status=422)
    return AppError("integrity_error", f"Dữ liệu {label_of(type_)} vi phạm ràng buộc.", status=422)


# --- Ghi ----------------------------------------------------------------------------------------------------------


def uow(ctx: OpContext, summary: str, default_op: str):
    """`ctx.uow()`; gọi ngoài op (service gọi trực tiếp, fixture test) thì change set mang tên `default_op`."""
    return ctx.uow(summary, op_name=None if current_op.get() else default_op)


def patch(
    ctx: OpContext,
    type_: str,
    id_: int,
    values: dict[str, Any],
    expected_updated_at: datetime | None = None,
) -> dict[str, Any]:
    """Sửa các cột của một thực thể qua UoW (hoàn tác được). Lệch `expected_updated_at` → `stale_write` (409)."""
    clean = check_values(type_, values)
    u = uow(ctx, f"Sửa {label_of(type_)} #{id_}", "entity.patch")
    try:
        with u as s:
            obj = get(s, type_, id_)
            check_fresh(type_, obj, expected_updated_at)
            u.project_id = project_id_of(s, obj)
            validator = PATCH_VALIDATORS.get(type_)
            if validator is not None:
                clean = validator(s, obj, clean)
            for k, v in clean.items():
                setattr(obj, k, v)
            s.flush()
            out = to_dict(obj)
    except IntegrityError as e:
        raise integrity_error(type_, e) from None
    return out


# --- Liệt kê ------------------------------------------------------------------------------------------------------


def _bad_cursor() -> AppError:
    return AppError("invalid_cursor", "Cursor phân trang không hợp lệ.",
                    hint="Dùng đúng `next_cursor` của trang trước, hoặc bỏ trống để lấy trang đầu.", status=422)


def list_page(
    s: Session,
    type_: str,
    filters: dict[str, Any] | None,
    cursor: str | None,
    limit: int = 100,
) -> dict[str, Any]:
    """Một trang thực thể chưa xoá. Lọc bằng nhau (giá trị list = IN, null = IS NULL). Có cột `rank` → sắp theo
    (rank, id), cursor `"<rank>:<id>"`; không thì theo id, cursor `"<id>"`."""
    cls = model_of(type_)
    cols = _columns(cls)
    limit = max(1, min(int(limit), MAX_LIMIT))
    q = select(cls).where(cls.deleted_at.is_(None))
    for k, v in (filters or {}).items():
        if k not in cols or secrets.is_secret(cols[k]):
            raise AppError("unknown_field", f"Không lọc được theo trường '{k}' của {label_of(type_)}.", status=422,
                           extra={"fields": [k]})
        attr = getattr(cls, k)
        if v is None:
            q = q.where(attr.is_(None))
        elif isinstance(v, list):
            q = q.where(attr.in_(v))
        else:
            q = q.where(attr == v)
    has_rank = "rank" in cols
    if cursor:
        if has_rank:
            r, sep, i = cursor.rpartition(":")
            if not sep or not i.isdigit():
                raise _bad_cursor()
            q = q.where(or_(cls.rank > r, and_(cls.rank == r, cls.id > int(i))))
        else:
            if not cursor.isdigit():
                raise _bad_cursor()
            q = q.where(cls.id > int(cursor))
    q = q.order_by(cls.rank, cls.id) if has_rank else q.order_by(cls.id)
    rows = s.scalars(q.limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        last = rows[-1]
        next_cursor = f"{last.rank}:{last.id}" if has_rank else str(last.id)
    return {"items": [to_dict(r) for r in rows], "next_cursor": next_cursor}


# --- Xoá mềm hai bước ---------------------------------------------------------------------------------------------


def delete_op(type_: str) -> str:
    """Tên op phá huỷ dùng trong HMAC của confirm_token (gắn token với loại thực thể)."""
    return f"{type_}.delete"


def _summary(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": obj.id}
    for k in ("key", "name", "title"):
        if hasattr(obj, k):
            out[k] = getattr(obj, k)
    return out


def impact(s: Session, type_: str, rows: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": type_,
        "label": label_of(type_),
        "count": len(rows),
        "items": [_summary(r) for r in rows],
    }
    extra = IMPACT_EXTRA.get(type_)
    if extra is not None:
        out.update(extra(s, rows))
    return out


def _norm_ids(ids: list[int]) -> list[int]:
    clean = sorted(set(ids))
    if not clean:
        raise ValidationFailed("Cần ít nhất một id để xoá.")
    if len(clean) > MAX_DELETE:
        raise ValidationFailed(f"Mỗi lần xoá tối đa {MAX_DELETE} thực thể.")
    return clean


def soft_delete_preview(ctx: OpContext, type_: str, ids: list[int]) -> dict[str, Any]:
    """Tác động của việc xoá + `confirm_token` (10 phút) dạng `"<type>:<id,id>:<mã>"` cho `soft_delete`."""
    model_of(type_)
    clean = _norm_ids(ids)
    with ctx.services.db.session() as s:
        rows = [get(s, type_, i) for i in clean]
        imp = impact(s, type_, rows)
        inner = confirm.make_token(s, delete_op(type_), clean, imp)
    token = f"{type_}:{','.join(map(str, clean))}:{inner}"
    return {"impact": imp, "confirm_token": token, "expires_in_s": confirm.TTL_S}


def parse_token(token: str | None) -> tuple[str, list[int], str]:
    """`"<type>:<ids>:<mã>"` → (type, ids, mã). Sai dạng → `confirm_required`."""
    hint = "Gọi <loại>.delete_preview để lấy confirm_token mới."
    if not token:
        raise ConfirmRequired("Thao tác này cần xác nhận bằng confirm_token.", hint=hint)
    parts = token.split(":", 2)
    if len(parts) != 3 or parts[0] not in ENTITIES:
        raise ConfirmRequired("confirm_token không hợp lệ.", hint=hint)
    try:
        ids = [int(x) for x in parts[1].split(",")]
    except ValueError:
        raise ConfirmRequired("confirm_token không hợp lệ.", hint=hint) from None
    return parts[0], ids, parts[2]


def soft_delete(ctx: OpContext, confirm_token: str | None, *, expect_type: str | None = None) -> dict[str, Any]:
    """Xoá mềm (đặt `deleted_at`) các thực thể trong token — một change set, hoàn tác được.

    Token sai/hết hạn/tác động đã đổi kể từ lúc xem trước → `confirm_required` (409).
    """
    type_, ids, inner = parse_token(confirm_token)
    if expect_type is not None and type_ != expect_type:
        raise ConfirmRequired(f"confirm_token này không dành cho {label_of(expect_type)}.",
                              hint=f"Gọi {expect_type}.delete_preview để lấy confirm_token mới.")
    u = uow(ctx, f"Xoá {len(ids)} {label_of(type_)}", delete_op(type_))
    with u as s:
        rows = [get(s, type_, i) for i in ids]
        confirm.verify(s, delete_op(type_), ids, impact(s, type_, rows), inner)
        projects = {project_id_of(s, r) for r in rows}
        u.project_id = projects.pop() if len(projects) == 1 else None
        now = utcnow()
        for r in rows:
            r.deleted_at = now
    return {"type": type_, "deleted": ids}
