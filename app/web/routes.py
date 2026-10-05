from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse


def build_router(platform) -> APIRouter:
    router = APIRouter()

    @router.api_route("/", methods=["GET", "HEAD"])
    async def root():
        return {"service": platform.settings.service_name, "status": "ok"}

    @router.get(platform.settings.health_path)
    async def health():
        return {"status": "ok", "service": platform.settings.service_name, "platform_ready": bool(platform.ready)}

    @router.get(platform.settings.ready_path)
    async def ready():
        mongo_ok = await platform.mongo.ping()
        ready = bool(mongo_ok and platform.ready and platform.manager and platform.manager.master_bot)
        if not ready:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail={"mongo": mongo_ok, "platform_ready": bool(platform.ready)})
        health = await platform.manager.get_health()
        return {"status": "ok", "mongo": True, **health}

    @router.get(platform.settings.metrics_path, response_class=PlainTextResponse)
    async def metrics():
        health = await platform.manager.get_health()
        lines = [
            f"platform_ready {int(health['ready'])}",
            f"bots_total {health['children']}",
            f"bots_running {health['children_running']}",
            f"bots_error {health['children_error']}",
        ]
        return "\n".join(lines) + "\n"

    return router
