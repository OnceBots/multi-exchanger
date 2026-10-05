from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pathlib import Path

from app.core.exceptions import BotAlreadyRunningError, InvalidBotTokenError
from app.services.webapp_auth import WebAppAuthError, validate_init_data


MASTER_STATIC_DIR = Path(__file__).resolve().parent / "master_static"
MASTER_HTML = MASTER_STATIC_DIR / "index.html"


class MasterWebAppAPI:
    """Authenticated Mini App for public child-bot creation from the Master bot."""

    def __init__(self, platform) -> None:
        self.platform = platform

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/master-app", response_class=HTMLResponse)
        async def master_app() -> HTMLResponse:
            return HTMLResponse(MASTER_HTML.read_text(encoding="utf-8"))

        @router.post("/api/master/create-bot")
        async def create_bot(
            request: Request,
            x_telegram_init_data: str | None = Header(default=None),
        ):
            if not x_telegram_init_data:
                raise HTTPException(status_code=401, detail="initData ausente")
            try:
                user = validate_init_data(
                    x_telegram_init_data,
                    self.platform.settings.master_bot_token,
                    self.platform.settings.webapp_auth_max_age_seconds,
                )
            except WebAppAuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc

            user_id = int(user["id"])
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(status_code=400, detail="json_invalido") from exc

            token = str(body.get("token") or "").strip()
            platform_name = str(body.get("name") or "").strip()[:80]
            requested_username = str(body.get("username_or_url") or "").strip()[:160]

            if not token or ":" not in token or len(token) > 256:
                raise HTTPException(status_code=400, detail="token_invalido")
            if not platform_name:
                raise HTTPException(status_code=400, detail="nombre_requerido")

            metadata = {
                "platform_name": platform_name,
                "requested_username": requested_username,
                "creation_source": "master_webapp",
            }
            try:
                info = await asyncio.wait_for(
                    self.platform.manager.register_bot(token, user_id, metadata=metadata),
                    timeout=60,
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="telegram_validation_timeout") from exc
            except InvalidBotTokenError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except BotAlreadyRunningError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception as exc:
                self.platform.logger.exception("master_webapp_create_bot_failed owner_id=%s", user_id)
                raise HTTPException(status_code=500, detail="no_se_pudo_crear_el_bot") from exc

            return {
                "ok": True,
                "bot": {
                    "bot_id": int(info.bot_id),
                    "username": info.username,
                    "name": platform_name,
                    "url": f"https://t.me/{info.username}" if info.username else None,
                    "status": "RUNNING",
                },
            }

        return router
