"""`python -m sagareel serve [--host HOST] [--port PORT]`."""

from __future__ import annotations

import argparse
import dataclasses
import sys


def _serve(argv: list[str]) -> int:
    import uvicorn

    from sagareel.app import create_app
    from sagareel.config import load_settings

    settings = load_settings()
    parser = argparse.ArgumentParser(prog="sagareel serve", description="Chạy máy chủ SagaReel Studio.")
    parser.add_argument("--host", default=settings.host, help=f"Địa chỉ lắng nghe (mặc định {settings.host})")
    parser.add_argument("--port", type=int, default=settings.port, help=f"Cổng (mặc định {settings.port})")
    args = parser.parse_args(argv)

    settings = dataclasses.replace(settings, host=args.host, port=args.port)
    # proxy_headers=False: giữ IP socket thật trong scope["client"] (uvicorn mặc định tin X-Forwarded-* từ 127.0.0.1
    # và ghi đè IP/scheme) — middleware bảo mật tự đọc header của tunnel khi cần.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=False)
    return 0


def _utf8_stdio() -> None:
    # Khi stdout/stderr bị chuyển hướng trên Windows, mã hoá mặc định (cp1252) không in được tiếng Việt.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return _serve(argv[1:])
    # Các lệnh khác sẽ được chuyển cho sagareel.cli.main (chưa có ở giai đoạn này).
    print("Lệnh chưa được hỗ trợ. Hiện chỉ có: python -m sagareel serve [--host HOST] [--port PORT]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
