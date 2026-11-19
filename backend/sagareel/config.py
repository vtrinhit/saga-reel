"""Cấu hình đường dẫn, cổng và vị trí dữ liệu runtime của SagaReel Studio."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_ENV = "SAGAREEL_DATA_DIR"

# backend/sagareel/config.py → thư mục gốc repo (nơi có pyproject.toml).
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AppSettings:
    root: Path
    data_dir: Path
    db_path: Path
    media_dir: Path
    cache_dir: Path
    trash_dir: Path
    outputs_dir: Path
    frontend_dist: Path
    env_file: Path
    host: str = "127.0.0.1"
    port: int = 8990
    public_hosts: tuple[str, ...] = ()


def _under(root: Path, value: Path | str) -> Path:
    """Đường dẫn tương đối được tính từ thư mục gốc, không phụ thuộc thư mục đang chạy."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else root / p


def load_settings(
    root: Path | None = None,
    data_dir: Path | None = None,
    env_file: Path | None = None,
    outputs_dir: Path | None = None,
) -> AppSettings:
    """Dựng AppSettings và tạo các thư mục dữ liệu cần thiết.

    `data_dir` ưu tiên: tham số → biến môi trường SAGAREEL_DATA_DIR → `root/"data"`.
    Không đọc nội dung `env_file` ở đây (chỉ ghi nhận đường dẫn).
    """
    root = Path(root) if root is not None else _DEFAULT_ROOT

    if data_dir is not None:
        data = _under(root, data_dir)
    elif os.environ.get(DATA_DIR_ENV):
        data = _under(root, os.environ[DATA_DIR_ENV])
    else:
        data = root / "data"

    settings = AppSettings(
        root=root,
        data_dir=data,
        db_path=data / "sagareel.db",
        media_dir=data / "media",
        cache_dir=data / "cache",
        trash_dir=data / "trash",
        outputs_dir=_under(root, outputs_dir) if outputs_dir is not None else root / "outputs",
        frontend_dist=root / "frontend" / "dist",
        env_file=_under(root, env_file) if env_file is not None else root / ".env.local",
    )

    for d in (
        settings.data_dir,
        settings.media_dir,
        settings.cache_dir,
        settings.trash_dir,
        settings.outputs_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    return settings
