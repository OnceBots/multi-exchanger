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

    targets = await service._recipient_ids({"room_id": "room-1", "package_id": "p1", "sender_id": 11})

    assert targets == [11, 777, 999]

@pytest.mark.asyncio
async def test_new_packages_send_only_to_owner_and_admins(tmp_path):
    service = build_service(tmp_path)

    class RoomRepo:
        async def members_for_room(self, bot_id, room_id):
            return [{"user_id": 11}, {"user_id": 12}, {"user_id": 13}]

    class AdminFeed:
        async def enabled_admins(self):
            return []

    service.ctx.repositories.room = RoomRepo()
    service.ctx.services = SimpleNamespace(admin_feed=AdminFeed())
    assert await service._recipient_ids({"room_id": "room-1", "package_id": "p1", "sender_id": 12}) == [12, 777]


def test_status_text_explains_user_hourly_video_quota(tmp_path):
    service = build_service(tmp_path)
    from app.core.datetime import utcnow
    text = service._status_text({
        "state": "OPEN", "room_id": "room-1", "sender_id": 444,
        "created_at": utcnow(), "last_item_at": utcnow(), "item_count": 2,
        "staged_count": 2, "pending_count": 0, "failed_count": 0,
        "duplicate_count": 1, "reserved_bytes": 100, "staged_bytes": 100,
        "recent_video_count": 7,
    })
    assert "7/10" in text
    assert "faltan" in text.lower()
    assert "duplicados" in text.lower()

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


@pytest.mark.asyncio
async def test_enqueue_creates_independent_packages_per_sender(tmp_path, monkeypatch):
    service = build_service(tmp_path)

    class FakeRepo:
        def __init__(self):
            self.packages = {}
            self.items = {}
            self.next_id = 0

        async def get_item(self, bot_id, item_id):
            return self.items.get(item_id)

        async def get_open_package(self, bot_id, room_id, sender_id):
            return next((p for p in self.packages.values() if p["room_id"] == room_id and p["sender_id"] == sender_id and p["state"] == "OPEN"), None)

        async def create_package(self, bot_id, room_id, sender_id):
            self.next_id += 1
            package = {"bot_id": bot_id, "room_id": room_id, "sender_id": sender_id, "package_id": f"p{self.next_id}", "state": "OPEN", "item_count": 0, "pending_count": 0, "reserved_bytes": 0}
            self.packages[package["package_id"]] = package
            return package

        async def add_item(self, bot_id, item):
            self.items[item["item_id"]] = dict(item)
            package = self.packages[item["package_id"]]
            package["item_count"] += 1
            package["pending_count"] += 1
            package["reserved_bytes"] += item["expected_size"]
            return True

        async def get_package(self, bot_id, package_id):
            return self.packages.get(package_id)

        async def close_package(self, *args, **kwargs):
            return None

    fake_repo = FakeRepo()
    service.repo = fake_repo
    service.ctx.repositories.packages = fake_repo

    async def no_progress(_package_id):
        return None
    monkeypatch.setattr(service, "schedule_progress_refresh", no_progress)

    def msg(user_id, message_id):
        return SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type="private"),
            message_id=message_id,
            from_user=SimpleNamespace(id=user_id),
        )

    accepted_a, item_a, error_a = await service.enqueue(msg(101, 1), "room-1", "video", "file-a", 100, "a.mp4")
    accepted_b, item_b, error_b = await service.enqueue(msg(202, 1), "room-1", "video", "file-b", 100, "b.mp4")

    assert accepted_a and accepted_b and not error_a and not error_b
    package_a = fake_repo.packages[fake_repo.items[item_a]["package_id"]]
    package_b = fake_repo.packages[fake_repo.items[item_b]["package_id"]]
    assert package_a["sender_id"] == 101
    assert package_b["sender_id"] == 202
    assert package_a["package_id"] != package_b["package_id"]


def test_sha256_helper_detects_same_video_bytes(tmp_path):
    left = tmp_path / "left.mp4"
    right = tmp_path / "right.mp4"
    left.write_bytes(b"video-payload" * 1024)
    right.write_bytes(b"video-payload" * 1024)
    assert RoomPackageService._sha256_file(left) == RoomPackageService._sha256_file(right)


