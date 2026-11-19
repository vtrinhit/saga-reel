"""Tiện ích file âm thanh dùng chung cho adapter TTS/STT: ghi nguyên tử, đo thời lượng, chuẩn hoá chữ đầu ra.

Hàm ở đây là đồng bộ (I/O đĩa, ffprobe); code async gọi qua `anyio.to_thread.run_sync`.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import unicodedata
from pathlib import Path

from sagareel.media.ffmpeg import part_path
from sagareel.media.probe import probe
from sagareel.providers.base import ProviderJobError


def write_atomic(path: Path, data: bytes) -> None:
    """Ghi `data` vào file tạm cạnh `path` rồi `os.replace` (người đọc không bao giờ thấy file dở)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(path)
    try:
        part.write_bytes(data)
        os.replace(part, path)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise


def copy_atomic(src: Path, dest: Path) -> None:
    """Chép `src` → `dest` qua file tạm + `os.replace`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(dest)
    try:
        shutil.copyfile(src, part)
        os.replace(part, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise


def duration_ms(path: Path) -> int:
    """Thời lượng (ms) đo bằng ffprobe; file không đọc được → `ProviderJobError("bad_audio")`."""
    try:
        info = probe(Path(path))
    except Exception as e:  # noqa: BLE001 — FFmpegError, file hỏng…
        raise ProviderJobError(
            "bad_audio", f"Âm thanh nhận được không đọc được ({type(e).__name__}).",
            hint="Thử lại; nếu vẫn lỗi, kiểm tra provider có trả đúng định dạng MP3 không.",
        ) from e
    if info.duration_ms <= 0:
        raise ProviderJobError("bad_audio", "Âm thanh nhận được không có thời lượng.",
                               hint="Thử lại hoặc đổi giọng/provider.")
    return info.duration_ms


def nfc(text: str) -> str:
    """Chuẩn hoá NFC — chỉ cho chữ **đầu ra** (từ, lời nhận dạng); không bao giờ cho chữ gửi đi."""
    return unicodedata.normalize("NFC", text)
