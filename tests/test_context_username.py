from types import SimpleNamespace
from app.core.context import BotContext


def test_bot_context_username_comes_from_runtime_info():
    ctx = BotContext(
        bot_id=123, bot=object(), dispatcher=object(), db=None, settings=None,
        runtime=SimpleNamespace(info=SimpleNamespace(username="example_bot")),
        logger=None, services=None, repositories=None, config=None,
    )
    assert ctx.bot_username == "example_bot"