@pytest.mark.asyncio
async def test_successful_delivery_keeps_final_zip_message_in_configured_channel(tmp_path):
    service = build_service(tmp_path)
    package = {
        "bot_id": 12345, "package_id": "pkg-final", "room_id": "room-1", "sender_id": 444,
        "state": "DELIVERING", "delivery_user_ids": [444], "staged_count": 10,
        "archive_size": 123, "archive_path": str(tmp_path / "does-not-need-upload.zip"),
        "password_encrypted": service.secret_box.encrypt("pass"), "status_message_ids": {},
        "delivery_source_chat_id": -100111, "delivery_source_message_id": 55,
        "delivery_source_chat": "configured_archive_channel",
    }
    deleted = []

    class FakeRepo:
        def __init__(self): self.doc = dict(package)
        async def set_package_fields(self, bot_id, package_id, fields, expected_state=None):
            if expected_state and self.doc.get("state") != expected_state:
                return False
            self.doc.update(fields)
            return True
        async def get_package(self, bot_id, package_id): return dict(self.doc)
        async def get_delivery_source(self, bot_id, package_id): return (-100111, 55)
        async def deliveries_for(self, bot_id, package_id): return set(self.sent)
        async def mark_delivery(self, bot_id, package_id, user_id, status, error=None): self.sent.add(user_id)
        async def list_failed_deliveries(self, bot_id, package_id): return []
        async def expire_package_items_in(self, *args, **kwargs): return None
        async def expire_deliveries_in(self, *args, **kwargs): return None
        sent = set()

    repo = FakeRepo()
    service.repo = repo
    service.ctx.repositories.packages = repo

    async def copy_message(**kwargs): return None
    async def delete_message(**kwargs): deleted.append(kwargs)
    service.ctx.bot.copy_message = copy_message
    service.ctx.bot.delete_message = delete_message
    await service._finalize_package("pkg-final")

    assert repo.doc["state"] == "SENT"
    assert repo.doc["channel_archive_kept"] if "channel_archive_kept" in repo.doc else True
    assert deleted == []

@pytest.mark.asyncio
async def test_progress_status_is_pinned_for_package_owner_only(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    package = {
        "bot_id": 12345, "package_id": "pin-test", "room_id": "room-1", "sender_id": 444,
        "state": "OPEN", "created_at": __import__("app.core.datetime", fromlist=["utcnow"]).utcnow(),
        "last_item_at": __import__("app.core.datetime", fromlist=["utcnow"]).utcnow(),
        "item_count": 1, "staged_count": 1, "pending_count": 0, "failed_count": 0,
        "duplicate_count": 0, "staged_bytes": 1024, "reserved_bytes": 1024,
        "status_message_ids": {},
    }

    class FakeRepository:
        async def get_package(self, bot_id, package_id):
            return dict(package)

        async def count_recent_user_videos(self, bot_id, room_id, sender_id, since):
            assert sender_id == 444
            return 1

        async def set_status_message(self, bot_id, package_id, user_id, message_id):
            return None

    sent_to = []
    pinned = []

    async def send_message(chat_id, text, reply_markup=None):
        sent_to.append(chat_id)
        return SimpleNamespace(message_id=1000 + chat_id)

    async def pin_chat_message(**kwargs):
        pinned.append(kwargs)

    class AdminFeed:
        async def enabled_admins(self):
            return []

    service.repo = FakeRepository()
    service.ctx.repositories.packages = service.repo
    service.ctx.services = SimpleNamespace(admin_feed=AdminFeed())
    service.ctx.bot.send_message = send_message
    service.ctx.bot.pin_chat_message = pin_chat_message
    monkeypatch.setattr(service, "_status_markup", lambda *_args, **_kwargs: None)

    await service._refresh_progress_all("pin-test")

    assert set(sent_to) == {444, 777}
    assert pinned == [{"chat_id": 444, "message_id": 1444, "disable_notification": True}]

@pytest.mark.asyncio
async def test_final_archive_upload_targets_configured_channel_and_stores_source(tmp_path, monkeypatch):
    import sys
    import types

    service = build_service(tmp_path, storage_channel_id="-100111")
    service.ctx.settings.telegram_api_timeout_seconds = 60
    sent_kwargs = {}

    class FakeFSInputFile:
        def __init__(self, path, filename=None):
            self.path = str(path)
            self.filename = filename

    aiogram_module = types.ModuleType("aiogram")
    aiogram_module.__path__ = []
    aiogram_types_module = types.ModuleType("aiogram.types")
    aiogram_types_module.FSInputFile = FakeFSInputFile
    monkeypatch.setitem(sys.modules, "aiogram", aiogram_module)
    monkeypatch.setitem(sys.modules, "aiogram.types", aiogram_types_module)

    async def send_document(**kwargs):
        sent_kwargs.update(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(id=-100111), message_id=987)

    updated = {}

    class FakeRepository:
        async def set_package_fields(self, bot_id, package_id, fields):
            updated.update(fields)
            return True

    service.ctx.bot.send_document = send_document
    service.repo = FakeRepository()
    service.ctx.repositories.packages = service.repo
    archive = tmp_path / "final-user-444.zip"
    archive.write_bytes(b"ZIP")

    result = await service._upload_archive_to_first_recipient(
        {"bot_id": 12345, "package_id": "pkg-archive", "room_id": "room-1", "sender_id": 444,
         "staged_count": 10, "archive_size": 3, "archive_name": archive.name},
        [444, 777], archive, "zip-password",
    )

    assert result == (-100111, 987)
    assert sent_kwargs["chat_id"] == -100111
    assert sent_kwargs["document"].path == str(archive)
    assert "Usuario ID: <code>444</code>" in sent_kwargs["caption"]
    assert "Contraseña AES-256" in sent_kwargs["caption"]
    assert updated["delivery_source_chat_id"] == -100111
    assert updated["delivery_source_message_id"] == 987
    assert updated["channel_archive_kept"] is True
