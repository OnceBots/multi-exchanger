from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from pathlib import Path

from app.services.webapp_auth import validate_init_data


class CreateBotBody(BaseModel):
    token: str = Field(min_length=30, max_length=256)


def build_webapp_router(platform) -> APIRouter:
    router = APIRouter()
    static_dir = Path(__file__).resolve().parent / "static"

    @router.get("/master-app", response_class=HTMLResponse)
    async def master_app():
        return HTMLResponse((static_dir / "master.html").read_text(encoding="utf-8"))

    @router.get("/app", response_class=HTMLResponse)
    async def child_app():
        return HTMLResponse((static_dir / "child.html").read_text(encoding="utf-8"))

    def init_data(request: Request) -> str:
        return request.headers.get("X-Telegram-Init-Data", "") or request.headers.get("Authorization", "")

    @router.post("/api/master/create-bot")
    async def create_bot(request: Request, body: CreateBotBody):
        user = validate_init_data(init_data(request), platform.settings.master_bot_token, platform.settings.webapp_auth_max_age_seconds)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        try:
            info = await platform.manager.register_bot(body.token, int(user["id"]), metadata={"created_from": "master_webapp"})
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:500]) from exc
        return {"ok": True, "bot_id": info.bot_id, "username": info.username, "first_name": info.first_name}

    @router.get("/api/child/rooms")
    async def child_rooms(request: Request, bot_id: int):
        runtime = platform.manager.registry.get(bot_id)
        if not runtime or not runtime.ctx:
            raise HTTPException(status_code=404, detail="Bot no disponible")
        user = validate_init_data(init_data(request), runtime.bot.token, platform.settings.webapp_auth_max_age_seconds)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        rooms = await runtime.ctx.repositories.room.list_for_user(bot_id, int(user["id"]), 50)
        public = await runtime.ctx.repositories.room.list_public(bot_id, 50)
        return {"ok": True, "bot_id": bot_id, "user_id": int(user["id"]), "mine": rooms, "public": public}

    return router
