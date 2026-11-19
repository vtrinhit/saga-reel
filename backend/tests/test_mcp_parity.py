"""Đối chiếu op ↔ tool MCP (spec §23.5), dùng đúng quy tắc đặt tên của `mcp_server/sagareel_mcp/tools.py`.

`tools.py` thuần Python (không import `mcp`) nên venv chính import được. Duyệt `REGISTRY` sống: op thêm sau tự được kiểm.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sagareel import ops as ops_pkg
from sagareel.cli import main as cli_main
from sagareel.cli import mcp_launcher
from sagareel.core import ops

MCP_DIR = Path(__file__).resolve().parents[2] / "mcp_server"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from sagareel_mcp import tools as mcp_tools


@pytest.fixture(scope="module")
def catalog() -> list[dict]:
    ops_pkg.load_all()
    return ops.catalog()


def test_mcp_package_does_not_import_backend_heavy_deps():
    # tools.py chỉ dùng stdlib: import được ở venv chính lẫn .venv-mcp.
    assert "mcp" not in mcp_tools.__dict__


def test_every_op_reachable_via_mcp(catalog):
    tools = mcp_tools.build_tools(catalog)
    by_op = {t.op for t in tools.values() if t.op}
    assert "sr_call" in tools and "sr_describe_ops" in tools
    for o in catalog:
        if o["mcp"] == "tool" and o["name"] != "job.wait":
            assert mcp_tools.tool_name(o["name"]) in tools, o["name"]
            assert o["name"] in by_op
    if "job.wait" in {o["name"] for o in catalog}:
        assert tools["sr_job_wait"].kind == "job_wait"
    assert len(tools) == len(set(tools))


def test_tool_names_valid_and_destructive_have_preview(catalog):
    assert mcp_tools.mapping_problems(catalog) == []


def test_destructive_tools_have_preview_op(catalog):
    names = {o["name"] for o in catalog}
    tools = mcp_tools.build_tools(catalog)
    missing = [
        t.name for t in tools.values()
        if t.kind == "op" and t.annotations["destructive_hint"]
        and not ({f"{t.op.split('.', 1)[0]}.delete_preview", f"{t.op}_preview"} & names)
    ]
    assert missing == []


def test_mcp_is_reserved_cli_command():
    assert "mcp" in cli_main.RESERVED_COMMANDS
    args = cli_main.build_parser([]).parse_args(["mcp"])
    assert args.handler is cli_main._cmd_mcp


def test_mcp_launcher_command(monkeypatch, tmp_path):
    py = tmp_path / "python.exe"
    py.write_text("")
    monkeypatch.setenv(mcp_launcher.PYTHON_ENV, str(py))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    cmd, env = mcp_launcher.command(["--http", "--port", "8991"])
    assert cmd == [str(py), "-m", "sagareel_mcp", "--http", "--port", "8991"]
    assert str(mcp_launcher.REPO_ROOT / "mcp_server") in env["PYTHONPATH"]


def test_mcp_dispatched_before_catalog(monkeypatch):
    seen = []
    monkeypatch.setattr(mcp_launcher, "main", lambda argv: seen.append(argv) or 0)
    # Không có transport/app: nếu CLI tải catalog trước thì sẽ lỗi app_not_running.
    assert cli_main.main(["mcp", "--http"]) == 0
    assert seen == [["--http"]]


def test_mcp_launcher_missing_python(monkeypatch, capsys):
    monkeypatch.setattr(mcp_launcher, "mcp_python", lambda: None)
    assert mcp_launcher.main([]) == 2
    assert "mcp_unavailable" in capsys.readouterr().err
