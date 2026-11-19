"""`sagareel mcp [--http] [--port 8991] …`: chạy MCP server (`python -m sagareel_mcp`) bằng venv riêng `.venv-mcp`.

MCP SDK kéo theo thư viện xung đột với venv chính, nên server MCP sống ở venv riêng và chỉ nói chuyện với app qua HTTP.
Python dùng: `SAGAREEL_MCP_PYTHON` nếu đặt, không thì `<repo>/.venv-mcp/Scripts/python.exe` (Windows) hoặc
`<repo>/.venv-mcp/bin/python`. stdin/stdout được kế thừa nguyên vẹn (stdio là kênh giao thức MCP).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PYTHON_ENV = "SAGAREEL_MCP_PYTHON"
# backend/sagareel/cli/mcp_launcher.py → gốc repo
REPO_ROOT = Path(__file__).resolve().parents[3]


def mcp_python() -> Path | None:
    if os.environ.get(PYTHON_ENV):
        return Path(os.environ[PYTHON_ENV])
    venv = REPO_ROOT / ".venv-mcp"
    for cand in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
        if cand.is_file():
            return cand
    return None


def command(argv: list[str]) -> tuple[list[str], dict[str, str]] | None:
    python = mcp_python()
    if python is None:
        return None
    env = dict(os.environ)
    paths = [str(REPO_ROOT / "mcp_server"), str(REPO_ROOT / "backend")]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return [str(python), "-m", "sagareel_mcp", *argv], env


def main(argv: list[str]) -> int:
    built = command(argv)
    if built is None:
        print(
            "mcp_unavailable: Không tìm thấy Python của venv MCP (.venv-mcp). "
            f"(gợi ý: tạo `.venv-mcp` ở gốc repo và cài `pip install -e mcp_server`, hoặc đặt {PYTHON_ENV}.)",
            file=sys.stderr,
        )
        return 2
    cmd, env = built
    try:
        return subprocess.call(cmd, env=env)
    except KeyboardInterrupt:
        return 130
