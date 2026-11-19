"""`python -m sagareel_mcp` (stdio, mặc định) hoặc `python -m sagareel_mcp --http [--port 8991]` (streamable HTTP,
chỉ nghe 127.0.0.1, bắt buộc `Authorization: Bearer <token SagaReel>`).

stdout ở chế độ stdio là kênh giao thức: mọi log ra stderr.
"""

from __future__ import annotations

import argparse
import logging
import sys

import anyio

DEFAULT_HTTP_PORT = 8991


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore", "mcp", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sagareel-mcp", description="MCP server của SagaReel Studio (gọi app qua HTTP).")
    p.add_argument("--http", action="store_true", help="Chạy streamable HTTP thay vì stdio")
    p.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help=f"Cổng HTTP (mặc định {DEFAULT_HTTP_PORT})")
    p.add_argument("--verbose", action="store_true", help="Log chi tiết (ra stderr)")
    return p


async def _run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    from sagareel_mcp.client import SagaReelClient
    from sagareel_mcp.server import build_server, init_options

    client = SagaReelClient()
    server, _state = build_server(client)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, init_options(server))
    finally:
        await client.aclose()


def _run_http(port: int) -> int:
    import uvicorn

    from sagareel_mcp.client import AppError, SagaReelClient
    from sagareel_mcp.server import build_http_app, build_server

    client = SagaReelClient()
    server, _state = build_server(client)
    try:
        app = build_http_app(server, client.token)
    except AppError as e:
        print(e.text(), file=sys.stderr)
        return 2
    logging.getLogger("sagareel_mcp").info("MCP HTTP tại http://127.0.0.1:%s/mcp (token nguồn: %s)", port,
                                           client.token_source)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _setup_logging(args.verbose)
    if args.http:
        return _run_http(args.port)
    anyio.run(_run_stdio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
