"""Đối chiếu đầy đủ op ↔ REST ↔ CLI ↔ MCP ↔ frontend (spec §20, §23.5).

- Mọi op có route REST `POST /api/ops/<op>` và lệnh CLI trỏ về đúng op.
- Mọi op gọi được qua MCP (tool riêng hoặc `sr_call`), dùng catalog đã commit `mcp_server/tests/fixtures/catalog.json`
  — và catalog đó phải khớp catalog sống (tạo lại: `mcp_server/scripts/dump_catalog.py`).
- Mọi op destructive có op xem trước.
- Mọi op frontend gọi (`callOp('…')` / `callOpLoose('…')`, bỏ file test) tồn tại trong catalog.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from sagareel import ops as ops_pkg
from sagareel.cli.main import build_parser, command_for
from sagareel.core import ops

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "mcp_server" / "tests" / "fixtures" / "catalog.json"
FRONTEND = REPO / "frontend" / "src"
CALL_RE = re.compile(r"""callOp(?:Loose)?\s*(?:<[^>()]*>)?\s*\(\s*['"`]([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)['"`]""")
FLAGS = ("group", "read_only", "destructive", "idempotent", "costly", "long_running", "mcp", "viewer")

if str(REPO / "mcp_server") not in sys.path:
    sys.path.insert(0, str(REPO / "mcp_server"))
from sagareel_mcp import tools as mcp_tools


@pytest.fixture(scope="module")
def live() -> list[dict]:
    ops_pkg.load_all()
    return ops.catalog()


@pytest.fixture(scope="module")
def committed() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_committed_mcp_catalog_is_fresh(live, committed):
    want = {o["name"]: {k: o[k] for k in FLAGS} for o in live}
    have = {o["name"]: {k: o.get(k) for k in FLAGS} for o in committed}
    assert set(have) == set(want), "Chạy mcp_server/scripts/dump_catalog.py rồi commit catalog.json"
    assert have == want
    assert {o["name"]: o["params_schema"] for o in committed} == {o["name"]: o["params_schema"] for o in live}


def test_every_op_has_rest_route_and_cli_command(client, live):
    openapi = client.get("/openapi.json").json()
    parser = build_parser(live)
    missing = []
    for o in live:
        post = openapi["paths"].get(f"/api/ops/{o['name']}", {}).get("post")
        if post is None or post.get("operationId") != o["name"]:
            missing.append(f"REST {o['name']}")
        group, action = command_for(o["name"])
        try:
            args = parser.parse_args([group, action])
        except SystemExit:
            missing.append(f"CLI {o['name']}")
            continue
        if args.op != o["name"]:
            missing.append(f"CLI {o['name']} → {args.op}")
    assert missing == []


def test_every_op_reachable_via_mcp(committed):
    tools = mcp_tools.build_tools(committed)
    assert "sr_call" in tools and "sr_describe_ops" in tools
    direct = {t.op for t in tools.values() if t.kind == "op"}
    unreachable = []
    for o in committed:
        if o["name"] in direct or o["name"] == "job.wait" and "sr_job_wait" in tools:
            continue
        # Không có tool riêng → phải gọi được qua sr_call (op mcp="call"); op "tool" mà không có tool là lỗi.
        if o["mcp"] != "call":
            unreachable.append(o["name"])
    assert unreachable == []
    assert mcp_tools.mapping_problems(committed) == []


def test_every_destructive_op_has_preview(live):
    names = {o["name"] for o in live}
    missing = [o["name"] for o in live if o["destructive"]
               and not ({f"{o['group']}.delete_preview", f"{o['name']}_preview"} & names)]
    assert missing == []
    previews = [o["name"] for o in live if o["name"].endswith("_preview") and not o["read_only"]]
    assert previews == [], "Op xem trước phải read_only"


def frontend_ops() -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for f in sorted(FRONTEND.rglob("*.ts*")):
        if re.search(r"\.(test|spec)\.tsx?$", f.name):
            continue
        for name in CALL_RE.findall(f.read_text(encoding="utf-8")):
            used.setdefault(name, []).append(f.relative_to(REPO).as_posix())
    return used


def test_frontend_ops_exist(live):
    used = frontend_ops()
    assert used, "Không tìm thấy lời gọi callOp nào trong frontend/src — biểu thức tìm kiếm sai?"
    names = {o["name"] for o in live}
    assert {n: fs for n, fs in used.items() if n not in names} == {}


def test_call_regex():
    src = "callOp('a.b'); callOpLoose(\"c.d_e\", {}); callOp<Foo>(`x.y`); callOp(name)"
    assert CALL_RE.findall(src) == ["a.b", "c.d_e", "x.y"]


def test_spec_and_skill_ops_registered(live):
    by = {o["name"]: o for o in live}
    for name in ("project.export_spec", "project.plan", "project.apply", "skill.list", "skill.get", "skill.update"):
        assert by[name]["mcp"] == "tool", name
    assert by["project.plan"]["read_only"] and by["project.export_spec"]["read_only"]
    assert by["project.apply"]["idempotent"] and not by["project.apply"]["destructive"]
