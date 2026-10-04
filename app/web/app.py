from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.services.webapp_auth import WebAppAuthError, validate_init_data


class WebAppAPI:
    def __init__(self, platform) -> None:
        self.platform = platform

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/api")

        async def auth(request: Request, bot_id: int) -> tuple[dict, object]:
            runtime = self.platform.manager.registry.get(bot_id)
            if not runtime:
                doc = await self.platform.manager.repositories.bots.get(bot_id)
                if not doc:
                    raise HTTPException(status_code=404, detail="bot_not_found")
                try:
                    await self.platform.manager.start_bot(bot_id)
                    runtime = self.platform.manager.registry.get(bot_id)
                except Exception as exc:
                    raise HTTPException(status_code=503, detail="bot_offline") from exc
            if not runtime or runtime.status.value != "RUNNING":
                raise HTTPException(status_code=503, detail="bot_offline")
            init_data = request.headers.get("X-Telegram-Init-Data", "")
            token = self.platform.manager.token_service.decrypt(runtime.info.encrypted_token)
            try:
                user = validate_init_data(init_data, token, self.platform.settings.webapp_auth_max_age_seconds)
            except WebAppAuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            if not user.get("id"):
                raise HTTPException(status_code=401, detail="telegram_user_missing")
            return user, runtime

        async def room_payload(room: dict, bot_id: int, user_id: int) -> dict:
            membership = await self.platform.manager.repositories.room.get_membership(bot_id, room["room_id"], user_id)
            can_manage = await self._can_manage(bot_id, room["room_id"], user_id)
            expires = room.get("expires_at")
            return {
                "room_id": room["room_id"],
                "name": room["name"],
                "description": room.get("description", ""),
                "visibility": room.get("visibility", "PUBLIC"),
                "status": room.get("status", "ACTIVE"),
                "current_members": int(room.get("current_members", 0)),
                "max_members": int(room.get("max_members", 0)),
                "owner_id": int(room.get("owner_id", 0)),
                "settings": room.get("settings", {}),
                "expires_at": expires.isoformat() if expires else None,
                "is_member": bool(membership and not membership.get("banned")),
                "role": membership.get("role") if membership else None,
                "muted": bool(membership.get("muted")) if membership else False,
                "banned": bool(membership.get("banned")) if membership else False,
                "can_manage": can_manage,
                "invite_code": room.get("invite_code") if can_manage or room.get("visibility") == "PUBLIC" else None,
                "bot_username": self.platform.manager.registry.get(bot_id).bot.username if self.platform.manager.registry.get(bot_id) else None,
            }

        @router.get("/{bot_id}/rooms")
        async def rooms(bot_id: int, request: Request, scope: str = "public"):
            user, runtime = await auth(request, bot_id)
            uid = int(user["id"])
            docs = await self.platform.manager.repositories.room.list_for_user(bot_id, uid, 50) if scope == "mine" else await self.platform.manager.repositories.room.list_public(bot_id, 50)
            payload = [await room_payload(doc, bot_id, uid) for doc in docs]
            return {"bot_id": bot_id, "scope": scope, "rooms": payload}

        @router.get("/{bot_id}/rooms/{room_id}")
        async def room_detail(bot_id: int, room_id: str, request: Request):
            user, runtime = await auth(request, bot_id)
            room = await self.platform.manager.repositories.room.get(bot_id, room_id)
            if not room:
                raise HTTPException(status_code=404, detail="room_not_found")
            return await room_payload(room, bot_id, int(user["id"]))

        @router.post("/{bot_id}/rooms/{room_id}/join")
        async def join(bot_id: int, room_id: str, request: Request):
            user, runtime = await auth(request, bot_id)
            room = await runtime.ctx.services.room.join_room(bot_id, int(user["id"]), room_id)
            if not room:
                raise HTTPException(status_code=409, detail="room_not_available")
            return {"ok": True, "room": await room_payload(room, bot_id, int(user["id"]))}

        @router.post("/{bot_id}/rooms/{room_id}/leave")
        async def leave(bot_id: int, room_id: str, request: Request):
            user, runtime = await auth(request, bot_id)
            ok = await runtime.ctx.services.room.leave_room(bot_id, int(user["id"]), room_id)
            if not ok:
                raise HTTPException(status_code=409, detail="cannot_leave")
            return {"ok": True}

        @router.post("/{bot_id}/rooms")
        async def create(bot_id: int, request: Request):
            user, runtime = await auth(request, bot_id)
            body = await request.json()
            settings = body.get("settings") or {}
            room = await runtime.ctx.services.room.create_room(
                bot_id,
                int(user["id"]),
                str(body.get("name", "Sala")),
                str(body.get("description", "")),
                str(body.get("visibility", "PUBLIC")),
                int(body.get("max_members", 100) or 100),
                settings=settings,
                duration_minutes=int(body.get("duration_minutes", 0) or 0),
            )
            return {"ok": True, "room": await room_payload(room, bot_id, int(user["id"]))}

        @router.patch("/{bot_id}/rooms/{room_id}")
        async def update_room(bot_id: int, room_id: str, request: Request):
            user, runtime = await auth(request, bot_id)
            await self._require_manage(bot_id, room_id, int(user["id"]))
            body = await request.json()
            allowed = {k: body[k] for k in ("name", "description", "visibility", "max_members", "public_listing") if k in body}
            if "settings" in body and isinstance(body["settings"], dict):
                allowed["settings"] = body["settings"]
            if "duration_minutes" in body:
                minutes = int(body["duration_minutes"] or 0)
                from app.bot.child_handlers import datetime_utc_plus
                allowed["expires_at"] = datetime_utc_plus(minutes) if minutes > 0 else None
            room = await self.platform.manager.repositories.room.update_room(bot_id, room_id, **allowed)
            return {"ok": True, "room": await room_payload(room, bot_id, int(user["id"]))}

        @router.post("/{bot_id}/rooms/{room_id}/actions/{action}")
        async def room_action(bot_id: int, room_id: str, action: str, request: Request):
            user, runtime = await auth(request, bot_id)
            await self._require_manage(bot_id, room_id, int(user["id"]))
            if action == "pause":
                ok = await self.platform.manager.repositories.room.pause(bot_id, room_id)
            elif action == "resume":
                ok = await self.platform.manager.repositories.room.resume(bot_id, room_id)
            elif action == "close":
                ok = await self.platform.manager.repositories.room.close(bot_id, room_id)
            else:
                raise HTTPException(status_code=400, detail="unsupported_action")
            return {"ok": ok}

        @router.get("/{bot_id}/rooms/{room_id}/members")
        async def members(bot_id: int, room_id: str, request: Request):
            user, runtime = await auth(request, bot_id)
            await self._require_manage(bot_id, room_id, int(user["id"]))
            docs = await self.platform.manager.repositories.room.list_members(bot_id, room_id, 100)
            result = []
            for member in docs:
                profile = await self.platform.manager.repositories.user.get(bot_id, int(member["user_id"])) or {}
                result.append({
                    "user_id": int(member["user_id"]),
                    "name": profile.get("first_name") or profile.get("username") or str(member["user_id"]),
                    "username": profile.get("username"),
                    "role": member.get("role", "MEMBER"),
                    "muted": bool(member.get("muted")),
                    "banned": bool(member.get("banned")),
                })
            return {"members": result}

        @router.post("/{bot_id}/rooms/{room_id}/members/{member_id}/actions/{action}")
        async def member_action(bot_id: int, room_id: str, member_id: int, action: str, request: Request):
            user, runtime = await auth(request, bot_id)
            await self._require_manage(bot_id, room_id, int(user["id"]))
            if action == "mute":
                member = await self.platform.manager.repositories.room.get_membership(bot_id, room_id, member_id)
                if not member:
                    raise HTTPException(status_code=404, detail="member_not_found")
                await self.platform.manager.repositories.room.update_member(bot_id, room_id, member_id, muted=not bool(member.get("muted")))
            elif action == "ban":
                member = await self.platform.manager.repositories.room.get_membership(bot_id, room_id, member_id)
                if not member:
                    raise HTTPException(status_code=404, detail="member_not_found")
                await self.platform.manager.repositories.room.update_member(bot_id, room_id, member_id, banned=not bool(member.get("banned")))
            elif action == "kick":
                await runtime.ctx.services.room.leave_room(bot_id, member_id, room_id)
            else:
                raise HTTPException(status_code=400, detail="unsupported_action")
            return {"ok": True}

        @router.get("/{bot_id}/profile")
        async def profile(bot_id: int, request: Request):
            user, runtime = await auth(request, bot_id)
            uid = int(user["id"])
            profile = await self.platform.manager.repositories.user.get(bot_id, uid) or {}
            rooms = await self.platform.manager.repositories.room.list_for_user(bot_id, uid, 100)
            return {
                "user": {
                    "id": uid,
                    "first_name": user.get("first_name", ""),
                    "username": user.get("username"),
                    "language_code": profile.get("language_code") or user.get("language_code") or "es",
                },
                "stats": {
                    "rooms": len(rooms),
                    "owned_rooms": sum(1 for r in rooms if int(r.get("owner_id", 0)) == uid),
                    "active_rooms": sum(1 for r in rooms if r.get("status") == "ACTIVE"),
                },
            }

        @router.post("/{bot_id}/profile/language")
        async def set_language(bot_id: int, request: Request):
            user, runtime = await auth(request, bot_id)
            body = await request.json()
            language = str(body.get("language", "es"))[:5].lower()
            if language not in {"es", "en", "pt"}:
                raise HTTPException(status_code=400, detail="unsupported_language")
            await self.platform.manager.repositories.user.upsert(bot_id, int(user["id"]), language_code=language, preferred_language=language)
            return {"ok": True, "language": language}

        return router

    async def _can_manage(self, bot_id: int, room_id: str, user_id: int) -> bool:
        if user_id in self.platform.settings.admin_ids:
            return True
        member = await self.platform.manager.repositories.room.get_membership(bot_id, room_id, user_id)
        return bool(member and member.get("role") in {"OWNER", "ADMIN"} and not member.get("banned"))

    async def _require_manage(self, bot_id: int, room_id: str, user_id: int) -> None:
        if not await self._can_manage(bot_id, room_id, user_id):
            raise HTTPException(status_code=403, detail="forbidden")
