from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.core.crypto import SecretBox
from app.db.client import MongoManager
from app.db.indexes import ensure_indexes
from app.logging_config import configure_logging
from app.bot.manager import BotManager
from app.web.routes import build_router
from app.web.webhook import build_webhook_router
from app.web.app import WebAppAPI


class Platform:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mongo = MongoManager(settings)
        self.manager: BotManager | None = None
        self.logger = logging.getLogger("platform")

    async def startup(self) -> None:
        await self.mongo.connect()
        await ensure_indexes(self.mongo)
        self.manager = BotManager(self.settings, self.mongo, SecretBox(self.settings.token_encryption_key))
        await self.manager.start_master()
        await self.manager.bootstrap_children()
        if self.settings.mode == "webhook":
            # The first webhook registration occurs during startup; the monitor
            # re-registers it after the HTTP server has had time to become reachable.
            self.logger.info("webhook_post_start_reconciliation_scheduled")
        self.logger.info("platform_ready")

    async def shutdown(self) -> None:
        if self.manager:
            await self.manager.shutdown()
        await self.mongo.close()


def create_app() -> FastAPI:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    platform = Platform(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await platform.startup()
        app.state.platform = platform
        try:
            yield
        finally:
            await platform.shutdown()

    app = FastAPI(title="Telegram Multi-Bot Platform", lifespan=lifespan)
    app.state.platform = platform

    @app.get("/app")
    async def mini_app():
        path = Path(__file__).resolve().parent / "webapp" / "index.html"
        from fastapi.responses import HTMLResponse
        return HTMLResponse(path.read_text(encoding="utf-8"))

    app.mount("/app/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "webapp" / "static")), name="webapp-static")

    # Routers are bound to the initialized Platform at request time.
    app.include_router(_late_router(build_router, platform))
    app.include_router(_late_router(build_webhook_router, platform))
    app.include_router(_late_router(lambda p: WebAppAPI(p).router(), platform))
    return app


def _late_router(builder, platform):
    """Build routers now; handlers access the same platform object throughout app lifetime."""
    return builder(platform)


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=settings.environment == "development")
