"""`GET /api/events` — luồng SSE của event bus (tiến độ job, thực thể đổi…), có phát lại theo `Last-Event-ID`.

- Sự kiện đầu luôn là `server.hello {"boot_id"}` (không có `id`, để không ghi đè id client đang giữ); `boot_id`
  khác lần trước → server đã khởi động lại, client nên tải lại trạng thái.
- Mỗi sự kiện bus: `id=seq`, `event=type`, `data=JSON`. Ping 15 s.
- `?project=<id>` lọc theo project (vẫn nhận sự kiện toàn cục `project_id=None`).
- Ngắt kết nối / tắt server: sse-starlette huỷ generator; subscription được huỷ đăng ký trong `finally`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from sagareel.core.errors import ErrorPayload

PING_SECONDS = 15


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
    ) -> EventSourceResponse:
        services = request.app.state.services
        last_seq = _parse_last_event_id(last_event_id)

        async def stream() -> AsyncIterator[ServerSentEvent]:
            sub = services.bus.subscribe(project, last_seq)  # đăng ký trước hello: không hở sự kiện nào
            try:
                yield ServerSentEvent(event="server.hello", data=json.dumps({"boot_id": services.boot_id}))
                async for ev in sub:
                    yield ServerSentEvent(
                        id=str(ev.seq), event=ev.type, data=json.dumps(ev.data, ensure_ascii=False, default=str)
                    )
            finally:
                sub.close()

        return EventSourceResponse(stream(), ping=PING_SECONDS)

    return router
