from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Request


async def parse_payload(request: Request) -> dict:
    return await request.json()


def build_webhook_router(platform) -> APIRouter:
    router = APIRouter()
    logger = logging.getLogger("webhook")

    def _log_task_result(task: asyncio.Task, *, scope: str) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("webhook_task_failed scope=%s", scope)

    @router.post("/telegram/webhook/master")
    async def master_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        if x_telegram_bot_api_secret_token != platform.settings.webhook_secret:
            raise HTTPException(status_code=403, detail="forbidden")
        payload = await parse_payload(request)
        task = asyncio.create_task(
            platform.manager.master_dp.feed_raw_update(platform.manager.master_bot, payload),
            name=f"master-update-{payload.get('update_id', 'unknown')}",
        )
        task.add_done_callback(lambda done: _log_task_result(done, scope="master"))
        return {"ok": True}

    @router.post("/telegram/webhook/{bot_id}")
    async def child_webhook(bot_id: int, request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        doc = await platform.manager.repositories.bots.get(bot_id)
        if not doc or not doc.get("enabled", True):
            raise HTTPException(status_code=404, detail="bot_not_found")
        secret = platform.manager.token_service.decrypt(doc["webhook_secret_encrypted"])
        if x_telegram_bot_api_secret_token != secret:
            raise HTTPException(status_code=403, detail="forbidden")
        payload = await parse_payload(request)
        update_id = payload.get("update_id")
        if update_id is not None:
            accepted = await platform.manager.repositories.media.register_update(bot_id, int(update_id), payload)
            if not accepted:
                return {"ok": True, "duplicate": True}
        task = asyncio.create_task(
            platform.manager.handle_webhook_update(bot_id, payload),
            name=f"child-{bot_id}-update-{payload.get('update_id', 'unknown')}",
        )
        task.add_done_callback(lambda done: _log_task_result(done, scope=f"child:{bot_id}"))
        return {"ok": True}

    return router
