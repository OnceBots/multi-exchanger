from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Request


async def parse_payload(request: Request) -> dict:
    return await request.json()


def build_webhook_router(platform) -> APIRouter:
    router = APIRouter()
    logger = logging.getLogger("webhook")

    def _schedule(coro, *, bot_id: int | None, update_id: int | None, kind: str) -> None:
        task = asyncio.create_task(coro, name=f"webhook:{kind}:{bot_id or 'master'}:{update_id or 'na'}")

        def done(completed: asyncio.Task) -> None:
            if completed.cancelled():
                return
            try:
                exc = completed.exception()
            except asyncio.CancelledError:
                return
            if exc is not None:
                logger.exception(
                    "webhook_dispatch_failed kind=%s bot_id=%s update_id=%s error=%s",
                    kind, bot_id, update_id, exc, exc_info=exc,
                )

        task.add_done_callback(done)

    @router.post("/telegram/webhook/master")
    async def master_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        expected = platform.settings.webhook_secret
        if x_telegram_bot_api_secret_token != expected:
            logger.warning("master_webhook_forbidden")
            raise HTTPException(status_code=403, detail="forbidden")
        payload = await parse_payload(request)
        update_id = payload.get("update_id")
        logger.info(
            "master_webhook_received update_id=%s kind=%s",
            update_id,
            next((key for key in payload.keys() if key != "update_id"), "unknown"),
        )
        _schedule(
            platform.manager.master_dp.feed_raw_update(platform.manager.master_bot, payload),
            bot_id=None, update_id=int(update_id) if update_id is not None else None, kind="master",
        )
        return {"ok": True}

    @router.post("/telegram/webhook/{bot_id}")
    async def child_webhook(bot_id: int, request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        doc = await platform.manager.repositories.bots.get(bot_id)
        if not doc or not doc.get("enabled", True):
            logger.warning("child_webhook_not_found bot_id=%s", bot_id)
            raise HTTPException(status_code=404, detail="bot_not_found")
        secret = platform.manager.token_service.decrypt(doc["webhook_secret_encrypted"])
        if x_telegram_bot_api_secret_token != secret:
            logger.warning("child_webhook_forbidden bot_id=%s", bot_id)
            raise HTTPException(status_code=403, detail="forbidden")
        payload = await parse_payload(request)
        update_id = payload.get("update_id")
        if update_id is not None:
            accepted = await platform.manager.repositories.media.register_update(bot_id, int(update_id), payload)
            if not accepted:
                logger.info("child_webhook_duplicate bot_id=%s update_id=%s", bot_id, update_id)
                return {"ok": True, "duplicate": True}
        logger.info(
            "child_webhook_received bot_id=%s update_id=%s kind=%s",
            bot_id, update_id, next((key for key in payload.keys() if key != "update_id"), "unknown"),
        )
        _schedule(
            platform.manager.handle_webhook_update(bot_id, payload),
            bot_id=bot_id, update_id=int(update_id) if update_id is not None else None, kind="child",
        )
        return {"ok": True}

    return router
