"""App factory FastAPI của SagaReel Studio."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sagareel import APP_NAME, __version__
from sagareel import handlers as handlers_pkg
from sagareel import ops as ops_pkg
from sagareel.api.middleware import SecurityMiddleware
from sagareel.api.routes_auth import build_router as build_auth_router
from sagareel.api.routes_events import build_router as build_events_router
from sagareel.api.routes_export import router as export_router
from sagareel.api.routes_media import router as media_router
from sagareel.api.routes_ops import build_router
from sagareel.api.routes_upload import router as upload_router
from sagareel.api.static import build_static_router
from sagareel.config import AppSettings, load_settings
from sagareel.container import Services
from sagareel.core.errors import AppError, internal_error, payload, validation_failed
from sagareel.core.events import EventBus
from sagareel.core.resolver import SCHEMA_PATH, Resolver, load_schema
from sagareel.db.session import Database
from sagareel.services import auth, seed, skills
from sagareel.services.providers import ProviderService
from sagareel.services.worker import Worker

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


def _seed_env(services: Services) -> None:
    """Seed từ .env.local; lỗi (file hỏng, DB bận…) chỉ ghi log — tên lỗi, không nội dung — và app vẫn chạy."""
    try:
        seed.seed_from_env(services.db, services.settings.env_file, resolver=services.resolver)
    except Exception as e:  # noqa: BLE001 — traceback có thể chứa giá trị từ file env, không log
        logger.error("Không seed được từ .env.local (%s); dùng op provider.import_env để thử lại", type(e).__name__)


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
        resolver=Resolver(load_schema(SCHEMA_PATH)),
        bus=EventBus(),
    )
    services.worker = Worker(services, lane_limit=services.lane_limit)
    # Kết nối provider: lane `conn:<id>` dùng concurrency của kết nối, `ffmpeg`/`story` dùng trần loại concurrency.render/llm.
    # Dựng chỉ import adapter (registry.discover), không gọi provider nào.
    services.providers = ProviderService(services)
    services.worker.lane_limit = services.providers.lane_limit

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.bus.bind_loop(asyncio.get_running_loop())  # publish từ thread worker chuyển về loop này
        try:
            # Truy cập DB từ code async luôn qua thread (không chặn event loop).
            await anyio.to_thread.run_sync(services.db.migrate)
            # Dữ liệu mặc định (công việc LLM, dự án mẫu) rồi kết nối từ .env.local (lần đầu). Chỉ ghi DB, không gọi
            # provider nào.
            await anyio.to_thread.run_sync(seed.seed_defaults, services.db)
            if services.settings.seed_samples:
                await anyio.to_thread.run_sync(seed.seed_samples, services.db)
            await anyio.to_thread.run_sync(_seed_env, services)
            # File skill vận hành ↔ DB (skill.list/get chỉ đọc; lỗi chỉ cảnh báo).
            await anyio.to_thread.run_sync(skills.sync_on_startup, services)
            # Token agent cục bộ cho CLI/MCP trên máy này (`data/local-agent.token`); lỗi chỉ cảnh báo.
            await anyio.to_thread.run_sync(auth.ensure_local_agent_token_safe, services)
            # Job `running` sót từ lần trước → queued (nối lại được) hoặc interrupted; rồi mới chạy worker.
            await anyio.to_thread.run_sync(services.worker.recover)
            await services.worker.start()
            # Không tự gọi provider nào khi khởi động.
            yield
        finally:
            await services.worker.stop()
            services.db.dispose()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.services = services
    # Thêm trước handler lỗi → nằm trong UnhandledErrorMiddleware (lỗi bất ngờ vẫn ra JSON chuẩn).
    app.add_middleware(SecurityMiddleware)
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
    handlers_pkg.load_all()  # job handler theo tính năng (`sagareel/handlers/*.py`) tự đăng ký
    app.include_router(build_router())
    app.include_router(build_events_router())
    app.include_router(media_router)
    app.include_router(upload_router)
    app.include_router(export_router)
    app.include_router(build_auth_router())
    app.include_router(build_static_router(settings.frontend_dist))  # SPA fallback: luôn để include này cuối cùng
    return app
