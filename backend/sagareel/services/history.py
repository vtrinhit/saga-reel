"""Lịch sử phiên bản: liệt kê, hoàn tác, làm lại, khôi phục (spec §5.3, §23.4).

Quy tắc xung đột khi hoàn tác/làm lại change set C: với mỗi thực thể e của C, xét các revision của e thuộc change set
mới hơn C.
- Change set "thường" (kể cả `history.restore`) chưa bị hoàn tác → xung đột `undo_conflict`.
- Change set đã bị hoàn tác → bỏ qua.
- Change set `history.undo`/`history.redo` (chưa hoàn tác) → không tự chặn; chỉ xung đột nếu trạng thái hiện tại của e
  khác trạng thái C mong đợi (C.after khi hoàn tác, C.before khi làm lại), so sánh bỏ qua `updated_at` và cột bí mật,
  coi "không có dòng" ≡ "đã xoá mềm". Nhờ vậy Ctrl+Z / Ctrl+Shift+Z nối tiếp hoạt động, còn làm lại sai thứ tự thì bị
  chặn.

Hoàn tác/làm lại mặc định (không truyền id) bỏ qua các change set `history.undo`/`history.redo` để Ctrl+Z hai lần không
thành "hoàn tác rồi làm lại". Không hoàn tác/làm lại trực tiếp chính các change set đó.

Lưu ý: module này định nghĩa hàm `list` (giữ tên `history.list` theo interface) nên không dùng builtin `list` bên dưới.
"""

import builtins
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import DateTime, select
from sqlalchemy.orm import Mapper, Session
from sqlalchemy.types import TypeDecorator

from sagareel.core import secrets
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Conflict, NotFound
from sagareel.core.uow import (
    UnitOfWork,
    bookkeeping_columns,
    entity_type_of,
    read_snapshot,
)
from sagareel.db.base import Base, utcnow
from sagareel.db.models import ChangeSet, Revision

OP_UNDO = "history.undo"
OP_REDO = "history.redo"
OP_RESTORE = "history.restore"
# Change set sinh ra bởi hoàn tác/làm lại: không phải đích của Ctrl+Z, không tự chặn hoàn tác change set khác.
TRANSPARENT_OPS = (OP_UNDO, OP_REDO)


def _skip_on_apply(mapper: Mapper) -> frozenset[str]:
    """Cột sổ sách không bao giờ được áp ngược: `updated_at` (để onupdate đóng dấu thời điểm hoàn tác/khôi phục) và
    cột `info={"bookkeeping": True}` (trạng thái chạy như `health` — giữ giá trị hiện tại)."""
    return bookkeeping_columns(mapper)


# --- Schema -----------------------------------------------------------------------------------------------------


class EntityRef(BaseModel):
    type: str
    id: int
    revision_id: int


class ChangeSetOut(BaseModel):
    id: int
    op_name: str
    actor: str
    summary: str
    created_at: datetime
    undone_at: datetime | None
    entities: builtins.list[EntityRef]


class HistoryPage(BaseModel):
    items: builtins.list[ChangeSetOut]
    next_cursor: int | None = None


# --- Đọc --------------------------------------------------------------------------------------------------------


def _outs(s: Session, change_sets: Sequence[ChangeSet]) -> builtins.list[ChangeSetOut]:
    ids = [cs.id for cs in change_sets]
    refs: dict[int, builtins.list[EntityRef]] = {i: [] for i in ids}
    if ids:
        for rev in s.scalars(select(Revision).where(Revision.change_set_id.in_(ids)).order_by(Revision.id)):
            refs[rev.change_set_id].append(EntityRef(type=rev.entity_type, id=rev.entity_id, revision_id=rev.id))
    return [
        ChangeSetOut(
            id=cs.id, op_name=cs.op_name, actor=cs.actor, summary=cs.summary, created_at=cs.created_at,
            undone_at=cs.undone_at, entities=refs[cs.id],
        )
        for cs in change_sets
    ]


def get(ctx: OpContext, change_set_id: int) -> ChangeSetOut:
    with ctx.services.db.session() as s:
        return _outs(s, [_get_change_set(s, change_set_id)])[0]


