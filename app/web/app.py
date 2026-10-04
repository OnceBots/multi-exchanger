from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.services.webapp_auth import WebAppAuthError, validate_init_data


class WebAppAPI:
    def __init__(self, platform) -> None:
        self.platform = platform

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/api")

        async def auth(request: Request, bot_id: int) -> tuple[dict, object]:
            runtime = self.platform.manager.registry.get(bot_id)
            if not runtime:
                raise HTTPException(status_code=404, detail="bot_offline")
            init_data = request.headers.get("X-Telegram-Init-Data", "")
            token = self.platform.manager.token_service.decrypt(runtime.info.encrypted_token)
            try:
                user = validate_init_data(init_data, token, self.platform.settings.webapp_auth_max_age_seconds)
            except WebAppAuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            return user, runtime

        @router.get("/{bot_id}/rooms")
        async def rooms(bot_id: int, request: Request):
            user, runtime = await auth(request, bot_id)
            docs = await self.platform.manager.repositories.room.list_public(bot_id, 50)
            own = await self.platform.manager.repositories.room.list_for_user(bot_id, int(user["id"]), 50)
            return {"bot_id": bot_id, "rooms": docs, "my_rooms": own}

        @router.post("/{bot_id}/rooms/{room_id}/join")
        async def join(bot_id: int, room_id: str, request: Request):
            user, runtime = await auth(request, bot_id)
            room = await runtime.ctx.services.room.join_room(bot_id, int(user["id"]), room_id)
            if not room:
                raise HTTPException(status_code=404, detail="room_not_found")
            return {"ok": True, "room": room}

        @router.post("/{bot_id}/rooms")
        async def create(bot_id: int, request: Request):
            user, runtime = await auth(request, bot_id)
            body = await request.json()
            room = await runtime.ctx.services.room.create_room(bot_id, int(user["id"]), str(body.get("name", "Sala")), str(body.get("description", "")), str(body.get("visibility", "PUBLIC")), int(body.get("max_members", 100)))
            return {"ok": True, "room": room}

        return router


def build_webapp(platform) -> tuple[APIRouter, HTMLResponse]:
    path = Path(__file__).resolve().parents[1] / "webapp" / "index.html"
    content = path.read_text(encoding="utf-8")
    return WebAppAPI(platform).router(), HTMLResponse(content)
