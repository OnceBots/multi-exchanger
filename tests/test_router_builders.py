from fastapi import APIRouter

from app.web.routes import build_router
from app.web.webapp import build_webapp_router
from app.web.webhook import build_webhook_router


class _Settings:
    service_name = "test"
    health_path = "/health"
    ready_path = "/ready"
    metrics_path = "/metrics"
    master_bot_token = "x"
    webapp_auth_max_age_seconds = 60


class _Platform:
    settings = _Settings()


def test_all_router_builders_return_api_router():
    platform = _Platform()
    assert isinstance(build_router(platform), APIRouter)
    assert isinstance(build_webapp_router(platform), APIRouter)
    assert isinstance(build_webhook_router(platform), APIRouter)


def test_child_callback_answer_is_not_recursive():
    from pathlib import Path
    source = Path("app/bot/child_handlers.py").read_text(encoding="utf-8")
    assert "await callback.answer(*args, **kwargs)" in source
    assert "await _safe_callback_answer(callback, *args, **kwargs)" not in source
