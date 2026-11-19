"""Cache phái sinh của dựng phim (spec §23.6): segment chuẩn hoá, khối, bản nháp — luôn tạo lại được.

Bố cục dưới `settings.cache_dir`:
- `segments/<fp[:2]>/<fp>.mov` — segment chuẩn hoá / still_motion (khoá = fingerprint đầu vào).
- `render/stems/<fp>.wav` — stem thoại của khối (sidechain ducking ở lượt âm thanh cuối).
- `render/media/...` — khối và bản nháp đã đăng ký vào bảng `media` (origin `derived`).
- `render/work/...` — file tạm của một lần dựng (list concat, ASS…), xoá sau khi xong.

Dùng file → `touch` (mtime) để dọn theo LRU khi vượt hạn mức.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

SCOPES = {
    "segments": ("segments",),
    "chunks": ("render/media", "render/stems"),
    "work": ("render/work",),
}
SCOPES["all"] = SCOPES["segments"] + SCOPES["chunks"] + SCOPES["work"]


def fingerprint(obj: Any) -> str:
    """sha256 của JSON chuẩn (khoá sắp xếp, không phụ thuộc thứ tự dict)."""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_cache_dir() -> Path:
    from sagareel.config import load_settings

    return load_settings().cache_dir


def segment_path(cache_dir: Path, fp: str, ext: str = ".mov") -> Path:
    return Path(cache_dir) / "segments" / fp[:2] / f"{fp}{ext}"


def stem_path(cache_dir: Path, fp: str) -> Path:
    return Path(cache_dir) / "render" / "stems" / f"{fp}.wav"


def touch(path: Path) -> None:
    with contextlib.suppress(OSError):
        now = time.time()
        os.utime(path, (now, now))


def hit(path: Path) -> bool:
    """File cache có sẵn (và đánh dấu vừa dùng)."""
    if Path(path).is_file() and Path(path).stat().st_size > 0:
        touch(path)
        return True
    return False


def _files(cache_dir: Path, scope: str) -> list[Path]:
    out: list[Path] = []
    for sub in SCOPES[scope]:
        root = Path(cache_dir) / sub
        if root.is_dir():
            out.extend(p for p in root.rglob("*") if p.is_file())
    return out


def stats(cache_dir: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for scope in ("segments", "chunks", "work"):
        files = _files(cache_dir, scope)
        result[scope] = {"files": len(files), "bytes": sum(_size(p) for p in files)}
    result["total"] = {k: sum(v[k] for v in result.values()) for k in ("files", "bytes")}
    return result


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def clear(cache_dir: Path, scope: str) -> dict[str, int]:
    """Xoá file cache của `scope` (segments/chunks/work/all). File đang bị giữ (Windows) thì bỏ qua."""
    removed = freed = 0
    for p in _files(cache_dir, scope):
        size = _size(p)
        try:
            p.unlink()
        except OSError:
            continue
        removed += 1
        freed += size
    for sub in SCOPES[scope]:
        root = Path(cache_dir) / sub
        if root.is_dir():
            for d in sorted((d for d in root.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
                with contextlib.suppress(OSError):
                    d.rmdir()
    return {"files": removed, "bytes": freed}


def prune(cache_dir: Path, *, max_bytes: int, min_free_bytes: int = 0, keep: set[Path] | None = None) -> dict:
    """Dọn LRU (mtime cũ nhất trước) tới khi tổng ≤ `max_bytes` và ổ còn ≥ `min_free_bytes`. `keep` không bị xoá."""
    keep = {Path(p).resolve() for p in (keep or set())}
    files = [p for p in _files(cache_dir, "all")]
    entries = []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append((st.st_mtime, st.st_size, p))
    entries.sort()
    total = sum(e[1] for e in entries)

    def free() -> int:
        try:
            return shutil.disk_usage(cache_dir).free
        except OSError:
            return min_free_bytes

    removed = freed = 0
    for _, size, p in entries:
        if total <= max_bytes and (min_free_bytes <= 0 or free() >= min_free_bytes):
            break
        if p.resolve() in keep:
            continue
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
        freed += size
    return {"files": removed, "bytes": freed, "remaining_bytes": total}
