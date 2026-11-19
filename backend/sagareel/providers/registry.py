"""Registry adapter tự đăng ký.

```python
@register_adapter
class MyTTS(TTSAdapter):
    adapter_id = "my_tts"
    ...
```

`discover()` import mọi module con của `sagareel.providers` (đệ quy, trừ `base`/`registry`) để decorator chạy; chỉ
import, không I/O, không gọi provider. `get()`/`all()` tự `discover()` ở lần đầu.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import threading

from sagareel.core.errors import AppError
from sagareel.providers.base import BASE_CLASSES, KINDS, Adapter

_ADAPTERS: dict[str, type[Adapter]] = {}
_SKIP = {"base", "registry"}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_lock = threading.Lock()
_discovered = False


def register_adapter(cls: type[Adapter]) -> type[Adapter]:
    """Decorator: đăng ký lớp adapter theo `cls.adapter_id`. Trùng id với lớp khác → `ValueError`."""
    if not (isinstance(cls, type) and issubclass(cls, BASE_CLASSES)):
        raise TypeError(f"{cls!r} phải kế thừa một trong {[b.__name__ for b in BASE_CLASSES]}")
    if inspect.isabstract(cls):
        missing = sorted(getattr(cls, "__abstractmethods__", ()))
        raise TypeError(f"{cls.__name__} còn phương thức trừu tượng chưa cài đặt: {missing}")
    adapter_id = getattr(cls, "adapter_id", None)
    if not isinstance(adapter_id, str) or not _ID_RE.match(adapter_id):
        raise ValueError(f"{cls.__name__}.adapter_id không hợp lệ: {adapter_id!r} (cần snake_case)")
    if cls.kind not in KINDS:
        raise ValueError(f"{cls.__name__}.kind không hợp lệ: {cls.kind!r} (cần một trong {KINDS})")
    if not isinstance(getattr(cls, "label", None), str) or not cls.label:
        raise ValueError(f"{cls.__name__} cần `label` (tên hiển thị tiếng Việt)")
    # Bí mật duy nhất của kết nối là cột `api_key` (che khi trả ra, không vào lịch sử); `options` là JSON thường.
    bad = [f.name for f in cls.config_fields if f.type == "secret" and f.name != "api_key"]
    if bad:
        raise ValueError(f"{cls.__name__}: trường bí mật phải tên 'api_key' (options không giữ bí mật): {bad}")
    existing = _ADAPTERS.get(adapter_id)
    if existing is not None and (existing.__module__, existing.__qualname__) != (cls.__module__, cls.__qualname__):
        raise ValueError(f"Adapter {adapter_id!r} đã được đăng ký bởi {existing.__module__}.{existing.__qualname__}")
    _ADAPTERS[adapter_id] = cls  # cùng lớp nạp lại (reload module) thì thay bản mới
    return cls


def discover() -> None:
    """Import mọi module trong `sagareel.providers.*` (đệ quy). Idempotent."""
    global _discovered
    with _lock:
        if _discovered:
            return
        import sagareel.providers as pkg

        for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
            if info.name.rsplit(".", 1)[-1] in _SKIP:
                continue
            importlib.import_module(info.name)
        _discovered = True


def unknown_adapter(adapter_id: str) -> AppError:
    return AppError(
        "unknown_adapter",
        f"Không có adapter nào tên '{adapter_id}'.",
        hint="Gọi provider.adapters để xem các adapter hiện có.",
        status=422,
    )


def unregister(adapter_id: str) -> None:
    """Gỡ một adapter (dùng cho test đăng ký adapter tạm)."""
    _ADAPTERS.pop(adapter_id, None)


def get(adapter_id: str) -> type[Adapter]:
    discover()
    cls = _ADAPTERS.get(adapter_id)
    if cls is None:
        raise unknown_adapter(adapter_id)
    return cls


def all() -> list[type[Adapter]]:  # noqa: A001 — tên theo interface
    """Mọi adapter đã đăng ký, sắp theo (kind, adapter_id)."""
    discover()
    return sorted(_ADAPTERS.values(), key=lambda c: (KINDS.index(c.kind), c.adapter_id))
