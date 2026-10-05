from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse



def build_webhook_router(platform) -> APIRouter:
    router = APIRouter()
    logger = logging.getLogger("telegram.webhook")

    async def payload(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_json") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="invalid_payload")
        return data

    def valid_secret(received: str | None, expected: str) -> bool:
        return bool(received and expected and received == expected)

    @router.post("/telegram/webhook/master")
    async def master_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        expected = platform.settings.webhook_secret
        if not valid_secret(x_telegram_bot_api_secret_token, expected):
            logger.warning("master_webhook_forbidden")
            raise HTTPException(status_code=403, detail="forbidden")
        data = await payload(request)
        update_id = data.get("update_id")
        logger.info("master_webhook_received update_id=%s keys=%s", update_id, sorted(data.keys()))
        if not platform.ready or not platform.manager:
            logger.warning("master_webhook_not_ready update_id=%s", update_id)
            raise HTTPException(status_code=503, detail="platform_not_ready")
        try:
            await platform.manager.handle_master_webhook_update(data)
        except Exception:
            logger.exception("master_webhook_dispatch_failed update_id=%s", update_id)
            raise HTTPException(status_code=500, detail="dispatch_failed")
        logger.info("master_webhook_processed update_id=%s", update_id)
        return JSONResponse({"ok": True})

    @router.post("/telegram/webhook/{bot_id}")
    async def child_webhook(bot_id: int, request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
        if not platform.ready or not platform.manager:
            logger.warning("child_webhook_not_ready bot_id=%s", bot_id)
            raise HTTPException(status_code=503, detail="platform_not_ready")
        doc = await platform.manager.repositories.bots.get(bot_id)
        if not doc or not doc.get("enabled", True):
            logger.warning("child_webhook_not_found bot_id=%s", bot_id)
            raise HTTPException(status_code=404, detail="bot_not_found")
        try:
            expected = platform.manager.token_service.decrypt(doc["webhook_secret_encrypted"])
        except Exception:
            logger.exception("child_webhook_secret_decrypt_failed bot_id=%s", bot_id)
            raise HTTPException(status_code=500, detail="secret_error")
        if not valid_secret(x_telegram_bot_api_secret_token, expected):
            logger.warning("child_webhook_forbidden bot_id=%s", bot_id)
            raise HTTPException(status_code=403, detail="forbidden")
        data = await payload(request)
        update_id = data.get("update_id")
        logger.info("child_webhook_received bot_id=%s update_id=%s keys=%s", bot_id, update_id, sorted(data.keys()))
        if update_id is not None:
            accepted = await platform.manager.repositories.media.register_update(bot_id, int(update_id), data)
            if not accepted:
                logger.info("child_webhook_duplicate bot_id=%s update_id=%s", bot_id, update_id)
                return {"ok": True, "duplicate": True}
        try:
            await platform.manager.handle_webhook_update(bot_id, data)
        except Exception:
            logger.exception("child_webhook_dispatch_failed bot_id=%s update_id=%s", bot_id, update_id)
            raise HTTPException(status_code=500, detail="dispatch_failed")
        logger.info("child_webhook_processed bot_id=%s update_id=%s", bot_id, update_id)
        return JSONResponse({"ok": True})

    return router
