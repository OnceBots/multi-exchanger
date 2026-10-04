import asyncio
from types import SimpleNamespace
from app.bot.runtime import BotRuntime
from app.core.enums import BotStatus


class DummySession:
    async def close(self):
        pass


class DummyBot:
    session = DummySession()


class DummyDP:
    pass


def test_runtime_status():
    info = SimpleNamespace(bot_id=1)
    runtime = BotRuntime(info, DummyBot(), DummyDP())
    assert runtime.status == BotStatus.CREATED
    asyncio.run(runtime.stop())
    assert runtime.status == BotStatus.STOPPED
