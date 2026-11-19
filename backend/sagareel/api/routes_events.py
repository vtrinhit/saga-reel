"""`GET /api/events` — luồng SSE của event bus (tiến độ job, thực thể đổi…), có phát lại theo `Last-Event-ID`.

- Sự kiện đầu luôn là `server.hello {"boot_id"}` (không có `id`, để không ghi đè id client đang giữ); `boot_id`
  khác lần trước → server đã khởi động lại, client nên tải lại trạng thái.
- Mỗi sự kiện bus: `id=seq`, `event=type`, `data=JSON`. Ping 15 s.
- `?project=<id>` lọc theo project (vẫn nhận sự kiện toàn cục `project_id=None`).
- Principal của request (phiên/token) được kiểm lại mỗi `REVALIDATE_SECONDS`; hết hiệu lực → đóng luồng.
- Ngắt kết nối / tắt server: sse-starlette huỷ generator; subscription được huỷ đăng ký trong `finally`.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, Header, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from sagareel.core.errors import ErrorPayload
from sagareel.services import auth

PING_SECONDS = 15
REVALIDATE_SECONDS = 60.0  # kiểm lại principal (phiên/token còn hiệu lực) ít nhất mỗi chừng này giây


def _parse_last_event_id(value: str | None) -> int | None:
    try:
        return int(value) if value is not None and value.strip() else None
    except ValueError:
        return None


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/events",
        tags=["events"],
        summary="Luồng sự kiện SSE (có phát lại theo Last-Event-ID)",
        response_class=EventSourceResponse,
        responses={
            200: {"content": {"text/event-stream": {}}, "description": "Luồng Server-Sent Events"},
            # Mô tả lỗi đúng hình payload() thay vì HTTPValidationError mặc định của FastAPI.
            422: {"model": ErrorPayload, "description": "Tham số không hợp lệ (`validation_failed`)"},
        },
    )
    async def events(
        request: Request,
        project: int | None = Query(None, description="Chỉ nhận sự kiện của project này (và sự kiện toàn cục)"),
        last_event_id: str | None = Header(None, alias="Last-Event-ID"),
        last_event_id_q: str | None = Query(
            None,
            alias="last_event_id",
            description=(
                "Như header Last-Event-ID, cho EventSource mới tạo (không gửi được header). "
                "Có header hợp lệ thì header thắng (trình duyệt tự nối lại gửi header mới nhất, URL vẫn là URL cũ)."
            ),
        ),
    ) -> EventSourceResponse:
        services = request.app.state.services
        last_seq = _parse_last_event_id(last_event_id)
        if last_seq is None:
            last_seq = _parse_last_event_id(last_event_id_q)

        principal = getattr(request.state, "principal", None)

        async def still_valid() -> bool:
            return await anyio.to_thread.run_sync(auth.principal_still_valid, services, principal)

        async def stream() -> AsyncIterator[ServerSentEvent]:
            sub = services.bus.subscribe(project, last_seq)  # đăng ký trước hello: không hở sự kiện nào
            try:
                yield ServerSentEvent(event="server.hello", data=json.dumps({"boot_id": services.boot_id}))
                next_check = time.monotonic() + REVALIDATE_SECONDS
                while True:
                    if time.monotonic() >= next_check:
                        # Phiên/token bị thu hồi, hết hạn hoặc vai trò bị tắt → đóng luồng (client nối lại sẽ nhận 401).
                        if not await still_valid():
                            return
                        next_check = time.monotonic() + REVALIDATE_SECONDS
                    ev = None
                    with anyio.move_on_after(max(0.0, next_check - time.monotonic())):
                        try:
                            ev = await sub.__anext__()
                        except StopAsyncIteration:
                            return
                    if ev is not None:
                        yield ServerSentEvent(
                            id=str(ev.seq), event=ev.type, data=json.dumps(ev.data, ensure_ascii=False, default=str)
                        )
            finally:
                sub.close()

        return EventSourceResponse(stream(), ping=PING_SECONDS)

    return router
