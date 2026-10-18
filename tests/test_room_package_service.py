from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.services.room_package_service import RoomPackageService, format_bytes, progress_bar, safe_archive_filename


class UnusedRepository:
    async def get_item(self, *args, **kwargs):
        raise AssertionError("the missing-buffer guard must run before Mongo access")


def build_service(tmp_path: Path, *, bot_id: int = 12345, storage_channel_id: str = "-100111", host_root: Path | None = None):
    host_root = host_root or (tmp_path / "bot-api-data")
    host_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(
        media_package_dir=str(tmp_path / "packages"),
        media_package_max_bytes=1_800 * 1024 * 1024,
        media_package_idle_seconds=60,
        room_queue_maxsize=100,
        token_encryption_key=Fernet.generate_key().decode("ascii"),
        storage_channel_id=storage_channel_id,
        telegram_api_local_mode=True,
        telegram_api_host_files_root=str(host_root),
        telegram_api_server_files_root="/var/lib/telegram-bot-api",
        admin_ids=frozenset({777}),
    )
    ctx = SimpleNamespace(
        bot_id=bot_id,
        bot=SimpleNamespace(token=f"{bot_id}:CURRENT_TOKEN"),
        settings=settings,
        repositories=SimpleNamespace(packages=UnusedRepository()),
    )
    return RoomPackageService(ctx)


def test_media_enqueue_refuses_to_accept_when_private_buffer_is_not_configured(tmp_path):
    service = build_service(tmp_path, storage_channel_id="")
    message = SimpleNamespace(
        chat=SimpleNamespace(id=123456, type="private"),
        message_id=88,
        from_user=SimpleNamespace(id=444),
    )

    accepted, item_id, reason = __import__("asyncio").run(
        service.enqueue(message, "room-1", "video", "file-id", 1024, "clip.mp4")
    )

    assert accepted is False
    assert item_id is None
    assert reason and "No se borró el mensaje original" in reason


def test_local_bot_api_path_maps_under_same_bot_old_token_directory(tmp_path):
    host_root = tmp_path / "bot-api-data"
    old_dir = host_root / "12345:OLD_TOKEN_VALUE"
    video = old_dir / "videos" / "file_6.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"sample-video")
    service = build_service(tmp_path, bot_id=12345, host_root=host_root)

    resolved = service._resolve_local_api_file("/var/lib/telegram-bot-api/12345:OLD_TOKEN_VALUE/videos/file_6.mp4")

    assert resolved == video.resolve()
    assert resolved.read_bytes() == b"sample-video"


def test_local_bot_api_path_refuses_an_explicit_other_bot_directory(tmp_path):
    host_root = tmp_path / "bot-api-data"
    for dirname in ("12345:CURRENT", "67890:OTHER"):
        video = host_root / dirname / "videos" / "file_6.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(dirname.encode())
    service = build_service(tmp_path, bot_id=12345, host_root=host_root)

    with pytest.raises(FileNotFoundError, match="otro bot"):
        service._resolve_local_api_file("/var/lib/telegram-bot-api/67890:OTHER/videos/file_6.mp4")


def test_status_shows_progress_and_idle_timer(tmp_path):
    service = build_service(tmp_path)
    from app.core.datetime import utcnow

    text = service._status_text({
        "state": "OPEN",
        "room_id": "room-1",
        "created_at": utcnow(),
        "last_item_at": utcnow(),
        "item_count": 3,
        "staged_count": 2,
        "pending_count": 1,
        "failed_count": 0,
        "reserved_bytes": 10 * 1024 * 1024,
        "staged_bytes": 7 * 1024 * 1024,
    })

    assert "Paquete multimedia" in text
    assert "Acumulado" in text
    assert "Falta para completar" in text
    assert "pendientes" in text
    assert "temporizador de inactividad espera" in text


def test_filename_and_progress_helpers_are_bounded():
    assert safe_archive_filename("../../bad:name.mp4", "fallback") == "bad_name.mp4"
    assert len(safe_archive_filename("x" * 300, "fallback")) == 180
    assert len(progress_bar(1, 100)) == 10
    assert "MiB" in format_bytes(1024 * 1024)


def test_aes256_zip_contains_original_file_bytes(tmp_path):
    pyzipper = pytest.importorskip("pyzipper")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"sample media bytes" * 50)
    archive_path = tmp_path / "package.zip"
    password = "secret-password-123"

    RoomPackageService._build_encrypted_zip(
        archive_path,
        [{"staged_path": str(source), "filename": "clip.mp4"}],
        password,
    )

    assert archive_path.is_file()
    with pyzipper.AESZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        assert names == ["00001_clip.mp4"]
        archive.setpassword(password.encode("utf-8"))
        assert archive.read(names[0]) == source.read_bytes()
        assert archive.getinfo(names[0]).wz_aes_strength == 3


@pytest.mark.asyncio
async def test_every_admin_id_receives_room_package_independent_of_feed_preference(tmp_path):
    service = build_service(tmp_path)

    class RoomRepo:
        async def members_for_room(self, bot_id, room_id):
            return [{"user_id": 11}, {"user_id": 12}]

    class AdminFeed:
        async def enabled_admins(self):
            # Feed preferences are separate from package delivery.
            return [999]

    service.ctx.repositories.room = RoomRepo()
    service.ctx.services = SimpleNamespace(admin_feed=AdminFeed())

    targets = await service._recipient_ids({"room_id": "room-1", "package_id": "p1"})

    assert targets == [11, 12, 777, 999]

@pytest.mark.asyncio
async def test_download_failure_is_recorded_without_masking_error_or_deleting_original(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    item = {
        "bot_id": 12345,
        "item_id": "item-failure-1",
        "package_id": "package-failure-1",
        "room_id": "room-1",
        "sender_id": 444,
        "original_chat_id": 444,
        "original_message_id": 88,
        "file_id": "file-id",
        "filename": "clip.mp4",
        "media_type": "video",
        "expected_size": 100,
        "status": "QUEUED",
    }

    class FakeRepository:
        def __init__(self):
            self.item = dict(item)
            self.failed_error = None

        async def get_item(self, bot_id, item_id):
            return dict(self.item) if item_id == self.item["item_id"] else None

        async def mark_downloading(self, bot_id, item_id):
            self.item["status"] = "DOWNLOADING"

        async def mark_item_failed(self, bot_id, current, error):
            self.item["status"] = "FAILED"
            self.failed_error = error

    repo = FakeRepository()
    service.repo = repo
    service.ctx.bot.send_message = _async_noop
    deleted_messages = []

    async def delete_message(**kwargs):
        deleted_messages.append(kwargs)

    service.ctx.bot.delete_message = delete_message

    async def fail_download(*args, **kwargs):
        raise FileNotFoundError("test path failure")

    async def no_progress(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_download_to_local", fail_download)
    monkeypatch.setattr(service, "schedule_progress_refresh", no_progress)

    await service._process_item("item-failure-1")

    assert repo.item["status"] == "FAILED"
    assert repo.failed_error == "test path failure"
    assert deleted_messages == []


async def _async_noop(*args, **kwargs):
    return None
