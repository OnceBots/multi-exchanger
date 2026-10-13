from pathlib import Path
from types import SimpleNamespace

from aiogram.client.telegram import SimpleFilesPathWrapper, TelegramAPIServer

from app.bot.api_client import build_api_server


def test_local_api_server_uses_local_mode_and_path_wrapper(tmp_path):
    server_root = tmp_path / "server"
    host_root = tmp_path / "host"
    host_root.mkdir(parents=True, exist_ok=True)
    (host_root / "video.mp4").write_bytes(b"video")
    settings = SimpleNamespace(
        telegram_api_base_url="http://127.0.0.1:8081",
        telegram_api_local_mode=True,
        telegram_api_server_files_root=str(server_root),
        telegram_api_host_files_root=str(host_root),
    )
    api = build_api_server(settings)
    assert isinstance(api, TelegramAPIServer)
    assert api.base == "http://127.0.0.1:8081/bot{token}/{method}"
    assert api.is_local is True
    assert isinstance(api.wrap_local_file, SimpleFilesPathWrapper)
    assert api.wrap_local_file.to_local(Path(server_root / "video.mp4")) == (host_root / "video.mp4").resolve()


def test_non_local_api_mode_does_not_enable_local_files(tmp_path):
    settings = SimpleNamespace(
        telegram_api_base_url="http://127.0.0.1:8081",
        telegram_api_local_mode=False,
        telegram_api_server_files_root=str(tmp_path),
        telegram_api_host_files_root=str(tmp_path),
    )
    api = build_api_server(settings)
    assert api.is_local is False
