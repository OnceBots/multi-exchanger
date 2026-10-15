from types import SimpleNamespace

from app.bot.runtime import BotRuntime


def test_bot_context_username_comes_from_runtime_info():
    info = SimpleNamespace(bot_id=123, username="example_bot", owner_id=456)
    runtime = BotRuntime(info=info, bot=object(), dispatcher=object())

    ctx = runtime.build_context(db=None, settings=None, repositories=None, services=None)

    assert ctx.bot_username == "example_bot"
