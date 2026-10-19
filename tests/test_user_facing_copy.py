from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_VISIBLE_FILES = (
    "app/bot/child_handlers.py",
    "app/services/room_package_service.py",
    "app/webapp/index.html",
    "app/webapp/static/app.js",
    "app/web/static/child.html",
)


def test_user_facing_bot_copy_never_mentions_hosting_provider():
    for relative in USER_VISIBLE_FILES:
        content = (PROJECT_ROOT / relative).read_text(encoding="utf-8").casefold()
        assert "vultr" not in content, f"Unexpected provider reference in user-facing file: {relative}"


def test_user_facing_copy_explains_shared_zip_not_private_zip():
    child = (PROJECT_ROOT / "app/bot/child_handlers.py").read_text(encoding="utf-8").casefold()
    miniapp = (PROJECT_ROOT / "app/web/static/child.html").read_text(encoding="utf-8").casefold()
    assert "zip compartido" in child
    assert "contenido de otros miembros" in child or "archivos de otros miembros" in child
    assert "zip compartido" in miniapp
