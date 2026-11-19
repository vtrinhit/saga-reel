"""Job handler theo tính năng: mỗi module `sagareel/handlers/<tinh_nang>.py` tự đăng ký bằng `@jobs.job_handler(...)`.

`load_all()` được gọi lúc dựng app (cạnh `ops.load_all()`) và trong fixture test dựng worker — thêm tính năng mới chỉ
cần thêm file, không sửa file chung.
"""

from __future__ import annotations

import importlib
import pkgutil


def load_all() -> None:
    """Import mọi module trong package (idempotent: module đã import không chạy lại)."""
    for info in pkgutil.iter_modules(__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")
