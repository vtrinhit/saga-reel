"""Base khai báo, mixin cột chung và kiểu cột tuỳ biến cho SQLite."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, MetaData, Text
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Tên ràng buộc/index cố định để migration batch (SQLite) về sau xoá/sửa được theo tên.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Datetime luôn có múi giờ UTC.

    SQLite không lưu offset: chuẩn hoá về UTC khi ghi, gắn lại `timezone.utc` khi đọc.
    Datetime không có múi giờ được coi là UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class _JSONText(TypeDecorator):
    """JSON lưu dạng TEXT, giữ nguyên ký tự Unicode (`ensure_ascii=False`). `None` → SQL NULL."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value: str | None, dialect) -> Any:
        if value is None:
            return None
        return json.loads(value)


class JSONDictType(_JSONText):
    """Cột JSON dạng object (dict); dùng qua `JSONDict` (bản theo dõi thay đổi).

    Theo dõi thay đổi tại chỗ chỉ ở mức nông (`MutableDict`): `obj.options["x"] = 1` được lưu,
    nhưng sửa lồng (`obj.options["a"]["b"] = 1`) thì phải gán lại cả giá trị hoặc gọi
    `sqlalchemy.orm.attributes.flag_modified(obj, "options")`.

    Cảnh báo: session chỉ giữ tham chiếu yếu tới object đã nạp. Sửa tại chỗ trên object không có biến nào giữ
    (vd. `s.scalar(select(M)).options["x"] = 1`) bị mất vì object bị thu gom trước khi flush — gán object vào biến
    trước rồi mới sửa.
    """


class JSONListType(_JSONText):
    """Cột JSON dạng mảng (list); dùng qua `JSONList` (bản theo dõi thay đổi).

    Theo dõi thay đổi tại chỗ chỉ ở mức nông (`MutableList`): `append`, `obj.items[0] = …` được lưu,
    nhưng sửa phần tử lồng (`obj.items[0]["k"] = 1`) thì phải gán lại hoặc gọi `flag_modified`.

    Cảnh báo: như `JSONDictType` — sửa tại chỗ trên object không có biến nào giữ (session chỉ giữ tham chiếu yếu) có
    thể bị mất; gán object vào biến trước rồi mới sửa.
    """


# Kiểu dùng trong model: thay đổi tại chỗ ở mức nông tự đánh dấu dirty.
JSONDict = MutableDict.as_mutable(JSONDictType())
JSONList = MutableList.as_mutable(JSONListType())


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UTCDateTime()}


class Plain:
    """Mixin cho bảng không theo dõi lịch sử: `id`, `created_at`."""

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Tracked:
    """Mixin cho thực thể theo dõi lịch sử (★): ghi revision khi thêm/sửa/xoá, xoá mềm qua `deleted_at`."""

    __tracked__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None]
