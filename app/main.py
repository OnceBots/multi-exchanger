from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.bot.manager import BotManager
from app.config import Settings
from app.core.crypto import SecretBox
from app.db.client import MongoManager
from app.db.indexes import ensure_indexes
from app.logging_config import configure_logging
from app.web.routes import build_router
from app.web.webapp import build_webapp_router
from app.web.webhook import build_webhook_router


class Platform:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mongo = MongoManager(settings)
        self.manager: BotManager | None = None
        self.ready = False
        self.logger = logging.getLogger("platform")

    async def startup(self) -> None:
        await self.mongo.connect()
        await ensure_indexes(self.mongo)
        self.manager = BotManager(self.settings, self.mongo, SecretBox(self.settings.token_encryption_key))
        await self.manager.start()
        self.ready = True

    async def shutdown(self) -> None:
        self.ready = False
        if self.manager:
            await self.manager.shutdown()
        await self.mongo.close()


def create_app() -> FastAPI:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    platform = Platform(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await platform.startup()
            app.state.platform = platform
            yield
        except Exception:
            logging.getLogger("platform").exception("startup_failed")
            raise
        finally:
            await platform.shutdown()

    app = FastAPI(title="Telegram Multi-Bot Platform", version="1.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.state.platform = platform
    static_dir = Path(__file__).resolve().parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(build_router(platform))
    app.include_router(build_webhook_router(platform))
    app.include_router(build_webapp_router(platform))
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port)
