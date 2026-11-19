"""Khai báo thao tác theo nhóm (`*_ops.py`), mỏng, gọi xuống services."""

from __future__ import annotations

import importlib
import pkgutil


def load_all() -> None:
    """Import mọi module `sagareel.ops.*_ops` để chúng tự đăng ký vào `core.ops.REGISTRY` (idempotent)."""
    for info in pkgutil.iter_modules(__path__):
        if info.name.endswith("_ops"):
            importlib.import_module(f"{__name__}.{info.name}")
