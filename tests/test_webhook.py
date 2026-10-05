from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.webhook import build_webhook_router


class FakeFeed:
    def __init__(self):
        self.feed_raw_update = AsyncMock()


class FakeMedia:
    async def register_update(self, bot_id, update_id, payload):
        return True

    async def mark_update(self, *args, **kwargs):
        return None


class FakeRepos:
    def __init__(self):
        self.media = FakeMedia()
        self.bots = self

    async def get(self, bot_id):
        return {"bot_id": bot_id, "enabled": True, "webhook_secret_encrypted": "encrypted"}


class FakeTokenService:
    def decrypt(self, value):
        return "child-secret"


class FakePlatform:
    def __init__(self):
        self.settings = SimpleNamespace(webhook_secret="master-secret")
        self.ready = True
        self.manager = SimpleNamespace(
            master_dp=FakeFeed(),
            master_bot=object(),
            repositories=FakeRepos(),
            token_service=FakeTokenService(),
            handle_webhook_update=AsyncMock(),
            handle_master_webhook_update=AsyncMock(),
        )


def test_master_webhook_dispatches():
    platform = FakePlatform()
    app = FastAPI()
    app.include_router(build_webhook_router(platform))
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook/master",
            headers={"X-Telegram-Bot-Api-Secret-Token": "master-secret"},
            json={"update_id": 123, "message": {"message_id": 1}},
        )
    assert response.status_code == 200
    assert platform.manager.handle_master_webhook_update.await_count == 1


def test_master_webhook_rejects_wrong_secret():
    platform = FakePlatform()
    app = FastAPI()
    app.include_router(build_webhook_router(platform))
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook/master",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 123},
        )
    assert response.status_code == 403


def test_child_webhook_dispatches_and_persists():
    platform = FakePlatform()
    app = FastAPI()
    app.include_router(build_webhook_router(platform))
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook/123",
            headers={"X-Telegram-Bot-Api-Secret-Token": "child-secret"},
            json={"update_id": 456, "message": {"message_id": 2}},
        )
    assert response.status_code == 200
    platform.manager.handle_webhook_update.assert_awaited_once()


def test_child_webhook_rejects_wrong_secret():
    platform = FakePlatform()
    app = FastAPI()
    app.include_router(build_webhook_router(platform))
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook/123",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 456},
        )
    assert response.status_code == 403


def test_webhook_returns_503_while_platform_is_starting():
    platform = FakePlatform()
    platform.ready = False
    app = FastAPI()
    app.include_router(build_webhook_router(platform))
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook/master",
            headers={"X-Telegram-Bot-Api-Secret-Token": "master-secret"},
            json={"update_id": 999},
        )
    assert response.status_code == 503
