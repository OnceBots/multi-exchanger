from __future__ import annotations

import html
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.services.webapp_auth import authenticate_webapp_request


class ReportBody(BaseModel):
    contribution_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=2, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class SanctionBody(BaseModel):
    user_id: int = Field(gt=0)
    kind: str = Field(min_length=2, max_length=40)
    reason: str = Field(min_length=2, max_length=250)
    minutes: int | None = Field(default=None, ge=1, le=43200)


def build_contribution_web_router(platform) -> APIRouter:
    router = APIRouter()
    static_path = Path(__file__).resolve().parents[2] / "web" / "static" / "contribution.html"

    def runtime_for(bot_id: int):
        runtime = platform.manager.registry.get(bot_id)
        if not runtime or not runtime.ctx:
            raise HTTPException(status_code=404, detail="Bot no disponible")
        if runtime.status.value != "RUNNING":
            raise HTTPException(status_code=503, detail="Bot no está disponible temporalmente")
        return runtime

    def auth(request: Request, runtime):
        return authenticate_webapp_request(
            request,
            bot_id=runtime.ctx.bot_id,
            bot_token=runtime.bot.token,
            launch_secret=platform.settings.webhook_secret,
            max_age_seconds=platform.settings.webapp_auth_max_age_seconds,
        )

    def moderator(runtime, user_id: int) -> bool:
        return user_id == runtime.ctx.owner_id or user_id in set(runtime.ctx.settings.admin_ids or [])

    @router.get("/contribution-app", response_class=HTMLResponse)
    async def contribution_app():
        return HTMLResponse(static_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @router.get("/api/child/contribution/profile")
    async def contribution_profile(request: Request, bot_id: int):
        runtime = runtime_for(bot_id)
        user, source = auth(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        profile = runtime.ctx.services.contribution.decorate_profile(await runtime.ctx.services.contribution.profile(bot_id, int(user["id"])))
        return {"ok": True, "auth_source": source, "profile": profile}

    @router.get("/api/child/contribution/leaderboard")
    async def contribution_leaderboard(request: Request, bot_id: int):
        runtime = runtime_for(bot_id)
        user, source = auth(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        return {"ok": True, "auth_source": source, "leaderboard": await runtime.ctx.services.contribution.leaderboard(bot_id, 20)}

    @router.post("/api/child/contribution/report")
    async def contribution_report(request: Request, bot_id: int, body: ReportBody):
        runtime = runtime_for(bot_id)
        user, _ = auth(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        result = await runtime.ctx.services.contribution.report(bot_id, int(user["id"]), body.contribution_id, body.reason, body.note)
        return {"ok": True, "report": result}

    @router.get("/api/child/moderation")
    async def moderation_dashboard(request: Request, bot_id: int):
        runtime = runtime_for(bot_id)
        user, _ = auth(request, runtime)
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Mini App no autenticada")
        uid = int(user["id"])
        if not moderator(runtime, uid):
            raise HTTPException(status_code=403, detail="No autorizado")
        return {"ok": True, **await runtime.ctx.services.contribution.moderation_snapshot(bot_id, 30)}

    @router.post("/api/child/moderation/contributions/{contribution_id}/approve")
    async def approve(request: Request, bot_id: int, contribution_id: str):
        runtime = runtime_for(bot_id)
        user, _ = auth(request, runtime)
        if not user or not user.get("id") or not moderator(runtime, int(user["id"])):
            raise HTTPException(status_code=403, detail="No autorizado")
        result = await runtime.ctx.services.contribution.approve_contribution(bot_id, contribution_id, int(user["id"]))
        if not result:
            raise HTTPException(status_code=409, detail="Aporte inexistente o ya procesado")
        return {"ok": True, "item": result}

    @router.post("/api/child/moderation/contributions/{contribution_id}/reject")
    async def reject(request: Request, bot_id: int, contribution_id: str):
        runtime = runtime_for(bot_id)
        user, _ = auth(request, runtime)
        if not user or not user.get("id") or not moderator(runtime, int(user["id"])):
            raise HTTPException(status_code=403, detail="No autorizado")
        result = await runtime.ctx.services.contribution.reject_contribution(bot_id, contribution_id, int(user["id"]), reason="Rechazado desde moderación")
        if not result:
            raise HTTPException(status_code=409, detail="Aporte inexistente o ya procesado")
        return {"ok": True, "item": result}


    @router.post("/api/child/moderation/reports/{report_id}/resolve")
    async def resolve_report(request: Request, bot_id: int, report_id: str):
        runtime = runtime_for(bot_id)
        user, _ = auth(request, runtime)
        if not user or not user.get("id") or not moderator(runtime, int(user["id"])):
            raise HTTPException(status_code=403, detail="No autorizado")
        result = await runtime.ctx.services.contribution.repo.resolve_report(
            bot_id, report_id, int(user["id"]), "Resuelto por moderación"
        )
        if not result:
            raise HTTPException(status_code=409, detail="Reporte inexistente o ya resuelto")
        await runtime.ctx.services.contribution.repo.log_action(
            bot_id=bot_id, moderator_id=int(user["id"]), action="REPORT_RESOLVED", target_id=report_id
        )
        return {"ok": True, "report": result}

    @router.post("/api/child/moderation/sanction")
    async def sanction(request: Request, bot_id: int, body: SanctionBody):
        runtime = runtime_for(bot_id)
        user, _ = auth(request, runtime)
        if not user or not user.get("id") or not moderator(runtime, int(user["id"])):
            raise HTTPException(status_code=403, detail="No autorizado")
        result = await runtime.ctx.services.contribution.sanction(bot_id, body.user_id, int(user["id"]), body.kind, body.reason, body.minutes)
        return {"ok": True, "sanction": result}

    return router
