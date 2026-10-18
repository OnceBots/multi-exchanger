from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.local_file_paths import map_api_file_path

try:
    from app.bot.api_client import build_api_server
except ModuleNotFoundError as exc:
    if exc.name != "aiogram":
        raise
    build_api_server = None


def make_settings(server_root: Path, host_root: Path, *, local=True):
    return SimpleNamespace(
        telegram_api_base_url="http://127.0.0.1:8081",
        telegram_api_local_mode=local,
        telegram_api_server_files_root=str(server_root),
        telegram_api_host_files_root=str(host_root),
    )


def make_file(path: Path, body: bytes = b"image") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path.resolve()


def test_relative_get_file_path_maps_under_bot_id_directory(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / "123456" / "photos" / "file_1.jpg")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(settings, "photos/file_1.jpg", bot_token=token) == expected


def test_absolute_container_path_maps_to_host_volume(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / "123456" / "photos" / "file_1.jpg")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(
        settings,
        server_root / "123456" / "photos" / "file_1.jpg",
        bot_token=token,
    ) == expected


def test_absolute_container_path_uses_numeric_bot_id_folder(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / "123456" / "videos" / "file_1.mp4", b"video")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(
        settings,
        server_root / "123456" / "videos" / "file_1.mp4",
        bot_token=token,
    ) == expected


def test_absolute_host_path_is_accepted_for_same_bot(tmp_path):
    server_root = tmp_path / "server"
    host_root = tmp_path / "host"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / "123456" / "photos" / "file_1.jpg")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(settings, expected, bot_token=token) == expected


def test_relative_path_already_prefixed_with_bot_id_is_not_double_prefixed(tmp_path):
    server_root = tmp_path / "server"
    host_root = tmp_path / "host"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / "123456" / "photos" / "file_1.jpg")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(
        settings, "123456/photos/file_1.jpg", bot_token=token
    ) == expected


def test_rejects_path_traversal(tmp_path):
    settings = make_settings(tmp_path / "server", tmp_path / "host")
    with pytest.raises(ValueError, match="segura"):
        map_api_file_path(settings, "../outside.jpg", bot_token="123456:TEST_TOKEN")


def test_does_not_resolve_another_bots_directory(tmp_path):
    server_root = tmp_path / "server"
    host_root = tmp_path / "host"
    token_a = "111:BOT_A"
    token_b = "222:BOT_B"
    make_file(host_root / "222" / "photos" / "file_1.jpg")
    settings = make_settings(server_root, host_root)

    with pytest.raises(FileNotFoundError):
        map_api_file_path(settings, "222/photos/file_1.jpg", bot_token=token_a)


def test_requires_token_for_relative_paths(tmp_path):
    settings = make_settings(tmp_path / "server", tmp_path / "host")
    with pytest.raises(ValueError, match="sin el token"):
        map_api_file_path(settings, "photos/file_1.jpg")


def test_non_local_mode_cannot_map_local_path(tmp_path):
    settings = make_settings(tmp_path / "server", tmp_path / "host", local=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_API_LOCAL_MODE"):
        map_api_file_path(settings, "photos/file_1.jpg", bot_token="123456:TEST_TOKEN")


def test_local_api_wrapper_uses_per_bot_mapping(tmp_path):
    if build_api_server is None:
        pytest.skip("Aiogram is not installed in the packaging environment")
    server_root = tmp_path / "server"
    host_root = tmp_path / "host"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / "123456" / "photos" / "file_1.jpg")
    settings = make_settings(server_root, host_root)

    api = build_api_server(settings, token=token)
    assert api.is_local is True
    assert Path(api.wrap_local_file.to_local("photos/file_1.jpg")) == expected


def test_absolute_container_path_maps_full_token_directory(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / token / "videos" / "file_6.mp4", b"video")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(
        settings,
        server_root / token / "videos" / "file_6.mp4",
        bot_token=token,
    ) == expected


def test_relative_get_file_path_maps_full_token_directory(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    token = "123456:TEST_TOKEN"
    expected = make_file(host_root / token / "videos" / "file_6.mp4", b"video")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(settings, "videos/file_6.mp4", bot_token=token) == expected

def test_absolute_path_accepts_rotated_token_directory_for_same_bot(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    active_token = "123456:NEW_TOKEN"
    stored_token = "123456:OLD_TOKEN"
    expected = make_file(host_root / stored_token / "videos" / "file_6.mp4", b"video")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(
        settings,
        server_root / stored_token / "videos" / "file_6.mp4",
        bot_token=active_token,
    ) == expected


def test_relative_path_searches_unique_same_bot_directory_after_token_rotation(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    active_token = "123456:NEW_TOKEN"
    stored_token = "123456:OLD_TOKEN"
    expected = make_file(host_root / stored_token / "videos" / "file_6.mp4", b"video")
    settings = make_settings(server_root, host_root)

    assert map_api_file_path(
        settings, "videos/file_6.mp4", bot_token=active_token
    ) == expected


def test_absolute_host_style_path_maps_by_bot_directory_without_exposing_root(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    active_token = "123456:NEW_TOKEN"
    stored_token = "123456:OLD_TOKEN"
    expected = make_file(host_root / stored_token / "videos" / "file_6.mp4", b"video")
    settings = make_settings(server_root, host_root)
    reported = tmp_path / "old-host-root" / "data" / stored_token / "videos" / "file_6.mp4"

    assert map_api_file_path(settings, reported, bot_token=active_token) == expected


def test_rejects_ambiguous_relative_file_across_same_bot_token_directories(tmp_path):
    server_root = tmp_path / "container" / "var" / "lib" / "telegram-bot-api"
    host_root = tmp_path / "host" / "tg-bot-api" / "data"
    token = "123456:ACTIVE_TOKEN"
    make_file(host_root / "123456:OLD_TOKEN" / "videos" / "file_6.mp4", b"old")
    make_file(host_root / token / "videos" / "file_6.mp4", b"new")
    settings = make_settings(server_root, host_root)

    with pytest.raises(FileNotFoundError, match="varios archivos"):
        map_api_file_path(settings, "videos/file_6.mp4", bot_token=token)

