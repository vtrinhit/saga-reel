"""App factory FastAPI của SagaReel Studio."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sagareel import APP_NAME, __version__
from sagareel import ops as ops_pkg
from sagareel.api.routes_media import router as media_router
from sagareel.api.routes_ops import build_router
from sagareel.config import AppSettings, load_settings
from sagareel.container import Services
from sagareel.core.errors import AppError, internal_error, payload, validation_failed
from sagareel.db.session import Database

__all__ = ["APP_NAME", "create_app"]

logger = logging.getLogger("sagareel")


def _error_response(e: AppError) -> JSONResponse:
    return JSONResponse(payload(e), status_code=e.status)


class UnhandledErrorMiddleware:
    """Lỗi không lường trước → 500 `internal_error`; traceback chỉ vào log, không lộ ra response.

    Dùng middleware ASGI thay vì `exception_handlers[Exception]` vì Starlette luôn ném lại lỗi sau handler đó
    (server log trùng, TestClient nổ). Middleware này nằm trong ServerErrorMiddleware nên lỗi dừng tại đây.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("Lỗi không lường trước khi xử lý %s %s", scope.get("method"), scope.get("path"))
            if started:  # đã gửi header, không thể đổi response nữa
                raise
            await _error_response(internal_error())(scope, receive, send)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(validation_failed(exc.errors(), strip_body=True))

    app.add_middleware(UnhandledErrorMiddleware)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    services = Services(
        settings=settings,
        boot_id=uuid.uuid4().hex,
        db=Database(settings.db_path),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            # Truy cập DB từ code async luôn qua thread (không chặn event loop).
            await anyio.to_thread.run_sync(services.db.migrate)
            # Seed, worker… được các task sau bổ sung. Không tự gọi provider nào khi khởi động.
            yield
        finally:
            services.db.dispose()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.services = services
    _install_error_handlers(app)

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "app": APP_NAME,
            "version": __version__,
            "boot_id": services.boot_id,
        }

    ops_pkg.load_all()
    app.include_router(build_router())
    app.include_router(media_router)
    return app
