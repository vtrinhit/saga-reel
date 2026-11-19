"""Event bus trong tiến trình: phát sự kiện (tiến độ job, thực thể đổi…) tới UI/agent qua SSE, có phát lại.

- `publish(...)` gọi được từ **bất kỳ thread nào**: trên thread của event loop thì áp dụng ngay; từ thread khác thì
  chuyển về loop bằng `loop.call_soon_threadsafe`. Mọi thay đổi trạng thái (gán `seq`, ghi buffer, phát cho
  subscriber) đều diễn ra trên loop nên `seq` luôn tăng đúng thứ tự trong buffer. Chưa bind loop (vd. test, CLI)
  thì áp dụng tại chỗ dưới khoá. `publish` không bao giờ ném lỗi vào op gọi nó.
- `subscribe(project_id, last_seq)` đăng ký **ngay khi gọi** (không phải async generator: sự kiện phát giữa lúc
  gọi và lần `__anext__` đầu không bị mất) và trả về `Subscription` (async iterator, nhớ `aclose()`).
  `last_seq=None` → chỉ sự kiện mới; ngược lại phát lại các sự kiện trong buffer có `seq > last_seq`. Nếu buffer
  không còn đủ (`last_seq` cũ hơn buffer) hoặc `last_seq` lớn hơn `seq` hiện tại (id từ lần chạy trước) → phát
  trước một sự kiện `resync` (client nên tải lại trạng thái).
- Lọc project: subscriber `project_id=None` nhận mọi sự kiện; ngược lại nhận sự kiện cùng project hoặc
  `project_id=None`. `resync` luôn được giao.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("sagareel.events")

RESYNC = "resync"


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    type: str
    project_id: int | None
    data: dict = field(default_factory=dict)
    at: float = 0.0


class Subscription:
    """Async iterator các `Event` của một subscriber. Hàng đợi bị tràn → xoá và giao một `resync`."""

    def __init__(self, bus: EventBus, project_id: int | None, max_pending: int) -> None:
        self._bus = bus
        self.project_id = project_id
        self._max_pending = max_pending
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._closed = False

    def wants(self, ev: Event) -> bool:
        return self.project_id is None or ev.project_id is None or ev.project_id == self.project_id

    def _deliver(self, ev: Event) -> None:
        """Chỉ gọi trên thread của loop."""
        if self._closed:
            return
        if self._queue.qsize() >= self._max_pending:  # subscriber đọc quá chậm: bỏ hàng đợi, yêu cầu resync
            while not self._queue.empty():
                self._queue.get_nowait()
            ev = Event(ev.seq, RESYNC, None, {"reason": "overflow", "seq": ev.seq}, time.time())
        self._queue.put_nowait(ev)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        if self._closed:
            raise StopAsyncIteration
        ev = await self._queue.get()
        if ev is None:  # đã đóng trong lúc chờ
            raise StopAsyncIteration
        return ev

    def close(self) -> None:
        """Huỷ đăng ký (không chờ — an toàn trong khối `finally` khi bị huỷ)."""
        if self._closed:
            return
        self._closed = True
        self._bus._unsubscribe(self)
        self._queue.put_nowait(None)  # đánh thức `__anext__` đang chờ

    async def aclose(self) -> None:
        self.close()


class EventBus:
    def __init__(self, buffer_size: int = 2000) -> None:
        if buffer_size < 1:
            raise ValueError("buffer_size phải >= 1")
        self.buffer_size = buffer_size
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._seq = 0
        self._subs: list[Subscription] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    # --- API ----------------------------------------------------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, type: str, data: dict, project_id: int | None = None) -> None:
        """Phát sự kiện; an toàn gọi từ thread bất kỳ, không bao giờ ném lỗi."""
        loop = self._loop
        try:
            # Chụp bản sao ngay (trên thread gọi): caller có thể sửa/tái dùng dict sau khi publish trả về.
            data = dict(data)
            if loop is None or self._on_loop(loop):
                self._apply(type, data, project_id, fanout=True)
                return
            try:
                loop.call_soon_threadsafe(self._apply, type, data, project_id, True)
            except RuntimeError:  # loop đã đóng (đang tắt): chỉ ghi buffer, không còn subscriber nào nghe
                self._apply(type, data, project_id, fanout=False)
        except Exception:
            logger.exception("Không phát được sự kiện %s", type)

    def subscribe(self, project_id: int | None, last_seq: int | None) -> Subscription:
        """Đăng ký ngay (phải gọi trên event loop); trả `Subscription` đã nạp sẵn phần phát lại."""
        running = asyncio.get_running_loop()
        if self._loop is None or self._loop.is_closed():
            self._loop = running
        elif self._loop is not running:
            raise RuntimeError("EventBus.subscribe phải được gọi trên event loop đã bind.")
        # Tụt sau quá một buffer thì dù sao cũng không phát lại được nữa → tràn = resync.
        sub = Subscription(self, project_id, self.buffer_size + 1)
        with self._lock:
            for ev in self._replay(last_seq):  # phần phát lại (≤ buffer + 1) không tính là tràn
                if ev.type == RESYNC or sub.wants(ev):
                    sub._queue.put_nowait(ev)
            self._subs.append(sub)
        return sub

    # --- Nội bộ -------------------------------------------------------------------------------------------------

    @staticmethod
    def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
        try:
            return asyncio.get_running_loop() is loop
        except RuntimeError:
            return False

    def _apply(self, type: str, data: dict, project_id: int | None, fanout: bool) -> None:
        """Gán seq + ghi buffer + phát. Có thể chạy như callback trên loop → không bao giờ ném lỗi ra ngoài."""
        try:
            with self._lock:
                # Dựng xong sự kiện rồi mới tăng seq: dựng lỗi thì không để lại lỗ seq.
                ev = Event(self._seq + 1, type, project_id, data, time.time())
                self._buffer.append(ev)
                self._seq = ev.seq
                subs = list(self._subs) if fanout else []
        except Exception:
            logger.exception("Không phát được sự kiện %s", type)
            return
        for sub in subs:
            try:
                if sub.wants(ev):
                    sub._deliver(ev)
            except Exception:
                logger.exception("Không giao được sự kiện %s (seq %s) cho subscriber", type, ev.seq)

    def _replay(self, last_seq: int | None) -> list[Event]:
        if last_seq is None:
            return []
        oldest = self._buffer[0].seq if self._buffer else self._seq + 1
        if last_seq > self._seq:  # id từ lần chạy trước (seq đã đếm lại): gửi lại toàn bộ buffer
            reason, after = "stale", 0
        elif last_seq < oldest - 1:  # buffer đã trôi qua phần client còn thiếu
            reason, after = "gap", 0
        else:
            return [ev for ev in self._buffer if ev.seq > last_seq]
        resync = Event(oldest - 1, RESYNC, None, {"reason": reason, "seq": self._seq}, time.time())
        return [resync, *(ev for ev in self._buffer if ev.seq > after)]

    def _unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass


__all__ = ["RESYNC", "Event", "EventBus", "Subscription"]
