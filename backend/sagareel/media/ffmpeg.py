"""Chạy ffmpeg an toàn: tiến độ, huỷ, timeout, ghi file tạm rồi đổi tên.

- Luôn chèn `-nostdin -hide_banner -y -progress pipe:1 -nostats` trước tham số của người gọi.
- Đọc stdout (khối `key=value` của `-progress`) để báo phần trăm; stderr được đọc song song vào một đuôi có giới hạn
  (không đọc thì ffmpeg nói nhiều sẽ làm đầy pipe và treo).
- Huỷ / timeout / task bị cancel → terminate, chờ ngắn, kill, luôn đợi tiến trình thoát rồi mới xoá file `.part`
  (Windows không xoá được file mà tiến trình còn giữ).
- Người gọi ghi vào file tạm do `atomic_output(path)` cấp (`<tên>.<token>.part.<ext>`), thành công thì đổi tên
  thành `path`. Phần mở rộng thật nằm cuối để ffmpeg chọn đúng muxer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import shutil
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path

from sagareel.core.errors import AppError

logger = logging.getLogger("sagareel.ffmpeg")

GLOBAL_ARGS = ("-nostdin", "-hide_banner", "-y", "-progress", "pipe:1", "-nostats")
STDERR_TAIL_LINES = 40
_STOP_GRACE_S = 3.0


class FFmpegError(AppError):
    """ffmpeg/ffprobe thất bại. `stderr_tail` = các dòng stderr cuối (để chẩn đoán, không đưa vào response)."""

    default_code = "ffmpeg_failed"

    def __init__(self, message: str, stderr_tail: str = "", *, code: str | None = None) -> None:
        super().__init__(code or self.default_code, message, status=500)
        self.stderr_tail = stderr_tail


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FFmpegError(
            "Không tìm thấy ffmpeg trên PATH. Hãy cài ffmpeg rồi khởi động lại ứng dụng.", code="ffmpeg_missing"
        )
    return exe


def build_command(args: list[str]) -> list[str]:
    return [ffmpeg_exe(), *GLOBAL_ARGS, *args]


def popen_kwargs() -> dict:
    """Trên Windows không bật cửa sổ console cho tiến trình con."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def part_path(path: Path) -> Path:
    """`a.mp4` → `a.<token>.part.mp4` (token ngẫu nhiên: hai lần ghi cùng đích không giẫm lên nhau)."""
    return path.with_name(f"{path.stem}.{secrets.token_hex(4)}.part{path.suffix}")


def is_part_file(path: Path) -> bool:
    return ".part." in path.name or path.name.endswith(".part")


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        try:
            path.unlink()
        except PermissionError:
            logger.warning("Không xoá được file tạm %s (đang bị giữ)", path)


