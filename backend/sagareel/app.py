"""App factory FastAPI của SagaReel Studio."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sagareel import __version__
from sagareel.config import AppSettings, load_settings
from sagareel.container import Services

APP_NAME = "SagaReel Studio"


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    services = Services(settings=settings, boot_id=uuid.uuid4().hex)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Khởi động (migrate, seed, worker) được các task sau bổ sung.
        # Không tự gọi provider nào khi khởi động.
        yield

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.services = services

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "app": APP_NAME,
            "version": __version__,
            "boot_id": services.boot_id,
        }

    return app
