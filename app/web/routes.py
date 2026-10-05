from __future__ import annotations

from fastapi import APIRouter, Request
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
        master_ok = platform.manager.master_bot is not None
        return {"status": "ok" if mongo_ok and master_ok else "degraded", "mongo": mongo_ok, "master": master_ok, "bots_running": sum(1 for r in platform.manager.registry.values() if str(r.status) == "RUNNING")}

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
