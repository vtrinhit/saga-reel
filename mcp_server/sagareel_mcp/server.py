"""MCP server (SDK `mcp` 2.x, lowlevel `Server`): tool sinh động từ catalog của app đang chạy.

App chưa chạy lúc khởi động là chuyện bình thường (Claude Desktop mở MCP trước): server vẫn lên với tool chung, catalog
được tải lười ở lần `tools/list`/`tools/call` đầu tiên, `sr_reload` tải lại.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import NotificationOptions, Server

from sagareel_mcp.client import AppError, SagaReelClient
from sagareel_mcp.tools import ToolDef, build_tools, tool_name

logger = logging.getLogger("sagareel_mcp")

JOB_WAIT_CHUNK_S = 600  # trần `timeout_s` của op job.wait
JOB_WAIT_GRACE_S = 30
JOB_POLL_S = 2.0
ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})

INSTRUCTIONS = (
    "SagaReel Studio — studio dựng video AI. Mỗi thao tác là một op; tool `sr_<nhóm>_<hành động>` cho op hay dùng, "
    "`sr_describe_ops` xem danh mục + schema, `sr_call` gọi op bất kỳ. Quy tắc an toàn: (1) xoá hai bước — gọi "
    "`*_delete_preview` lấy `confirm_token`, báo người dùng, rồi mới xoá; (2) op tốn tiền: chạy `dry_run: true` "
    "trước, tôn trọng ngân sách (`budget_exceeded`); (3) op chạy lâu trả `job_id` → `sr_job_wait`; (4) gate dừng "
    "chờ người duyệt (`needs_attention`/`waiting_*`) thì hỏi người dùng, không tự vượt; (5) sửa nhầm → "
    "`sr_history_undo`. Lỗi trả `error_code: chi tiết (gợi ý: …)` tiếng Việt — đọc gợi ý rồi sửa params."
)


class ToolState:
    """Catalog + bảng tool hiện tại (dùng chung cho mọi phiên)."""

    def __init__(self, client: SagaReelClient) -> None:
        self.client = client
        self.catalog: list[dict] = []
        self.tools: dict[str, ToolDef] = build_tools([])
        self.loaded = False
        self.load_error: AppError | None = None
        self._lock = anyio.Lock()

    async def load(self, *, force: bool = False) -> None:
        async with self._lock:
            if self.loaded and not force:
                return
            try:
                catalog = await self.client.catalog()
            except AppError as e:
                self.load_error = e
                logger.warning("Chưa tải được catalog: %s", e.code)
                return
            self.catalog = catalog
            self.tools = build_tools(catalog)
            self.loaded = True
            self.load_error = None

    async def ensure(self) -> None:
        if not self.loaded:
            await self.load()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def ok(value: Any) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_dump(value))],
        structured_content=value if isinstance(value, dict) else None,
        is_error=False,
    )


def err(e: AppError) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=e.text())], is_error=True)


def to_mcp_tool(t: ToolDef) -> types.Tool:
    return types.Tool(
        name=t.name,
        description=t.description,
        input_schema=t.input_schema,
        annotations=types.ToolAnnotations(**t.annotations),
    )


class Handlers:
    def __init__(self, state: ToolState) -> None:
        self.state = state

    async def list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        await self.state.ensure()
        return types.ListToolsResult(tools=[to_mcp_tool(t) for t in self.state.tools.values()])

    async def call_tool(self, ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        args = dict(params.arguments or {})
        try:
            await self.state.ensure()
            tool = self.state.tools.get(params.name)
            if tool is None and self.state.load_error is not None and not self.state.loaded:
                raise self.state.load_error
            if tool is None:
                raise AppError("unknown_tool", f"Không có tool '{params.name}'.",
                               hint="Gọi `sr_reload` nếu app vừa cập nhật, hoặc `sr_describe_ops` + `sr_call`.")
            handler: Callable[[ToolDef, dict], Awaitable[Any]] = getattr(self, f"_t_{tool.kind}")
            value = await handler(tool, args)
            if tool.kind == "reload":
                await _notify_tools_changed(ctx)
            return ok(value)
        except AppError as e:
            return err(e)

    # --- tool -----------------------------------------------------------------------------------------------

    async def _t_op(self, tool: ToolDef, args: dict) -> Any:
        assert tool.op is not None
        return await self.state.client.call(tool.op, args)

    async def _t_describe(self, tool: ToolDef, args: dict) -> Any:
        if not self.state.loaded:
            raise self.state.load_error or AppError("app_not_running", "Chưa tải được danh mục thao tác.")
        group = args.get("group")
        with_schema = args.get("with_schema", True)
        rows = []
        for o in self.state.catalog:
            if group and o["group"] != group:
                continue
            tname = tool_name(o["name"])
            row = {
                "name": o["name"],
                "tool": tname if tname in self.state.tools and self.state.tools[tname].op == o["name"] else "sr_call",
                "summary": o.get("summary", ""),
                **{f: bool(o.get(f)) for f in ("read_only", "destructive", "idempotent", "costly", "long_running")},
            }
            if with_schema:
                row["params_schema"] = o.get("params_schema")
            rows.append(row)
        if group and not rows:
            groups = ", ".join(sorted({o["group"] for o in self.state.catalog}))
            raise AppError("unknown_group", f"Không có nhóm thao tác '{group}'.", hint=f"Các nhóm hiện có: {groups}.")
        return {"ops": rows}

    async def _t_call(self, tool: ToolDef, args: dict) -> Any:
        op = args.get("op")
        params = args.get("params") or {}
        if not isinstance(op, str) or not op:
            raise AppError("invalid_arguments", "Thiếu tên op.", hint="Ví dụ: {\"op\": \"system.info\"}.")
        if not isinstance(params, dict):
            raise AppError("invalid_arguments", "`params` phải là object JSON.", hint="Ví dụ: {\"id\": 1}.")
        return await self.state.client.call(op, params)

    async def _t_reload(self, tool: ToolDef, args: dict) -> Any:
        await self.state.load(force=True)
        if not self.state.loaded or self.state.load_error is not None:
            raise self.state.load_error or AppError("app_not_running", "Chưa tải được danh mục thao tác.")
        return {"ops": len(self.state.catalog), "tools": len(self.state.tools)}

    async def _t_estimate(self, tool: ToolDef, args: dict) -> Any:
        op = args.get("op")
        if op not in tool.extra.get("ops", []):
            raise AppError("invalid_arguments", f"Op ước tính không hợp lệ: {op!r}.",
                           hint=f"Chọn một trong: {', '.join(tool.extra.get('ops', []))}.")
        return await self.state.client.call(op, args.get("params") or {})

    async def _t_job_wait(self, tool: ToolDef, args: dict) -> Any:
        ids = args.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
            raise AppError("invalid_arguments", "`ids` phải là danh sách id job (số nguyên), ít nhất một.",
                           hint="Ví dụ: {\"ids\": [12, 13], \"timeout\": 600}.")
        timeout = args.get("timeout", 600)
        if not isinstance(timeout, int) or timeout < 0:
            raise AppError("invalid_arguments", "`timeout` phải là số giây (số nguyên ≥ 0).")
        names = {o["name"] for o in self.state.catalog}
        if "job.wait" in names or not self.state.loaded:
            return await self._wait_server(ids, timeout)
        return await self._wait_poll(ids, timeout)

    async def _wait_server(self, ids: list[int], timeout: int) -> Any:
        remaining = timeout
        while True:
            chunk = min(JOB_WAIT_CHUNK_S, remaining)
            result = await self.state.client.call("job.wait", {"ids": ids, "timeout_s": chunk},
                                                  read_timeout=chunk + JOB_WAIT_GRACE_S)
            remaining -= chunk
            timed_out = isinstance(result, dict) and bool(result.get("timed_out"))
            if remaining <= 0 or not timed_out:
                return result

    async def _wait_poll(self, ids: list[int], timeout: int) -> Any:
        deadline = anyio.current_time() + timeout
        while True:
            items = [await self.state.client.call("job.get", {"id": i}) for i in ids]
            if all(isinstance(j, dict) and j.get("status") not in ACTIVE_JOB_STATUSES for j in items):
                return {"items": items, "timed_out": False}
            left = deadline - anyio.current_time()
            if left <= 0:
                return {"items": items, "timed_out": True}
            await anyio.sleep(min(JOB_POLL_S, left))


async def _notify_tools_changed(ctx: Any) -> None:
    try:
        await ctx.session.send_tool_list_changed()
    except Exception:  # noqa: BLE001 — phiên không hỗ trợ: client tự gọi lại tools/list
        logger.debug("Không gửi được tools/list_changed")


def build_server(client: SagaReelClient) -> tuple[Server, ToolState]:
    state = ToolState(client)
    h = Handlers(state)
    server = Server(
        "sagareel",
        version="0.1.0",
        title="SagaReel Studio",
        instructions=INSTRUCTIONS,
        on_list_tools=h.list_tools,
        on_call_tool=h.call_tool,
    )
    return server, state


def init_options(server: Server):
    return server.create_initialization_options(NotificationOptions(tools_changed=True))


# --- HTTP transport: bearer bắt buộc -----------------------------------------------------------------------------


class BearerAuth:
    """ASGI middleware: request HTTP phải có `Authorization: Bearer <token SagaReel>`; scope khác (lifespan) đi thẳng."""

    def __init__(self, app: Any, token: str) -> None:
        if not token:
            raise ValueError("BearerAuth cần token")
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        got = b""
        for k, v in scope.get("headers") or []:
            if k.lower() == b"authorization":
                got = v
                break
        if not hmac.compare_digest(got, self._expected):
            body = json.dumps({"ok": False, "error_code": "unauthorized",
                               "detail": "Thiếu hoặc sai bearer token của SagaReel.",
                               "hint": "Gửi header Authorization: Bearer <SAGAREEL_TOKEN>."},
                              ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                    (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_http_app(server: Server, token: str | None, *, host: str = "127.0.0.1") -> Any:
    if not token:
        raise AppError("token_required", "Chế độ HTTP cần token SagaReel để xác thực client MCP.",
                       hint="Đặt SAGAREEL_TOKEN, hoặc chạy app trên máy này để có data/local-agent.token.")
    return BearerAuth(server.streamable_http_app(host=host), token)
