"""Che và băm bí mật; chụp snapshot thực thể cho lịch sử phiên bản.

Cột bí mật khai báo `info={"secret": True}`. Bí mật không bao giờ vào snapshot: chỉ lưu
`{"__secret__": sha256(value)[:12]}` — đủ để biết giá trị có đổi hay không, không đủ để khôi phục.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import Column, inspect
from sqlalchemy.orm import ColumnProperty, Mapper

SECRET_KEY = "__secret__"


MASK_REVEAL_MIN_LEN = 12  # chỉ lộ 4 ký tự cuối khi bí mật đủ dài để 4 ký tự đó không đáng kể


def mask(value: str) -> str:
    """Dạng hiển thị của bí mật: `""` → `""`; ≥ 12 ký tự → `"••••" + 4 ký tự cuối`; ngắn hơn → `"••••"`."""
    if not value:
        return ""
    if len(value) < MASK_REVEAL_MIN_LEN:
        return "••••"
    return "••••" + value[-4:]


def digest(value: str) -> str:
    """Dấu vết ngắn (12 ký tự hex của sha256) để so sánh bí mật mà không lưu nó."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def is_secret(column: Column) -> bool:
    return bool(column.info.get("secret"))


def column_of(attr: ColumnProperty) -> Column:
    return attr.columns[0]


def _plain(value: Any) -> Any:
    """Bản sao JSON thuần (dict/list thường, không còn MutableDict/MutableList), datetime → ISO."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def encode(column: Column, value: Any) -> Any:
    """Giá trị một cột trong snapshot."""
    if is_secret(column):
        return None if value is None else {SECRET_KEY: digest(str(value))}
    return _plain(value)


def snapshot_with(mapper: Mapper, get: Callable[[ColumnProperty], Any]) -> dict[str, Any]:
    """Snapshot mọi cột của `mapper`; `get(attr)` trả giá trị thô của thuộc tính (từ object hoặc từ dòng DB)."""
    return {attr.key: encode(column_of(attr), get(attr)) for attr in mapper.column_attrs}


def snapshot(obj: Any) -> dict[str, Any]:
    """Snapshot JSON-able của một object ORM: mọi cột, datetime ISO, cột bí mật thay bằng `{"__secret__": …}`."""
    return snapshot_with(inspect(obj).mapper, lambda attr: getattr(obj, attr.key))