@contextlib.contextmanager
def atomic_output(path: Path) -> Iterator[Path]:
    """Cấp đường dẫn `.part` cạnh `path`; thoát bình thường → `os.replace` thành `path`, lỗi → xoá `.part`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(path)
    try:
        yield part
        if not part.is_file():
            raise FFmpegError(f"Không có file đầu ra tạm {part.name}.", code="ffmpeg_no_output")
        os.replace(part, path)
    except BaseException:
        _unlink_quiet(part)
        raise


def progress_pct(block: dict[str, str], total_ms: int | None) -> float | None:
    """Phần trăm từ một khối `-progress`. `out_time_us` và `out_time_ms` đều tính bằng micro giây."""
    if not total_ms or total_ms <= 0:
        return None
    raw = block.get("out_time_us", block.get("out_time_ms"))
    try:
        us = int(raw) if raw is not None else None
    except ValueError:  # "N/A" ở đầu quá trình
        return None
    if us is None or us < 0:
        return None
    return max(0.0, min(100.0, us / 1000 / total_ms * 100))


async def _read_progress(
    stream: asyncio.StreamReader, total_ms: int | None, on_progress: Callable[[float], None] | None
) -> None:
    block: dict[str, str] = {}
    last: float | None = None
    async for raw in stream:
        line = raw.decode("utf-8", "replace").strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key != "progress":
            block[key] = value
            continue
        # Hết một khối: báo một lần, không lùi (ffmpeg có thể báo out_time dao động nhẹ).
        pct = 100.0 if value == "end" and total_ms else progress_pct(block, total_ms)
        block = {}
        if pct is None or on_progress is None:
            continue
        if last is not None:
            pct = max(pct, last)
            if pct == last:
                continue
        last = pct
        on_progress(pct)


async def _read_tail(stream: asyncio.StreamReader, tail: deque[str]) -> None:
    async for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            tail.append(line)


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """terminate → chờ ngắn → kill; luôn đợi tiến trình thoát hẳn."""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), _STOP_GRACE_S)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


def _output_parts(args: list[str]) -> list[Path]:
    """Các đối số đầu ra dạng file `.part` (không phải đầu vào sau `-i`) — xoá khi thất bại."""
    out = []
    for i, a in enumerate(args):
        if i > 0 and args[i - 1] == "-i":
            continue
        if not a.startswith("-") and is_part_file(Path(a)):
            out.append(Path(a))
    return out


async def run(
    args: list[str],
    *,
    timeout: float = 3600,
    total_ms: int | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: asyncio.Event | None = None,
) -> None:
    """Chạy ffmpeg với `args`; ném `FFmpegError` (code `ffmpeg_failed` / `ffmpeg_timeout` / `ffmpeg_cancelled`)."""
    cmd = build_command(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **popen_kwargs(),
        )
    except NotImplementedError:
        # Windows + SelectorEventLoop (vd. uvicorn --reload/--workers) không hỗ trợ tiến trình con async.
        raise FFmpegError(
            "Vòng lặp sự kiện hiện tại không chạy được tiến trình con (cần ProactorEventLoop trên Windows).",
            code="ffmpeg_unsupported_loop",
        ) from None
    tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    stdout_task = asyncio.create_task(_read_progress(proc.stdout, total_ms, on_progress))
    stderr_task = asyncio.create_task(_read_tail(proc.stderr, tail))
    wait_task = asyncio.create_task(proc.wait())
    cancel_task = asyncio.create_task(cancel.wait()) if cancel is not None else None
    tasks = [t for t in (stdout_task, stderr_task, wait_task, cancel_task) if t is not None]

    reason: str | None = None
    ok = False
    try:
        watch = {wait_task, stdout_task} | ({cancel_task} if cancel_task is not None else set())
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if cancel_task is not None and cancel_task.done():
                reason = "cancelled"
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                reason = "timeout"
                break
            done, _ = await asyncio.wait(watch, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            if cancel_task is not None and cancel_task in done:
                reason = "cancelled"
                break
            if stdout_task in done:
                watch.discard(stdout_task)
                if stdout_task.exception() is not None:  # on_progress ném lỗi → dừng ffmpeg rồi ném lại
                    reason = "callback"
                    break
            if wait_task in done:
                break
        if reason is not None:
            await _stop(proc)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)  # đọc nốt pipe tới EOF
        if reason == "callback":
            raise stdout_task.exception()  # type: ignore[misc]
        tail_text = "\n".join(tail)
        if reason == "cancelled":
            raise FFmpegError("Đã huỷ tiến trình ffmpeg.", tail_text, code="ffmpeg_cancelled")
        if reason == "timeout":
            raise FFmpegError(f"ffmpeg chạy quá {timeout:g} giây nên đã bị dừng.", tail_text, code="ffmpeg_timeout")
        if proc.returncode != 0:
            raise FFmpegError(f"ffmpeg thất bại (mã thoát {proc.returncode}).", tail_text)
        ok = True
    finally:
        if proc.returncode is None:  # task bị cancel từ ngoài, hoặc lỗi bất ngờ
            await asyncio.shield(_stop(proc))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not ok:
            for p in _output_parts(args):
                _unlink_quiet(p)
