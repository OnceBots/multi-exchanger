from __future__ import annotations

import html
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RoomVisibility
from app.services.webapp_auth import authenticate_webapp_request


class CreateBotBody(BaseModel):
    token: str = Field(min_length=30, max_length=256)


class CreateRoomBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    visibility: str = Field(default=RoomVisibility.PUBLIC.value)
    max_members: int = Field(default=100, ge=2, le=10000)
    duration_minutes: int = Field(default=0, ge=0, le=43200)
    allow_photo: bool = True
    allow_video: bool = True
    allow_files: bool = True
    allow_animation: bool = True
    allow_albums: bool = True


class RoomRefBody(BaseModel):
    room_id: str = Field(min_length=1, max_length=64)


def build_webapp_router(platform) -> APIRouter:
    router = APIRouter()
    logger = logging.getLogger("webapp")
    static_dir = Path(__file__).resolve().parent / "static"

    @router.get("/master-app", response_class=HTMLResponse)
    async def master_app():
        return HTMLResponse((static_dir / "master.html").read_text(encoding="utf-8"))

    @router.get("/app", response_class=HTMLResponse)
    async def child_app():
        return HTMLResponse((static_dir / "child.html").read_text(encoding="utf-8"))

    def auth_child(request: Request, runtime):
        user, source = authenticate_webapp_request(
            request,
            bot_id=runtime.ctx.bot_id,
            bot_token=runtime.bot.token,
            launch_secret=platform.settings.webhook_secret,
            max_age_seconds=platform.settings.webapp_auth_max_age_seconds,
        )
        return user, source

    def auth_master(request: Request):
        user, source = authenticate_webapp_request(
            request,
            bot_id=0,
            bot_token=platform.settings.master_bot_token,
            launch_secret=platform.settings.webhook_secret,
            max_age_seconds=platform.settings.webapp_auth_max_age_seconds,
        )
        return user, source

    @router.post("/api/master/create-bot")
    async def create_bot(request: Request, body: CreateBotBody):
        user, source = auth_master(request)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        try:
            info = await platform.manager.register_bot(body.token, int(user["id"]), metadata={"created_from": "master_webapp", "auth_source": source})
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:500]) from exc
        return {"ok": True, "bot_id": info.bot_id, "username": info.username, "first_name": info.first_name}

    def get_runtime(bot_id: int):
        runtime = platform.manager.registry.get(bot_id)
        if not runtime or not runtime.ctx:
            raise HTTPException(status_code=404, detail="Bot no disponible")
        if runtime.status.value != "RUNNING":
            raise HTTPException(status_code=503, detail="Bot no está disponible temporalmente")
        return runtime

    @router.get("/api/child/rooms")
    async def child_rooms(request: Request, bot_id: int):
        runtime = get_runtime(bot_id)
        user, source = auth_child(request, runtime)
        if not user or not user.get("id"):
            logger.warning(
                "mini_app_auth_failed bot_id=%s init_data=%s launch_header=%s launch_query=%s source=%s",
                bot_id,
                bool(request.headers.get("X-Telegram-Init-Data") or request.headers.get("Authorization")),
                bool(request.headers.get("X-Mini-App-Launch-Token")),
                bool(request.query_params.get("launch")),
                source,
            )
            raise HTTPException(status_code=401, detail="Mini App no autenticada. Ábrela desde el botón Mini App de Telegram.")
        uid = int(user["id"])
        rooms = await runtime.ctx.repositories.room.list_for_user(bot_id, uid, 50)
        public = await runtime.ctx.repositories.room.list_public(bot_id, 50)
        profile = await runtime.ctx.repositories.user.get(bot_id, uid) or {"bot_id": bot_id, "user_id": uid}
        return {"ok": True, "bot_id": bot_id, "bot_username": runtime.ctx.bot_username, "user_id": uid, "auth_source": source, "profile": profile, "mine": rooms, "public": public}

    @router.get("/api/child/profile")
    async def child_profile(request: Request, bot_id: int):
        runtime = get_runtime(bot_id)
        user, _ = auth_child(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        uid = int(user["id"])
        profile = await runtime.ctx.repositories.user.get(bot_id, uid) or {"bot_id": bot_id, "user_id": uid}
        return {"ok": True, "profile": profile}

    @router.post("/api/child/rooms")
    async def create_room(request: Request, bot_id: int, body: CreateRoomBody):
        runtime = get_runtime(bot_id)
        user, _ = auth_child(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        if body.visibility not in {RoomVisibility.PUBLIC.value, RoomVisibility.PRIVATE.value}:
            raise HTTPException(status_code=422, detail="Visibilidad inválida")
        room = await runtime.ctx.services.rooms.create_room(
            bot_id,
            int(user["id"]),
            body.name,
            body.visibility,
            body.max_members,
            {
                "allow_photo": body.allow_photo,
                "allow_video": body.allow_video,
                "allow_files": body.allow_files,
                "allow_animation": body.allow_animation,
                "allow_albums": body.allow_albums,
            },
            body.duration_minutes,
        )
        room = dict(room)
        username = (runtime.ctx.bot_username or "").lstrip("@").strip()
        if username and room.get("invite_code"):
            room["share_link"] = f"https://t.me/{username}?start=room_{room['invite_code']}"
        return {"ok": True, "room": room}

    @router.post("/api/child/rooms/{room_id}/join")
    async def join_room(request: Request, room_id: str, bot_id: int):
        runtime = get_runtime(bot_id)
        user, _ = auth_child(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        ok, message, room = await runtime.ctx.services.rooms.join(bot_id, int(user["id"]), room_id)
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        return {"ok": True, "message": message, "room": room}

    @router.post("/api/child/rooms/{room_id}/leave")
    async def leave_room(request: Request, room_id: str, bot_id: int):
        runtime = get_runtime(bot_id)
        user, _ = auth_child(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        ok, message = await runtime.ctx.services.rooms.leave(bot_id, room_id, int(user["id"]))
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        return {"ok": True, "message": message}

    return router
