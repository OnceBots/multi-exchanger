from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import PlainTextResponse


def build_router(platform) -> APIRouter:
    router = APIRouter()

    @router.api_route("/", methods=["GET", "HEAD"])
    async def root():
        return {"service": platform.settings.service_name, "status": "ok", "environment": platform.settings.environment}

    @router.get(platform.settings.health_path)
    async def health():
        return {"status": "ok", "service": platform.settings.service_name, "environment": platform.settings.environment}

    @router.get(platform.settings.ready_path)
    async def ready():
        mongo_ok = await platform.mongo.ping()
        manager_ok = platform.manager is not None
        master_ok = bool(manager_ok and platform.manager.master_bot is not None)
        platform_ready = bool(platform.ready)
        ok = mongo_ok and master_ok and platform_ready
        payload = {
            "status": "ok" if ok else "starting",
            "mongo": mongo_ok,
            "master": master_ok,
            "platform_ready": platform_ready,
            "bots_running": sum(1 for r in (platform.manager.registry.values() if platform.manager else []) if str(r.status) == "RUNNING"),
        }
        from fastapi.responses import JSONResponse
        return JSONResponse(payload, status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE)

    @router.get(platform.settings.metrics_path, response_class=PlainTextResponse)
    async def metrics():
        if not platform.settings.enable_metrics:
            return "metrics_disabled\n"
        lines = [f"bots_total {len(platform.manager.registry)}", f"bots_running {sum(1 for r in platform.manager.registry.values() if str(r.status) == 'RUNNING')}"]
        for bot_id, runtime in platform.manager.registry.items():
            for key, value in runtime.metrics.snapshot().items():
                lines.append(f"bot_{bot_id}_{key} {value}")
        return "\n".join(lines) + "\n"

    return router
