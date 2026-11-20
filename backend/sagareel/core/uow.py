"""UnitOfWork — đường ghi duy nhất (spec §5.3).

Mọi thay đổi DB của một op đi qua một `UnitOfWork`. Khi khối `with` kết thúc bình thường, với mỗi thực thể theo dõi
(model có `__tracked__ = True`, hoặc object đăng ký qua `uow.track(obj)`) bị thêm/sửa/xoá, ghi một `revision`
(`before_json`/`after_json`), tất cả gom vào một `change_set` — cùng transaction với dữ liệu.

```python
u = ctx.uow("Đổi tên kết nối")
with u as s:
    s.get(ProviderConnection, cid).name = "mới"
u.change_set_id  # id change set, hoặc None nếu không có gì đổi
```

Snapshot "trước" đọc thẳng từ DB ở lần flush đầu tiên chạm tới thực thể (nên bắt được cả sửa JSON tại chỗ);
snapshot "sau" đọc từ DB sau flush cuối. Thực thể có before == after không sinh revision; không có revision nào thì
không tạo change set. Lưu ý: `session.execute(update(...))`/`delete(...)` hàng loạt đi vòng qua ORM nên KHÔNG được
ghi lịch sử — đừng dùng chúng cho bảng theo dõi.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Mapper, Session

from sagareel.core import secrets
from sagareel.core.errors import AppError
from sagareel.core.ops import CommittedChange, committed_changes, current_op
from sagareel.db.models import ChangeSet, Revision
from sagareel.db.session import Database

EntityKey = tuple[str, int]  # (entity_type = tên bảng, entity_id)
CommitHook = Callable[[Session, ChangeSet | None], None]
# Cột chỉ để sổ sách: không tính là "thay đổi" khi so before/after (vẫn được lưu trong snapshot).
# Ngoài `updated_at`, model khai báo thêm cột trạng thái chạy (vd. `provider_connection.health`) bằng
# `info={"bookkeeping": True}` — xem `bookkeeping_columns(mapper)`.
BOOKKEEPING_COLUMNS = frozenset({"updated_at"})
BOOKKEEPING_INFO = "bookkeeping"


def entity_type_of(mapper: Mapper) -> str:
    return mapper.local_table.name


def bookkeeping_columns(mapper: Mapper | None) -> frozenset[str]:
    """Khoá thuộc tính sổ sách của model: `updated_at` + mọi cột có `info={"bookkeeping": True}`.

    Các cột này không tính là thay đổi có nghĩa (không sinh revision/change set), bị bỏ qua khi lịch sử so trạng thái
    để phát hiện xung đột, và hoàn tác/làm lại/khôi phục không bao giờ ghi chúng.
    """
    if mapper is None:
        return BOOKKEEPING_COLUMNS
    extra = {a.key for a in mapper.column_attrs if secrets.column_of(a).info.get(BOOKKEEPING_INFO)}
    return BOOKKEEPING_COLUMNS | extra


def read_snapshot(conn: Connection, mapper: Mapper, entity_id: int) -> dict[str, Any] | None:
    """Snapshot của dòng hiện có trong DB (trong transaction của `conn`), `None` nếu không có dòng."""
    row = conn.execute(select(mapper.local_table).where(mapper.primary_key[0] == entity_id)).mappings().first()
    if row is None:
        return None
    return secrets.snapshot_with(mapper, lambda attr: row[secrets.column_of(attr)])


def meaningful(snap: dict[str, Any] | None, mapper: Mapper | None = None) -> dict[str, Any] | None:
    """Phần snapshot có nghĩa khi so sánh: bỏ cột sổ sách (`bookkeeping_columns(mapper)`).

    Lần lưu chỉ làm đổi `updated_at` hay cột trạng thái chạy (vd. `health`) không sinh revision/change set.
    """
    if snap is None:
        return None
    skip = bookkeeping_columns(mapper)
    return {k: v for k, v in snap.items() if k not in skip}


MOVE_COLUMNS = frozenset({"rank", "scene_id", "sequence_id", "clip_id", "parent_id"})


def change_kind(before: dict[str, Any] | None, after: dict[str, Any] | None, mapper: Mapper | None = None) -> str:
    """Loại thay đổi của một thực thể trong change set: created / deleted / moved (chỉ đổi vị trí) / updated."""
    if before is None:
        return "created"
    if after is None:
        return "deleted"
    was, now = before.get("deleted_at"), after.get("deleted_at")
    if was is None and now is not None:
        return "deleted"
    if was is not None and now is None:
        return "created"  # khôi phục
    changed = {k for k, v in (meaningful(after, mapper) or {}).items() if before.get(k) != v}
    return "moved" if changed and changed <= MOVE_COLUMNS else "updated"


def db_busy() -> AppError:
    return AppError(
        "db_busy",
        "Cơ sở dữ liệu đang bận, thử lại sau",
        hint="Một thao tác ghi khác đang giữ khoá cơ sở dữ liệu; thử lại sau giây lát.",
        status=503,
    )


def _raise_if_busy(exc: BaseException | None) -> None:
    """Đổi lỗi SQLite "database is locked" (hết busy_timeout) thành `AppError("db_busy", status=503)`."""
    if isinstance(exc, OperationalError) and "database is locked" in str(exc.orig):
        raise db_busy() from exc


@dataclass
class _Record:
    mapper: Mapper
    entity_id: int
    before: dict[str, Any] | None


class UnitOfWork(AbstractContextManager):
    """Context manager trả `Session`; commit kèm change set + revision khi khối kết thúc không lỗi.

    `op_name` mặc định lấy từ `core.ops.current_op` (do `invoke`/`execute` đặt); truyền tường minh thì thắng.
    Sau khi thoát: `change_set_id` (None nếu không có thay đổi hoặc bị rollback) và `changed` (danh sách
    `(entity_type, entity_id)` theo thứ tự chạm lần đầu).
    """

    def __init__(
        self,
        db: Database,
        *,
        actor: str,
        op_name: str | None = None,
        project_id: int | None = None,
        summary: str = "",
    ) -> None:
        op_name = op_name or current_op.get()
        if not op_name:
            raise ValueError("UnitOfWork cần op_name: truyền tường minh hoặc chạy bên trong một op (current_op).")
        self.db = db
        self.actor = actor
        self.op_name = op_name
        self.project_id = project_id
        self.summary = summary
        self.change_set_id: int | None = None
        self.changed: list[EntityKey] = []
        self.kinds: list[tuple[str, int, str]] = []
        self._records: dict[EntityKey, _Record] = {}
        self._forced: dict[int, Any] = {}  # id(obj) → obj (giữ tham chiếu mạnh để id không bị tái dùng)
        self._hooks: list[CommitHook] = []
        self._session: Session | None = None
        self._cm: AbstractContextManager[Session] | None = None
        self._finalizing = False

    # --- API ----------------------------------------------------------------------------------------------------

    def track(self, obj: Any) -> None:
        """Theo dõi lịch sử cho một object của model không `Tracked` (vd. `GlobalSettings`) trong UoW này."""
        self._forced[id(obj)] = obj

    def before_commit(self, hook: CommitHook) -> None:
        """Gọi `hook(session, change_set | None)` sau khi ghi revision, ngay trước commit (cùng transaction).

        Chỉ dùng cho việc sổ sách (vd. `undone_by`): thay đổi trong hook không được ghi lịch sử.
        """
        self._hooks.append(hook)

    # --- Context manager ----------------------------------------------------------------------------------------

    def __enter__(self) -> Session:
        if self._cm is not None:
            raise RuntimeError("UnitOfWork chỉ dùng được một lần.")
        # Khoá ghi ngay: chọn đích, kiểm xung đột và snapshot "trước" đều nằm trong khoá ghi (không có tranh chấp
        # giữa lúc đọc và lúc ghi).
        self._cm = cm = self.db.session(immediate=True)
        s = self._session = cm.__enter__()
        try:
            s.connection()  # BEGIN IMMEDIATE ngay: hết busy_timeout mà chưa có khoá → db_busy trước khi chạy khối
        except BaseException as e:
            cm.__exit__(type(e), e, e.__traceback__)
            _raise_if_busy(e)
            raise
        event.listen(s, "before_flush", self._before_flush)
        event.listen(s, "after_flush", self._after_flush)
        return s

    def __exit__(self, exc_type, exc, tb) -> bool:
        s, cm = self._session, self._cm
        assert s is not None and cm is not None, "UnitOfWork chưa được mở"
        try:
            if exc_type is not None:
                cm.__exit__(exc_type, exc, tb)  # rollback (không nuốt lỗi)
                _raise_if_busy(exc)
                return False  # ném lại lỗi gốc
            try:
                self._finalize(s)
            except BaseException as e:
                self._reset_result()
                cm.__exit__(type(e), e, e.__traceback__)
                _raise_if_busy(e)
                raise
            try:
                cm.__exit__(None, None, None)  # commit
            except BaseException as e:
                self._reset_result()
                _raise_if_busy(e)
                raise
            self._report_committed()
            return False
        finally:
            event.remove(s, "before_flush", self._before_flush)
            event.remove(s, "after_flush", self._after_flush)

    # --- Nội bộ -------------------------------------------------------------------------------------------------

    def _is_tracked(self, obj: Any) -> bool:
        return bool(getattr(type(obj), "__tracked__", False)) or id(obj) in self._forced

    def _before_flush(self, session: Session, flush_context, instances) -> None:
        if self._finalizing:
            return
        # Không lọc bằng session.is_modified(): nó có thể trả False trong khi ORM vẫn phát UPDATE (vd. ghi cùng giá trị
        # vào JSON tại chỗ). Ghi nhận mọi object dirty; `_finalize` so snapshot để quyết định có revision hay không.
        dirty = [o for o in session.dirty if self._is_tracked(o)]
        deleted = [o for o in session.deleted if self._is_tracked(o)]
        if not dirty and not deleted:
            return
        conn = session.connection()
        for obj in dirty + deleted:
            state = inspect(obj)
            if state.identity is None:
                continue
            key = (entity_type_of(state.mapper), state.identity[0])
            if key not in self._records:  # lần chạm đầu tiên thắng: DB còn giữ giá trị trước UoW
                self._records[key] = _Record(state.mapper, key[1], read_snapshot(conn, state.mapper, key[1]))

    def _after_flush(self, session: Session, flush_context) -> None:
        if self._finalizing:
            return
        for obj in session.new:
            if not self._is_tracked(obj):
                continue
            mapper = inspect(obj).mapper
            key = (entity_type_of(mapper), mapper.primary_key_from_instance(obj)[0])
            self._records.setdefault(key, _Record(mapper, key[1], None))

    def _finalize(self, s: Session) -> None:
        s.flush()
        self._finalizing = True
        conn = s.connection()
        diffs: list[tuple[EntityKey, dict | None, dict | None]] = []
        for key, rec in self._records.items():
            after = read_snapshot(conn, rec.mapper, rec.entity_id)
            if meaningful(after, rec.mapper) != meaningful(rec.before, rec.mapper):
                diffs.append((key, rec.before, after))
        cs: ChangeSet | None = None
        if diffs:
            cs = ChangeSet(op_name=self.op_name, actor=self.actor, project_id=self.project_id, summary=self.summary)
            s.add(cs)
            s.flush()
            s.add_all(
                Revision(change_set_id=cs.id, entity_type=et, entity_id=eid, before_json=before, after_json=after)
                for (et, eid), before, after in diffs
            )
        for hook in self._hooks:
            hook(s, cs)
        s.flush()
        self.change_set_id = cs.id if cs is not None else None
        self.changed = [key for key, _, _ in diffs]
        self.kinds = [(et, eid, change_kind(before, after, self._records[(et, eid)].mapper))
                      for (et, eid), before, after in diffs]

    def _report_committed(self) -> None:
        """Báo change set vừa commit cho op đang chạy (để `ops.execute` phát `entity.changed`)."""
        collector = committed_changes.get()
        if collector is not None and self.change_set_id is not None:
            collector.append(CommittedChange(self.change_set_id, self.project_id, tuple(self.changed),
                                             tuple(self.kinds)))

    def _reset_result(self) -> None:
        self.change_set_id = None
        self.changed = []
        self.kinds = []