def list(
    ctx: OpContext,
    entity_type: str | None = None,
    entity_id: int | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> HistoryPage:
    """Change set mới nhất trước. Lọc theo thực thể nếu có, ngược lại theo `ctx.project_id` (None = toàn cục).

    `cursor` = `next_cursor` của trang trước (lấy các change set có id nhỏ hơn).
    """
    limit = max(1, min(limit, 200))
    q = select(ChangeSet)
    if entity_type is not None or entity_id is not None:
        sub = select(Revision.change_set_id)
        if entity_type is not None:
            sub = sub.where(Revision.entity_type == entity_type)
        if entity_id is not None:
            sub = sub.where(Revision.entity_id == entity_id)
        q = q.where(ChangeSet.id.in_(sub))
    else:
        q = q.where(_project_filter(ctx.project_id))
    if cursor is not None:
        q = q.where(ChangeSet.id < cursor)
    with ctx.services.db.session() as s:
        rows = s.scalars(q.order_by(ChangeSet.id.desc()).limit(limit + 1)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return HistoryPage(items=_outs(s, rows), next_cursor=rows[-1].id if has_more else None)


# --- Hoàn tác / làm lại / khôi phục -----------------------------------------------------------------------------


def _key_conflict(fn):
    """Khôi phục dòng đã xoá mềm khi đã có dòng chưa xoá cùng key (unique một phần, migration 0005) → 409 rõ ràng."""
    import functools

    from sqlalchemy.exc import IntegrityError

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except IntegrityError as e:
            if "UNIQUE" not in str(e.orig):
                raise
            raise Conflict("Không khôi phục được: mã (key) đã được dùng cho một mục khác đang tồn tại.",
                           code="key_taken", hint="Đổi mã của mục đang dùng (hoặc xoá nó) rồi thử lại.") from None

    return wrapper


@_key_conflict
def undo(ctx: OpContext, change_set_id: int | None = None) -> ChangeSetOut:
    """Hoàn tác change set (mặc định: mới nhất của `ctx.actor` trong `ctx.project_id` chưa hoàn tác).

    Trả về change set đã bị hoàn tác; bản thân thao tác ghi một change set `history.undo`.
    """
    u = UnitOfWork(ctx.services.db, actor=ctx.actor, op_name=OP_UNDO, project_id=ctx.project_id)
    with u as s:
        if change_set_id is None:
            cs = s.scalars(
                _own_change_sets(ctx).where(ChangeSet.undone_at.is_(None)).order_by(ChangeSet.id.desc()).limit(1)
            ).first()
            if cs is None:
                raise NotFound("Không có thao tác nào để hoàn tác.", code="nothing_to_undo")
        else:
            cs = _get_change_set(s, change_set_id)
            _reject_transparent(cs)
            if cs.undone_at is not None:
                raise Conflict(f"Thao tác #{cs.id} đã được hoàn tác rồi.", code="already_undone",
                               hint="Dùng history.redo để làm lại.")
        revs = _revisions(s, cs.id)
        _check_conflicts(s, cs, revs, "undo")
        for rev in reversed(revs):
            _apply(s, u, _mapper_for(rev.entity_type), rev.entity_id, rev.before_json)
        cs.undone_at = utcnow()
        u.project_id = cs.project_id
        u.summary = f"Hoàn tác #{cs.id} ({cs.op_name})"
        target = cs

        def link(_s: Session, new_cs: ChangeSet | None) -> None:
            target.undone_by = new_cs.id if new_cs is not None else None

        u.before_commit(link)
        target_id = cs.id
    return get(ctx, target_id)


@_key_conflict
def redo(ctx: OpContext, change_set_id: int | None = None) -> ChangeSetOut:
    """Làm lại change set đã hoàn tác (mặc định: cái được `ctx.actor` hoàn tác gần nhất trong project)."""
    u = UnitOfWork(ctx.services.db, actor=ctx.actor, op_name=OP_REDO, project_id=ctx.project_id)
    with u as s:
        if change_set_id is None:
            cs = s.scalars(
                _own_change_sets(ctx)
                .where(ChangeSet.undone_at.is_not(None))
                .order_by(ChangeSet.undone_by.desc().nulls_last(), ChangeSet.undone_at.desc(), ChangeSet.id.desc())
                .limit(1)
            ).first()
            if cs is None:
                raise NotFound("Không có thao tác nào để làm lại.", code="nothing_to_redo")
        else:
            cs = _get_change_set(s, change_set_id)
            _reject_transparent(cs)
            if cs.undone_at is None:
                raise Conflict(f"Thao tác #{cs.id} chưa bị hoàn tác nên không thể làm lại.", code="not_undone")
        revs = _revisions(s, cs.id)
        _check_conflicts(s, cs, revs, "redo")
        for rev in revs:
            _apply(s, u, _mapper_for(rev.entity_type), rev.entity_id, rev.after_json)
        cs.undone_at = None
        cs.undone_by = None
        u.project_id = cs.project_id
        u.summary = f"Làm lại #{cs.id} ({cs.op_name})"
        target_id = cs.id
    return get(ctx, target_id)


@_key_conflict
def restore(ctx: OpContext, entity_type: str, entity_id: int, revision_id: int) -> ChangeSetOut:
    """Đưa một thực thể về phiên bản ngay sau revision `revision_id` (giải quyết xung đột thủ công).

    Ghi change set `history.restore` (hoàn tác được như thao tác thường). Không kiểm tra xung đột.
    """
    mapper = _mapper_for(entity_type)
    u = UnitOfWork(ctx.services.db, actor=ctx.actor, op_name=OP_RESTORE, project_id=ctx.project_id)
    with u as s:
        rev = s.get(Revision, revision_id)
        if rev is None:
            raise NotFound(f"Không tìm thấy phiên bản #{revision_id}.", code="revision_not_found")
        if (rev.entity_type, rev.entity_id) != (entity_type, entity_id):
            raise AppError(
                "revision_mismatch",
                f"Phiên bản #{revision_id} không thuộc thực thể {entity_type} #{entity_id}.",
                hint="Dùng history.list với entity_type/entity_id để xem các phiên bản của thực thể.",
            )
        if u.project_id is None:
            u.project_id = s.get(ChangeSet, rev.change_set_id).project_id
        u.summary = f"Khôi phục {entity_type} #{entity_id} về phiên bản #{revision_id}"
        _apply(s, u, mapper, entity_id, rev.after_json)
    if u.change_set_id is None:
        raise Conflict(f"{entity_type} #{entity_id} đã ở đúng phiên bản #{revision_id}.", code="no_change")
    return get(ctx, u.change_set_id)


# --- Nội bộ -----------------------------------------------------------------------------------------------------


def _project_filter(project_id: int | None):
    return ChangeSet.project_id.is_(None) if project_id is None else ChangeSet.project_id == project_id


def _own_change_sets(ctx: OpContext):
    return select(ChangeSet).where(
        ChangeSet.actor == ctx.actor,
        _project_filter(ctx.project_id),
        ChangeSet.op_name.not_in(TRANSPARENT_OPS),
    )


def _get_change_set(s: Session, change_set_id: int) -> ChangeSet:
    cs = s.get(ChangeSet, change_set_id)
    if cs is None:
        raise NotFound(f"Không tìm thấy thao tác #{change_set_id} trong lịch sử.", code="change_set_not_found")
    return cs


def _reject_transparent(cs: ChangeSet) -> None:
    if cs.op_name in TRANSPARENT_OPS:
        raise AppError(
            "invalid_history_target",
            f"Thao tác #{cs.id} là một lần hoàn tác/làm lại, không hoàn tác hay làm lại trực tiếp được.",
            hint="Dùng history.undo / history.redo trên thao tác gốc.",
        )


def _revisions(s: Session, change_set_id: int) -> builtins.list[Revision]:
    return [*s.scalars(select(Revision).where(Revision.change_set_id == change_set_id).order_by(Revision.id))]


def _mapper_for(entity_type: str) -> Mapper:
    """entity_type (= tên bảng) → mapper; mọi lớp đã map, không chỉ `Tracked`."""
    for mapper in Base.registry.mappers:
        if entity_type_of(mapper) == entity_type:
            return mapper
    raise AppError("unknown_entity_type", f"Không có loại thực thể '{entity_type}'.")


def _comparable(mapper: Mapper, snap: dict[str, Any] | None) -> dict[str, Any] | None:
    """Dạng so sánh trạng thái: bỏ `updated_at` và cột bí mật; không có dòng ≡ đã xoá mềm."""
    if snap is None or snap.get("deleted_at") is not None:
        return None
    skip = {a.key for a in mapper.column_attrs if secrets.is_secret(secrets.column_of(a))} | _skip_on_apply(mapper)
    return {k: v for k, v in snap.items() if k not in skip}


def _check_conflicts(s: Session, cs: ChangeSet, revs: Sequence[Revision], mode: Literal["undo", "redo"]) -> None:
    conn = s.connection()
    conflicts = []
    for rev in revs:
        later = s.execute(
            select(Revision.change_set_id, ChangeSet.op_name, ChangeSet.undone_at)
            .join(ChangeSet, ChangeSet.id == Revision.change_set_id)
            .where(
                Revision.entity_type == rev.entity_type,
                Revision.entity_id == rev.entity_id,
                Revision.change_set_id > cs.id,
            )
            .order_by(Revision.change_set_id)
        ).all()
        if not later:
            continue
        blocking = [r.change_set_id for r in later if r.undone_at is None and r.op_name not in TRANSPARENT_OPS]
        if not blocking:
            mapper = _mapper_for(rev.entity_type)
            expected = rev.after_json if mode == "undo" else rev.before_json
            current = read_snapshot(conn, mapper, rev.entity_id)
            if _comparable(mapper, current) == _comparable(mapper, expected):
                continue
            blocking = [later[-1].change_set_id]
        conflicts.append({"type": rev.entity_type, "id": rev.entity_id, "change_set_id": blocking[-1]})
    if conflicts:
        action = "hoàn tác" if mode == "undo" else "làm lại"
        raise AppError(
            "undo_conflict",
            f"Không thể {action} thao tác #{cs.id}: {len(conflicts)} thực thể đã bị thay đổi sau đó.",
            hint="Dùng history.restore để khôi phục từng thực thể về phiên bản mong muốn.",
            status=409,
            extra={"entities": conflicts},
        )


def _decode(column, value: Any) -> Any:
    if value is None:
        return None
    col_type = column.type
    if isinstance(col_type, TypeDecorator):  # TypeDecorator không ủy quyền python_type → xét kiểu impl
        col_type = col_type.impl_instance
    if isinstance(col_type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    if isinstance(value, (dict, builtins.list)):
        return json.loads(json.dumps(value))  # bản sao thuần, không dính MutableDict của revision
    return value


def _refuse_secret_loss(mapper: Mapper, entity_id: int, target: dict[str, Any]) -> None:
    """Chèn lại dòng đã xoá cứng: snapshot không chứa bí mật nên không khôi phục được nó → từ chối thay vì lặng lẽ
    để trống (bí mật rỗng thì không có gì để mất)."""
    empty = {secrets.SECRET_KEY: secrets.digest("")}
    lost = [
        attr.key
        for attr in mapper.column_attrs
        if secrets.is_secret(secrets.column_of(attr)) and target.get(attr.key) not in (None, empty)
    ]
    if lost:
        raise AppError(
            "secret_lost",
            f"Không thể khôi phục {entity_type_of(mapper)} #{entity_id}: thực thể đã bị xoá hẳn và lịch sử không lưu "
            f"giá trị bí mật ({', '.join(lost)}).",
            hint="Nhập lại khoá API cho kết nối này sau khi khôi phục",
            status=409,
            extra={"entity": {"type": entity_type_of(mapper), "id": entity_id}, "columns": lost},
        )


def _apply(s: Session, u: UnitOfWork, mapper: Mapper, entity_id: int, target: dict[str, Any] | None) -> None:
    """Đưa thực thể về snapshot `target` (None = không tồn tại). Không bao giờ ghi cột bí mật."""
    cls = mapper.class_
    obj = s.get(cls, entity_id)
    if target is None:
        if obj is None:
            return
        u.track(obj)
        if "deleted_at" in mapper.columns:
            if obj.deleted_at is None:
                obj.deleted_at = utcnow()
        else:
            s.delete(obj)
        return
    created = obj is None
    if created:
        _refuse_secret_loss(mapper, entity_id, target)
        obj = cls()
        setattr(obj, mapper.get_property_by_column(mapper.primary_key[0]).key, entity_id)
    u.track(obj)
    skip = _skip_on_apply(mapper)
    for attr in mapper.column_attrs:
        column = secrets.column_of(attr)
        if (
            column.primary_key
            or attr.key in skip
            or secrets.is_secret(column)
            or attr.key not in target
        ):
            continue
        value = _decode(column, target[attr.key])
        if created or getattr(obj, attr.key) != value:
            setattr(obj, attr.key, value)
    if created:
        s.add(obj)
