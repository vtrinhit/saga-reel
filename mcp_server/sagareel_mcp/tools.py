"""Catalog op → định nghĩa tool MCP (thuần Python, không import `mcp`).

Main venv (test đối chiếu `backend/tests/test_mcp_parity.py`) import được module này để dùng đúng một quy tắc đặt tên.

- Op `mcp="tool"` → tool `sr_<group>_<action>` (`.` → `_`), input schema = params JSON Schema của op, annotation từ
  cờ catalog.
- Tool chung luôn có: `sr_describe_ops`, `sr_call`, `sr_reload`, `sr_job_wait` (thay tool sinh từ `job.wait`: chờ
  theo từng khúc 600 s).
- Bí danh luôn có nếu op tồn tại (kể cả op `mcp="call"`): `sr_entity_get/list/patch`, `sr_history_undo`,
  `sr_job_logs`, `sr_sequence_status` (→ `sequence.get`), `sr_estimate` (→ mọi op `*.estimate`).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal

TOOL_PREFIX = "sr_"
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
OP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# Tên tool chung, không op nào được sinh đè lên.
RESERVED_TOOLS = frozenset({"sr_describe_ops", "sr_call", "sr_reload", "sr_job_wait", "sr_estimate"})

# Bí danh tool → op (đăng ký nếu op có trong catalog, bất kể cờ mcp).
ALIASES: dict[str, str] = {
    "sr_entity_get": "entity.get",
    "sr_entity_list": "entity.list",
    "sr_entity_patch": "entity.patch",
    "sr_history_undo": "history.undo",
    "sr_job_logs": "job.logs",
    "sr_sequence_status": "sequence.get",
}

JOB_WAIT_MAX_S = 6 * 3600

Kind = Literal["op", "describe", "call", "reload", "job_wait", "estimate"]


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    annotations: dict[str, bool]
    kind: Kind
    op: str | None = None  # op gọi tới (kind="op")
    extra: dict[str, Any] = field(default_factory=dict)


def tool_name(op_name: str) -> str:
    """`project.delete_preview` → `sr_project_delete_preview`."""
    return TOOL_PREFIX + op_name.replace(".", "_")


def preview_ops_for(entry: dict) -> tuple[str, str]:
    """Op xem trước chấp nhận cho op destructive: `<group>.delete_preview` hoặc `<op>_preview`."""
    return f"{entry['group']}.delete_preview", f"{entry['name']}_preview"


def preview_for(entry: dict, names: set[str]) -> str | None:
    return next((p for p in preview_ops_for(entry) if p in names), None)


def annotations_for(entry: dict) -> dict[str, bool]:
    """MCP mặc định destructiveHint=true cho tool không read-only → luôn đặt rõ."""
    read_only = bool(entry.get("read_only"))
    return {
        "read_only_hint": read_only,
        "destructive_hint": bool(entry.get("destructive")) and not read_only,
        "idempotent_hint": bool(entry.get("idempotent")),
        "open_world_hint": bool(entry.get("costly")),
    }


def describe(entry: dict, names: set[str]) -> str:
    """Mô tả tiếng Việt: tóm tắt op + hướng dẫn an toàn theo cờ."""
    lines = [f"{entry.get('summary') or entry['name']} (op `{entry['name']}`)."]
    if entry.get("destructive"):
        preview = preview_for(entry, names) or preview_ops_for(entry)[0]
        lines.append(
            f"XOÁ/PHÁ HUỶ: luôn gọi `{tool_name(preview)}` trước để xem ảnh hưởng và lấy `confirm_token`, "
            "báo người dùng, rồi mới gọi tool này với `confirm_token` đó."
        )
    if entry.get("costly"):
        lines.append("TỐN TIỀN/HẠN MỨC: chạy với `dry_run: true` trước (nếu params có) để xem ước tính; tôn trọng "
                     "ngân sách (`budget_exceeded`).")
    if entry.get("long_running"):
        lines.append("Chạy lâu: trả `job_id` — dùng `sr_job_wait` để chờ, `sr_job_logs` để xem nhật ký.")
    if entry.get("read_only"):
        lines.append("Chỉ đọc.")
    return " ".join(lines)


def input_schema_for(entry: dict) -> dict:
    schema = copy.deepcopy(entry.get("params_schema") or {})
    if not isinstance(schema, dict):
        schema = {}
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def _op_tool(name: str, entry: dict, names: set[str]) -> ToolDef:
    return ToolDef(
        name=name,
        description=describe(entry, names),
        input_schema=input_schema_for(entry),
        annotations=annotations_for(entry),
        kind="op",
        op=entry["name"],
    )


def _generic_tools(catalog: list[dict]) -> list[ToolDef]:
    groups = sorted({o["group"] for o in catalog})
    tools = [
        ToolDef(
            name="sr_describe_ops",
            description=(
                "Danh mục thao tác của SagaReel Studio (tên, mô tả, cờ read_only/destructive/costly/long_running, "
                "JSON Schema params). Lọc theo `group` để gọn. Thao tác không có tool riêng thì gọi qua `sr_call`."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "group": {"type": "string", "description": "Nhóm op, vd. project, job"
                              + (f" (hiện có: {', '.join(groups)})" if groups else "")},
                    "with_schema": {"type": "boolean", "default": True,
                                    "description": "Kèm JSON Schema params (false = chỉ tên + mô tả)"},
                },
            },
            annotations={"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True,
                         "open_world_hint": False},
            kind="describe",
        ),
        ToolDef(
            name="sr_call",
            description=(
                "Gọi một thao tác bất kỳ theo tên với params JSON (app kiểm params theo schema trong catalog và trả "
                "lỗi tiếng Việt kèm gợi ý). Xem `sr_describe_ops` trước. Op destructive: gọi `*.delete_preview` "
                "trước; op tốn tiền: thử `dry_run: true` trước."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "op": {"type": "string", "description": "Tên op, vd. project.get"},
                    "params": {"type": "object", "description": "Params của op", "default": {}},
                },
                "required": ["op"],
            },
            annotations={"read_only_hint": False, "destructive_hint": True, "idempotent_hint": False,
                         "open_world_hint": True},
            kind="call",
        ),
        ToolDef(
            name="sr_reload",
            description="Tải lại danh mục thao tác từ app (sau khi app cập nhật/khởi động lại) và làm mới danh sách "
                        "tool.",
            input_schema={"type": "object", "properties": {}},
            annotations={"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True,
                         "open_world_hint": False},
            kind="reload",
        ),
    ]
    return tools


def _job_wait_tool() -> ToolDef:
    return ToolDef(
        name="sr_job_wait",
        description=(
            "Chờ các job rời queued/running (kết thúc succeeded/failed/cancelled, hoặc dừng chờ người: "
            "needs_attention, interrupted, waiting_*) hoặc hết `timeout` giây; trả trạng thái từng job và "
            "`timed_out`. Hết giờ không phải lỗi — gọi lại để chờ tiếp."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "description": "Id các job"},
                "timeout": {"type": "integer", "minimum": 0, "maximum": JOB_WAIT_MAX_S, "default": 600,
                            "description": "Số giây chờ tối đa"},
            },
            "required": ["ids"],
        },
        annotations={"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True,
                     "open_world_hint": False},
        kind="job_wait",
    )


def _estimate_tool(estimate_ops: list[str]) -> ToolDef:
    return ToolDef(
        name="sr_estimate",
        description="Ước tính chi phí/thời gian (số ảnh, video, giây, tiền nếu biết) trước khi chạy thao tác tốn tiền.",
        input_schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": estimate_ops,
                       "description": "Op ước tính; bỏ trống + có sequence_id → sequence.estimate"},
                "sequence_id": {"type": "integer", "description": "Lối tắt: ước tính cả tập (sequence.estimate)"},
                "params": {"type": "object", "default": {}, "description": "Params của op ước tính"},
            },
        },
        annotations={"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True,
                     "open_world_hint": False},
        kind="estimate",
        extra={"ops": estimate_ops},
    )


def build_tools(catalog: list[dict]) -> dict[str, ToolDef]:
    """Toàn bộ tool (tên → định nghĩa), thứ tự ổn định: tool chung trước, rồi theo tên op."""
    names = {o["name"] for o in catalog}
    by_name = {o["name"]: o for o in catalog}
    tools: dict[str, ToolDef] = {t.name: t for t in _generic_tools(catalog)}
    tools["sr_job_wait"] = _job_wait_tool()

    for entry in sorted(catalog, key=lambda o: o["name"]):
        if entry.get("mcp") != "tool" or not OP_NAME_RE.match(entry["name"]):
            continue
        name = tool_name(entry["name"])
        if name in RESERVED_TOOLS or name in tools or not TOOL_NAME_RE.match(name):
            continue  # job.wait → sr_job_wait chung; tên quá dài/trùng: vẫn gọi được qua sr_call
        tools[name] = _op_tool(name, entry, names)

    for alias, op_name in ALIASES.items():
        if alias not in tools and op_name in by_name:
            tools[alias] = _op_tool(alias, by_name[op_name], names)

    estimate_ops = sorted(n for n in names if n.endswith(".estimate"))
    if estimate_ops:
        tools["sr_estimate"] = _estimate_tool(estimate_ops)
    return tools


def mapping_problems(catalog: list[dict]) -> list[str]:
    """Đối chiếu op ↔ tool MCP: tên tool hợp lệ, không trùng; op destructive có op xem trước."""
    names = {o["name"] for o in catalog}
    problems: list[str] = []
    seen: dict[str, str] = {}
    for o in sorted(catalog, key=lambda o: o["name"]):
        if o.get("mcp") == "tool":
            t = tool_name(o["name"])
            if not TOOL_NAME_RE.match(t):
                problems.append(f"{o['name']}: tên tool {t!r} không hợp lệ (cần [A-Za-z0-9_-], ≤ 64 ký tự)")
            if t in RESERVED_TOOLS and o["name"] != "job.wait":
                problems.append(f"{o['name']}: tên tool {t!r} trùng tool chung")
            if t in seen:
                problems.append(f"{o['name']}: tên tool {t!r} trùng với op {seen[t]}")
            seen[t] = o["name"]
        if o.get("destructive") and preview_for(o, names) is None:
            a, b = preview_ops_for(o)
            problems.append(f"{o['name']}: destructive nhưng thiếu op {a} hoặc {b}")
    return problems
