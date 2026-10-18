from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import secrets
import shutil
import time
from datetime import timedelta
from pathlib import Path, PurePath
from urllib.parse import quote

import httpx

from app.core.crypto import SecretBox
from app.core.datetime import utcnow

_MIB = 1024 * 1024
# Shared limits across all child bots in this application process. Heavy work is
# moved off the webhook event path and these semaphores keep many rooms/bots
# from saturating the server all at once.
_GLOBAL_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(8)
_GLOBAL_ARCHIVE_SEMAPHORE = asyncio.Semaphore(1)
_GLOBAL_DELIVERY_SEMAPHORE = asyncio.Semaphore(4)
_GLOBAL_STATUS_SEMAPHORE = asyncio.Semaphore(4)
_GLOBAL_API_RATE_LOCK = asyncio.Lock()
_GLOBAL_LAST_API_WRITE = 0.0
_GLOBAL_API_WRITE_INTERVAL = 0.05  # Keep this service below ~20 outbound sends/second.
_GLOBAL_DISK_RESERVATION_LOCK = asyncio.Lock()
_GLOBAL_RESERVED_DISK_BYTES = 0
_DISK_FREE_RESERVE_BYTES = 512 * _MIB


def format_bytes(value: int) -> str:
    amount = max(0, int(value))
    if amount >= 1024**3:
        return f"{amount / (1024**3):.2f} GiB"
    if amount >= _MIB:
        return f"{amount / _MIB:.1f} MiB"
    if amount >= 1024:
        return f"{amount / 1024:.1f} KiB"
    return f"{amount} B"


def safe_archive_filename(value: str | None, fallback: str) -> str:
    raw = str(value or "").replace("\\", "/")
    name = PurePath(raw).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = fallback
    # Keep names manageable and avoid a very long ZIP directory record.
    return name[:180]


def progress_bar(used: int, total: int, width: int = 10) -> str:
    total = max(1, int(total))
    ratio = min(1.0, max(0.0, int(used) / total))
    filled = min(width, int(ratio * width))
    return "█" * filled + "░" * (width - filled)


class RoomPackageService:
    """Download each room file to durable disk, then deliver encrypted ZIP batches.

    Telegram updates only enqueue metadata. Network downloads, filesystem copies,
    ZIP creation and fan-out run in background workers so webhook handlers remain
    short and cancellable. File names and passwords are never logged.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.repo = ctx.repositories.packages
        self.logger = logging.getLogger(f"room_packages.{ctx.bot_id}")
        self.base_dir = Path(ctx.settings.media_package_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(64 * _MIB, int(ctx.settings.media_package_max_bytes))
        self.idle_seconds = max(10, int(ctx.settings.media_package_idle_seconds))
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(50, int(ctx.settings.room_queue_maxsize)))
        self._assignment_locks: dict[str, asyncio.Lock] = {}
        self._download_locks: dict[str, asyncio.Lock] = {}
        self._status_locks: dict[str, asyncio.Lock] = {}
        self._status_tasks: dict[str, asyncio.Task] = {}
        self._status_dirty: set[str] = set()
        self._finalize_tasks: dict[str, asyncio.Task] = {}
        self._archive_lock = _GLOBAL_ARCHIVE_SEMAPHORE
        self._delivery_lock = _GLOBAL_DELIVERY_SEMAPHORE
        self._scheduled_items: set[str] = set()
        self._last_status_refresh: dict[str, float] = {}
        self._last_counters_reconcile: dict[str, float] = {}
        self.secret_box = SecretBox(ctx.settings.token_encryption_key)
        self._last_recovery = 0.0

    async def _pace_outbound_call(self) -> None:
        """Limit sends/copies across all child bots without blocking the event loop."""
        global _GLOBAL_LAST_API_WRITE
        async with _GLOBAL_API_RATE_LOCK:
            now = time.monotonic()
            wait_for = _GLOBAL_API_WRITE_INTERVAL - (now - _GLOBAL_LAST_API_WRITE)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            _GLOBAL_LAST_API_WRITE = time.monotonic()

    def _lock(self, mapping: dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
        lock = mapping.get(key)
        if lock is None:
            lock = asyncio.Lock()
            mapping[key] = lock
        return lock

    async def enqueue(self, message, room_id: str, media_type: str, file_id: str, expected_size: int, filename: str | None) -> tuple[bool, str | None, str | None]:
        """Persist a queue item before returning; never delete the user's source here."""
        # A private source buffer is required because delivery fans out from one
        # Telegram message. Reject up front if it is missing so users never see
        # their original removed for a package that cannot be delivered.
        channel_value = str(getattr(self.ctx.settings, "storage_channel_id", "") or "").strip()
        if not channel_value.lstrip("-").isdigit() or not channel_value.startswith("-"):
            return False, None, "El sistema de paquetes todavía no tiene configurado el canal buffer privado. No se borró el mensaje original; avisá al administrador."
        expected_size = max(0, int(expected_size or 0))
        if expected_size > self.max_bytes:
            return False, None, f"Este archivo pesa {format_bytes(expected_size)} y supera el máximo por archivo ({format_bytes(self.max_bytes)}). No se borró el original."
        chat_id = int(message.chat.id)
        message_id = int(message.message_id)
        item_id = hashlib.sha256(f"{self.ctx.bot_id}:{chat_id}:{message_id}".encode()).hexdigest()[:32]
        item_existing = await self.repo.get_item(self.ctx.bot_id, item_id)
        if item_existing:
            self._queue_item(item_id)
            await self.schedule_progress_refresh(str(item_existing.get("package_id") or ""))
            return True, item_id, None

        lock = self._lock(self._assignment_locks, str(room_id))
        async with lock:
            # A duplicate can arrive while another webhook is assigning this item.
            item_existing = await self.repo.get_item(self.ctx.bot_id, item_id)
            if item_existing:
                self._queue_item(item_id)
                return True, item_id, None

            package = await self.repo.get_open_package(self.ctx.bot_id, room_id)
            if package and int(package.get("item_count", 0)) > 0:
                projected = int(package.get("reserved_bytes", 0)) + expected_size
                if expected_size and projected > self.max_bytes:
                    await self.repo.close_package(self.ctx.bot_id, package["package_id"], "size_limit")
                    await self.schedule_progress_refresh(package["package_id"])
                    package = None
            if package is None:
                package = await self.repo.create_package(self.ctx.bot_id, room_id)

            name = safe_archive_filename(filename, self._default_filename(media_type, message_id))
            item = {
                "item_id": item_id,
                "package_id": str(package["package_id"]),
                "room_id": str(room_id),
                "sender_id": int(message.from_user.id),
                "original_chat_id": chat_id,
                "original_message_id": message_id,
                "file_id": str(file_id),
                "media_type": str(media_type),
                "filename": name,
                "expected_size": expected_size,
                "status": "QUEUED",
                "source_deleted": False,
                "source_delete_attempted": False,
            }
            inserted = await self.repo.add_item(self.ctx.bot_id, item)
            if not inserted:
                existing = await self.repo.get_item(self.ctx.bot_id, item_id)
                if existing:
                    self._queue_item(item_id)
                    return True, item_id, None
                raise RuntimeError("No se pudo guardar el archivo en la cola persistente.")
            current = await self.repo.get_package(self.ctx.bot_id, package["package_id"])
            if current and int(current.get("reserved_bytes", 0)) >= self.max_bytes:
                await self.repo.close_package(self.ctx.bot_id, package["package_id"], "size_limit")
            self._queue_item(item_id)
            await self.schedule_progress_refresh(package["package_id"])
            self.logger.info(
                "room_package_item_queued bot_id=%s room_id=%s package_id=%s media_type=%s expected_bytes=%s",
                self.ctx.bot_id, room_id, package["package_id"], media_type, expected_size,
            )
            return True, item_id, None

    @staticmethod
    def _default_filename(media_type: str, message_id: int) -> str:
        extensions = {"photo": ".jpg", "video": ".mp4", "animation": ".mp4", "audio": ".mp3", "voice": ".ogg", "video_note": ".mp4", "sticker": ".webp", "document": ".bin"}
        return f"{media_type}-{message_id}{extensions.get(media_type, '.bin')}"

    def _queue_item(self, item_id: str) -> None:
        if not item_id:
            return
        item_id = str(item_id)
        if item_id in self._scheduled_items:
            return
        self._scheduled_items.add(item_id)
        try:
            self.queue.put_nowait(item_id)
        except asyncio.QueueFull:
            self._scheduled_items.discard(item_id)
            # The MongoDB record is durable. The monitor loop requeues it as
            # capacity becomes available; never block Telegram's webhook.
            self.logger.warning("room_package_queue_full bot_id=%s queue_size=%s", self.ctx.bot_id, self.queue.qsize())

    async def worker_loop(self, worker_number: int) -> None:
        await self._recover_pending_items()
        while True:
            try:
                item_id = await asyncio.wait_for(self.queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # The monitor recovers durable Mongo work periodically. Idle workers
                # should not issue their own DB query every second for every child bot.
                continue
            try:
                await self._process_item(item_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("room_package_item_worker_failed bot_id=%s worker=%s", self.ctx.bot_id, worker_number)
            finally:
                self._scheduled_items.discard(item_id)
                self.queue.task_done()

    async def monitor_loop(self) -> None:
        """Recover pending work and close idle/full packages without a blocking timer."""
        while True:
            try:
                await self._recover_pending_items()
                for package in await self.repo.list_packages_to_check(self.ctx.bot_id, 100):
                    package_id = str(package.get("package_id") or "")
                    state = str(package.get("state") or "")
                    if not package_id:
                        continue
                    last_sync = self._last_counters_reconcile.get(package_id, 0.0)
                    if time.monotonic() - last_sync >= 12.0 and state not in {"SENT", "FAILED", "EMPTY"}:
                        room_key = str(package.get("room_id") or "")
                        async with self._lock(self._assignment_locks, room_key):
                            await self.repo.reconcile_package_counters(self.ctx.bot_id, package_id)
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or state)
                            self._last_counters_reconcile[package_id] = time.monotonic()
                    if state == "OPEN" and int(package.get("pending_count", 0)) == 0:
                        last = package.get("last_item_at") or package.get("created_at") or utcnow()
                        idle_for = (utcnow() - last).total_seconds()
                        if int(package.get("item_count", 0)) > 0 and idle_for >= self.idle_seconds:
                            await self.repo.close_package(self.ctx.bot_id, package_id, "idle_timeout")
                            await self.schedule_progress_refresh(package_id)
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or "")
                        elif int(package.get("item_count", 0)) <= 0 and idle_for >= self.idle_seconds:
                            # Failed-only packages must not remain OPEN forever.
                            await self.repo.set_package_fields(
                                self.ctx.bot_id, package_id,
                                {"state": "EMPTY", "completed_at": utcnow(), "last_error": "no_staged_items"},
                                expected_state="OPEN",
                            )
                            await self.schedule_progress_refresh(package_id)
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or "")
                    if state == "SENT" and not package.get("files_cleaned", False):
                        try:
                            await asyncio.to_thread(shutil.rmtree, self._package_dir(str(package["room_id"]), package_id), True)
                        finally:
                            await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"files_cleaned": True})
                            await self.repo.expire_package_items_in(self.ctx.bot_id, package_id, utcnow() + timedelta(days=30))
                    if state in {"CLOSING", "PROCESSING", "DELIVERING", "SENT_WITH_ERRORS"}:
                        last_finalize = package.get("last_finalize_attempt_at")
                        if last_finalize and (utcnow() - last_finalize).total_seconds() < 30:
                            continue
                        if state == "CLOSING" and int(package.get("pending_count", 0)) <= 0:
                            if int(package.get("staged_count", 0)) > 0:
                                self._schedule_finalize(package_id)
                            else:
                                await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "EMPTY", "completed_at": utcnow()})
                        elif state == "PROCESSING":
                            # A prior process may have stopped during ZIP creation.
                            self._schedule_finalize(package_id)
                        elif state in {"DELIVERING", "SENT_WITH_ERRORS"}:
                            last_attempt = package.get("last_delivery_attempt_at")
                            # Back off longer on a blocked recipient or unavailable
                            # buffer. This prevents retry loops from consuming the
                            # Bot API quota while keeping the package durable.
                            if last_attempt and (utcnow() - last_attempt).total_seconds() < 300:
                                continue
                            self._schedule_finalize(package_id)
                # Five-second monitoring is enough for a 60-second idle cutoff and
                # avoids a tight MongoDB polling loop when many child bots are active.
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("room_package_monitor_failed bot_id=%s", self.ctx.bot_id)
                await asyncio.sleep(3)

    async def _recover_pending_items(self) -> None:
        now = time.monotonic()
        if now - self._last_recovery < 1.0 and not self.queue.empty():
            return
        self._last_recovery = now
        for item in await self.repo.list_pending_items(self.ctx.bot_id, 250):
            if item.get("status") == "STAGED_DELETE_PENDING":
                self._queue_item(str(item["item_id"]))
            else:
                self._queue_item(str(item["item_id"]))

    async def _process_item(self, item_id: str) -> None:
        item = await self.repo.get_item(self.ctx.bot_id, item_id)
        if not item:
            return
        if item.get("status") == "STAGED_DELETE_PENDING":
            await self._delete_source_after_staging(item)
            return
        if item.get("status") == "STAGED" or item.get("status") == "FAILED":
            return
        room_id = str(item.get("room_id") or "")
        async with self._lock(self._download_locks, room_id):
            # Re-read after waiting for a per-room lock: another retry may finish it.
            item = await self.repo.get_item(self.ctx.bot_id, item_id)
            if not item or item.get("status") not in {"QUEUED", "DOWNLOADING"}:
                return
            await self.repo.mark_downloading(self.ctx.bot_id, item_id)
            # Room IDs come from MongoDB, but normalize the path component anyway
            # so a damaged record can never escape the package volume.
            incoming_dir = self.base_dir / str(int(self.ctx.bot_id)) / self._safe_path_component(room_id, "room") / ".incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            incoming_path = incoming_dir / f"{item_id}.part"
            staged_tmp = incoming_dir / f"{item_id}.download"
            # This is referenced by the exception path too: initialize it before
            # any download step so a failed getFile/path mapping cannot mask the
            # original exception with UnboundLocalError and strand the queue item.
            target_path: Path | None = None
            for part in (incoming_path, staged_tmp):
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
            # A crash can occur after moving the download into /files but before
            # committing the STAGED item state. Remove only this item's orphaned
            # staging path before a safe retry.
            orphan_dir = self._package_dir(room_id, str(item.get("package_id") or "")) / "files"
            try:
                for orphan in orphan_dir.glob(f"{item_id[:8]}_*"):
                    orphan.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                async with _GLOBAL_DOWNLOAD_SEMAPHORE:
                    actual_size = await self._download_to_local(item, staged_tmp)
                if actual_size <= 0:
                    raise RuntimeError("El archivo descargado está vacío.")
                if actual_size > self.max_bytes:
                    raise RuntimeError(f"El archivo supera el máximo del paquete ({format_bytes(self.max_bytes)}); no se borró el original.")
                # Move out of temporary suffix only after the download is complete.
                os.replace(staged_tmp, incoming_path)
                item = await self._fit_item_in_package(item, actual_size)
                package_dir = self._package_dir(room_id, str(item["package_id"]))
                files_dir = package_dir / "files"
                files_dir.mkdir(parents=True, exist_ok=True)
                target_name = safe_archive_filename(item.get("filename"), self._default_filename(str(item.get("media_type") or "file"), int(item.get("original_message_id", 0))))
                target_path = files_dir / f"{item_id[:8]}_{target_name}"
                os.replace(incoming_path, target_path)
                await asyncio.to_thread(self._fsync_directory, files_dir)
                # Mark and verify the durable copy before deleting the member's
                # source message. Never trust a bare successful file move alone.
                async with self._lock(self._assignment_locks, room_id):
                    staged_ok = await self.repo.mark_staged(self.ctx.bot_id, item, str(target_path), actual_size)
                staged_item = await self.repo.get_item(self.ctx.bot_id, item_id) or item
                if (
                    not staged_ok
                    or staged_item.get("status") not in {"STAGED_DELETE_PENDING", "STAGED"}
                    or str(staged_item.get("staged_path") or "") != str(target_path)
                    or int(staged_item.get("staged_size", -1)) != actual_size
                ):
                    raise RuntimeError("No se pudo confirmar en la base de datos la copia completa; el original se conserva.")
                await self._delete_source_after_staging(staged_item)
                await self.schedule_progress_refresh(str(staged_item["package_id"]))
                package = await self.repo.get_package(self.ctx.bot_id, str(staged_item["package_id"]))
                if package and int(package.get("staged_bytes", 0)) >= self.max_bytes:
                    await self.repo.close_package(self.ctx.bot_id, str(staged_item["package_id"]), "size_limit")
                    await self.schedule_progress_refresh(str(staged_item["package_id"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    staged_tmp.unlink(missing_ok=True)
                    incoming_path.unlink(missing_ok=True)
                except OSError:
                    pass
                current = await self.repo.get_item(self.ctx.bot_id, item_id)
                stage_committed = bool(
                    current
                    and current.get("status") in {"STAGED", "STAGED_DELETE_PENDING"}
                    and target_path is not None
                    and str(current.get("staged_path") or "") == str(target_path)
                )
                if target_path is not None and not stage_committed:
                    try:
                        target_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                if current and current.get("status") in {"QUEUED", "DOWNLOADING"}:
                    async with self._lock(self._assignment_locks, room_id):
                        await self.repo.mark_item_failed(self.ctx.bot_id, current, str(exc))
                    await self._notify_source_failure(item, str(exc))
                    await self.schedule_progress_refresh(str(current.get("package_id") or ""))
                elif not current or current.get("status") not in {"STAGED", "STAGED_DELETE_PENDING"}:
                    await self._notify_source_failure(item, str(exc))
                self.logger.warning(
                    "room_package_item_failed bot_id=%s room_id=%s item_id=%s error_type=%s",
                    self.ctx.bot_id, room_id, item_id, type(exc).__name__,
                )

    async def _download_to_local(self, item: dict, destination: Path) -> int:
        """Fetch getFile metadata raw to avoid Aiogram's path wrapper raising early.

        In local Bot API mode, getFile provides a path in the Bot API server's
        data root. We map that path against the read-only shared mount and copy
        to this app's persistent volume. For cloud mode, Aiogram downloads via HTTP.
        """
        base = str(self.ctx.settings.telegram_api_base_url).rstrip("/")
        token = str(getattr(self.ctx.bot, "token", "") or "").strip()
        if not token:
            raise RuntimeError("El bot no tiene token disponible para descargar el archivo.")
        url = f"{base}/bot{quote(token, safe=':')}/getFile"
        timeout = max(30, int(getattr(self.ctx.settings, "telegram_api_timeout_seconds", 7200)))
        async with httpx.AsyncClient(timeout=min(timeout, 120)) as client:
            response = await client.post(url, json={"file_id": item["file_id"]})
            response.raise_for_status()
            payload = response.json()
        if not payload.get("ok") or not isinstance(payload.get("result"), dict):
            description = str(payload.get("description") or "getFile rechazó el archivo")
            raise RuntimeError(description[:300])
        file_path = str(payload["result"].get("file_path") or "")
        if not file_path:
            raise RuntimeError("Telegram no devolvió la ruta del archivo.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected = int(payload["result"].get("file_size") or item.get("expected_size") or 0)
        if expected > self.max_bytes:
            raise RuntimeError(f"El archivo supera el máximo del paquete ({format_bytes(self.max_bytes)}); no se borró el original.")
        # Reserve disk space across all child bots before starting large copies.
        # ZIP creation later needs a second copy of roughly the same size.
        reservation = max(expected, 32 * _MIB)
        await self._reserve_disk_space(reservation)
        try:
            if bool(self.ctx.settings.telegram_api_local_mode):
                source_path = await asyncio.to_thread(self._resolve_local_api_file, file_path)
                actual_source_size = await asyncio.to_thread(lambda: source_path.stat().st_size)
                if actual_source_size > self.max_bytes:
                    raise RuntimeError(f"El archivo supera el máximo del paquete ({format_bytes(self.max_bytes)}); no se borró el original.")
                if actual_source_size > reservation:
                    # Update the reservation if the Bot API did not report a size.
                    await self._increase_disk_reservation(actual_source_size - reservation)
                    reservation = actual_source_size
                await asyncio.to_thread(self._copy_durable, source_path, destination)
            else:
                # In cloud mode the standard Bot API requires downloadFile; use the
                # configured Aiogram session, which is not wrapped in local mode.
                await self.ctx.bot.download_file(file_path, destination=destination, timeout=timeout)
                # Aiogram closes the file after download, but fsync makes the staged
                # data durable before we record it and delete the user's message.
                await asyncio.to_thread(self._fsync_file, destination)
            actual = destination.stat().st_size
            if actual > self.max_bytes:
                raise RuntimeError(f"El archivo supera el máximo del paquete ({format_bytes(self.max_bytes)}); no se borró el original.")
            if expected and actual != expected:
                raise RuntimeError(f"El tamaño descargado no coincide con el informado por Telegram ({actual} != {expected}).")
            return actual
        finally:
            await self._release_disk_reservation(reservation)

    async def _reserve_disk_space(self, amount: int) -> None:
        """Reserve working disk globally so parallel child bots cannot fill the volume."""
        amount = max(0, int(amount))
        global _GLOBAL_RESERVED_DISK_BYTES
        async with _GLOBAL_DISK_RESERVATION_LOCK:
            free = shutil.disk_usage(self.base_dir).free
            if free - _GLOBAL_RESERVED_DISK_BYTES < amount + _DISK_FREE_RESERVE_BYTES:
                raise OSError(
                    f"Espacio insuficiente en Vultr: quedan {format_bytes(max(0, free - _GLOBAL_RESERVED_DISK_BYTES))}; "
                    f"se requieren {format_bytes(amount + _DISK_FREE_RESERVE_BYTES)} para conservar una reserva de seguridad."
                )
            _GLOBAL_RESERVED_DISK_BYTES += amount

    async def _increase_disk_reservation(self, extra: int) -> None:
        extra = max(0, int(extra))
        global _GLOBAL_RESERVED_DISK_BYTES
        if not extra:
            return
        async with _GLOBAL_DISK_RESERVATION_LOCK:
            free = shutil.disk_usage(self.base_dir).free
            if free - _GLOBAL_RESERVED_DISK_BYTES < extra + _DISK_FREE_RESERVE_BYTES:
                raise OSError("Espacio insuficiente en Vultr para continuar la descarga; el original se conserva.")
            _GLOBAL_RESERVED_DISK_BYTES += extra

    async def _release_disk_reservation(self, amount: int) -> None:
        global _GLOBAL_RESERVED_DISK_BYTES
        async with _GLOBAL_DISK_RESERVATION_LOCK:
            _GLOBAL_RESERVED_DISK_BYTES = max(0, _GLOBAL_RESERVED_DISK_BYTES - max(0, int(amount)))

    def _resolve_local_api_file(self, server_path: str) -> Path:
        # Use the same tested, per-bot-scoped resolver as Aiogram's local Bot API
        # file wrapper. Keeping one implementation avoids path-mapping drift.
        from app.services.local_file_paths import map_api_file_path

        token = str(getattr(self.ctx.bot, "token", "") or "").strip()
        return map_api_file_path(self.ctx.settings, server_path, bot_token=token)

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with Path(path).open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _copy_durable(source: Path, destination: Path) -> None:
        source = Path(source)
        destination = Path(destination)
        shutil.copyfile(source, destination)
        with destination.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    async def _fit_item_in_package(self, item: dict, actual_size: int) -> dict:
        room_id = str(item["room_id"])
        async with self._lock(self._assignment_locks, room_id):
            current = await self.repo.get_item(self.ctx.bot_id, str(item["item_id"])) or item
            package = await self.repo.get_package(self.ctx.bot_id, str(current["package_id"]))
            if not package:
                package = await self.repo.create_package(self.ctx.bot_id, room_id)
                await self.repo.update_item_package(self.ctx.bot_id, str(current["item_id"]), str(package["package_id"]), actual_size)
                current["package_id"] = str(package["package_id"])
                current["expected_size"] = actual_size
                # Recover counters for an orphaned queue item.
                await self.ctx.repositories.packages.packages.update_one(
                    {"bot_id": self.ctx.bot_id, "package_id": current["package_id"]},
                    {"$inc": {"item_count": 1, "pending_count": 1, "reserved_bytes": actual_size}},
                )
                return current
            expected = max(0, int(current.get("expected_size") or 0))
            projected = int(package.get("reserved_bytes", 0)) - expected + actual_size
            if projected > self.max_bytes and int(package.get("item_count", 0)) > 1:
                await self.repo.close_package(self.ctx.bot_id, package["package_id"], "size_limit_actual")
                new_package = await self.repo.get_open_package(self.ctx.bot_id, room_id)
                if new_package and int(new_package.get("reserved_bytes", 0)) + actual_size > self.max_bytes:
                    await self.repo.close_package(self.ctx.bot_id, new_package["package_id"], "size_limit_actual")
                    await self.schedule_progress_refresh(new_package["package_id"])
                    new_package = None
                if new_package is None:
                    new_package = await self.repo.create_package(self.ctx.bot_id, room_id)
                current = await self.repo.reassign_item(self.ctx.bot_id, current, new_package, actual_size)
                await self.schedule_progress_refresh(package["package_id"])
                await self.schedule_progress_refresh(new_package["package_id"])
                return current
            return current

    async def _delete_source_after_staging(self, item: dict) -> None:
        if item.get("source_deleted") or item.get("source_delete_attempted"):
            return
        current = await self.repo.get_item(self.ctx.bot_id, str(item.get("item_id") or ""))
        if not current or current.get("status") not in {"STAGED_DELETE_PENDING", "STAGED"}:
            self.logger.error("room_package_source_delete_skipped_unstaged bot_id=%s item_id=%s", self.ctx.bot_id, item.get("item_id"))
            return
        staged_path = Path(str(current.get("staged_path") or ""))
        try:
            resolved = staged_path.resolve(strict=True)
            resolved.relative_to(self.base_dir)
            if not resolved.is_file() or resolved.stat().st_size != int(current.get("staged_size", -1)):
                raise OSError("staged file size mismatch")
        except (OSError, ValueError, RuntimeError):
            await self.repo.mark_staged_storage_failed(self.ctx.bot_id, str(current["item_id"]), "staged_file_missing_or_invalid")
            async with self._lock(self._assignment_locks, str(current.get("room_id") or "")):
                await self.repo.reconcile_package_counters(self.ctx.bot_id, str(current.get("package_id") or ""))
            await self._notify_source_failure(current, "staged_file_missing_or_invalid")
            self.logger.error("room_package_source_delete_skipped_invalid_staged_file bot_id=%s item_id=%s", self.ctx.bot_id, current.get("item_id"))
            return
        try:
            await self.ctx.bot.delete_message(
                chat_id=int(item["original_chat_id"]),
                message_id=int(item["original_message_id"]),
            )
            await self.repo.mark_source_deleted(self.ctx.bot_id, item["item_id"], deleted=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.repo.mark_source_deleted(self.ctx.bot_id, item["item_id"], deleted=False, error=type(exc).__name__)
            self.logger.warning(
                "room_package_source_delete_failed bot_id=%s room_id=%s item_id=%s error_type=%s",
                self.ctx.bot_id, item.get("room_id"), item.get("item_id"), type(exc).__name__,
            )
            try:
                await self.ctx.bot.send_message(
                    chat_id=int(item["sender_id"]),
                    text="✅ El archivo ya está guardado en el paquete, pero Telegram no permitió borrar el mensaje original. No hace falta volver a enviarlo.",
                )
            except Exception:
                pass

    async def _notify_source_failure(self, item: dict, error: str) -> None:
        # The original remains visible until the download is fully staged.
        message = "❌ No pude descargar y guardar este archivo en Vultr. El mensaje original se conserva; no se ha borrado ni añadido al ZIP."
        try:
            await self._pace_outbound_call()
            await self.ctx.bot.send_message(chat_id=int(item["sender_id"]), text=message)
        except Exception:
            self.logger.info("room_package_failure_notice_unavailable bot_id=%s item_id=%s", self.ctx.bot_id, item.get("item_id"))

    @staticmethod
    def _safe_path_component(value: str, fallback: str = "item") -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or ""))[:80].strip("._-")
        return cleaned or fallback

    def _package_dir(self, room_id: str, package_id: str) -> Path:
        # IDs originate in MongoDB, but still normalize them to prevent path traversal
        # if a damaged or manually edited record contains separators.
        return self.base_dir / str(int(self.ctx.bot_id)) / self._safe_path_component(room_id, "room") / self._safe_path_component(package_id, "package")

    async def schedule_progress_refresh(self, package_id: str) -> None:
        package_id = str(package_id or "")
        if not package_id:
            return
        self._status_dirty.add(package_id)
        current = self._status_tasks.get(package_id)
        if current and not current.done():
            return
        task_registry = getattr(self.ctx, "task_registry", None)
        coro = self._debounced_progress_refresh(package_id)
        task = task_registry.create(coro, f"package-status:{self.ctx.bot_id}:{package_id}") if task_registry else asyncio.create_task(coro)
        self._status_tasks[package_id] = task

    async def _debounced_progress_refresh(self, package_id: str) -> None:
        try:
            while package_id in self._status_dirty:
                self._status_dirty.discard(package_id)
                last = self._last_status_refresh.get(package_id, 0.0)
                delay = max(1.5, 5.0 - (time.monotonic() - last))
                await asyncio.sleep(delay)
                await self._refresh_progress_all(package_id)
                self._last_status_refresh[package_id] = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("room_package_progress_refresh_failed bot_id=%s package_id=%s", self.ctx.bot_id, package_id)
        finally:
            self._status_tasks.pop(package_id, None)
            if package_id in self._status_dirty:
                await self.schedule_progress_refresh(package_id)

    async def _refresh_progress_all(self, package_id: str) -> None:
        package = await self.repo.get_package(self.ctx.bot_id, package_id)
        if not package:
            return
        lock = self._lock(self._status_locks, package_id)
        async with lock:
            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
            text = self._status_text(package)
            room_id = str(package["room_id"])
            members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
            semaphore = asyncio.Semaphore(2)

            async def update_one(member: dict) -> None:
                uid = int(member["user_id"])
                async with semaphore, _GLOBAL_STATUS_SEMAPHORE:
                    old_id = (package.get("status_message_ids") or {}).get(str(uid))
                    markup = self._status_markup(package_id, uid)
                    if old_id:
                        try:
                            await self._pace_outbound_call()
                            await asyncio.wait_for(
                                self.ctx.bot.edit_message_text(chat_id=uid, message_id=int(old_id), text=text, reply_markup=markup),
                                timeout=20,
                            )
                            return
                        except Exception as exc:
                            if "message is not modified" in str(exc).lower():
                                return
                    try:
                        await self._pace_outbound_call()
                        sent = await asyncio.wait_for(self.ctx.bot.send_message(uid, text, reply_markup=markup), timeout=20)
                        await self.repo.set_status_message(self.ctx.bot_id, package_id, uid, int(sent.message_id))
                    except Exception as exc:
                        self.logger.debug("room_package_status_send_failed bot_id=%s room_id=%s user_id=%s error_type=%s", self.ctx.bot_id, room_id, uid, type(exc).__name__)

            # Never materialize a task for every room member at once. Rooms can
            # contain thousands of users; batches keep memory and API pressure bounded.
            batch_size = 50
            for start in range(0, len(members), batch_size):
                await asyncio.gather(
                    *(update_one(member) for member in members[start:start + batch_size]),
                    return_exceptions=True,
                )

    def _status_text(self, package: dict) -> str:
        used = max(int(package.get("staged_bytes", 0)), int(package.get("reserved_bytes", 0)))
        count = max(0, int(package.get("item_count", 0)))
        staged = max(0, int(package.get("staged_count", 0)))
        pending = max(0, int(package.get("pending_count", 0)))
        failed = max(0, int(package.get("failed_count", 0)))
        last = package.get("last_item_at") or package.get("created_at") or utcnow()
        idle_left = max(0, self.idle_seconds - int((utcnow() - last).total_seconds()))
        state = str(package.get("state") or "OPEN")
        if state == "OPEN":
            close_text = f"⏳ Cierre por inactividad en aproximadamente <b>{idle_left} s</b>." if pending == 0 else "⏳ El temporizador de inactividad espera a que terminen las descargas pendientes."
        elif state == "CLOSING":
            close_text = "📦 Límite alcanzado: cerrando cuando terminen las descargas pendientes."
        elif state in {"PROCESSING", "DELIVERING"}:
            close_text = "🔐 Preparando o enviando el ZIP protegido."
        elif state == "SENT_WITH_ERRORS":
            close_text = "⚠️ El ZIP se generó, pero hay entregas pendientes; el sistema volverá a intentarlo automáticamente."
        elif state == "FAILED":
            close_text = "❌ El paquete necesita revisión del administrador; los archivos locales se conservan."
        elif state == "EMPTY":
            close_text = "ℹ️ No quedaron archivos válidos en este paquete."
        else:
            close_text = "✅ Paquete procesado."
        percent = min(100, int(used * 100 / max(1, self.max_bytes)))
        return (
            "<b>📦 Paquete multimedia de la sala</b>\n\n"
            f"{progress_bar(used, self.max_bytes)} <b>{percent}%</b>\n"
            f"📊 Acumulado: <b>{format_bytes(used)}</b> / {format_bytes(self.max_bytes)}\n"
            f"📉 Falta para completar: <b>{format_bytes(max(0, self.max_bytes - used))}</b>\n"
            f"📁 En paquete: <b>{count}</b> · descargados: <b>{staged}</b> · pendientes: <b>{pending}</b> · fallidos: <b>{failed}</b>\n"
            f"{close_text}\n\n"
            "El original se borra solamente después de guardarse en Vultr. Si la descarga falla, se conserva."
        )

    def _status_markup(self, package_id: str, user_id: int):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        rows = [[InlineKeyboardButton(text="🔄 Actualizar estado", callback_data=f"pack:status:{package_id}")]]
        if user_id in self.ctx.settings.admin_ids:
            rows.append([InlineKeyboardButton(text="📦 Cerrar paquete ahora", callback_data=f"pack:close:{package_id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def status_text(self, package_id: str) -> tuple[str, dict | None]:
        package = await self.repo.get_package(self.ctx.bot_id, package_id)
        if not package:
            return "Este paquete ya no está disponible.", None
        return self._status_text(package), package

    async def close_now(self, package_id: str) -> bool:
        package = await self.repo.get_package(self.ctx.bot_id, package_id)
        if not package or package.get("state") != "OPEN":
            return False
        await self.repo.close_package(self.ctx.bot_id, package_id, "admin_manual")
        await self.schedule_progress_refresh(package_id)
        return True

    def _schedule_finalize(self, package_id: str) -> None:
        current = self._finalize_tasks.get(package_id)
        if current and not current.done():
            return
        task_registry = getattr(self.ctx, "task_registry", None)
        coro = self._finalize_package(package_id)
        task = task_registry.create(coro, f"package-finalize:{self.ctx.bot_id}:{package_id}") if task_registry else asyncio.create_task(coro)
        self._finalize_tasks[package_id] = task

    async def _finalize_package(self, package_id: str) -> None:
        try:
            await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"last_finalize_attempt_at": utcnow()})
            package = await self.repo.get_package(self.ctx.bot_id, package_id)
            if not package:
                return
            state = str(package.get("state") or "")
            if state == "CLOSING":
                if int(package.get("pending_count", 0)) > 0 or int(package.get("staged_count", 0)) == 0:
                    return
                password = secrets.token_urlsafe(15)
                safe_room_id = self._safe_path_component(str(package["room_id"]), "room")
                archive_path = self._package_dir(str(package["room_id"]), package_id) / f"room-{safe_room_id}-{package_id}.zip"
                changed = await self.repo.set_package_fields(
                    self.ctx.bot_id, package_id,
                    {"state": "PROCESSING", "password_encrypted": self.secret_box.encrypt(password), "archive_path": str(archive_path), "archive_name": archive_path.name, "processing_started_at": utcnow()},
                    expected_state="CLOSING",
                )
                if not changed:
                    package = await self.repo.get_package(self.ctx.bot_id, package_id)
                    if not package:
                        return
                package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                await self.schedule_progress_refresh(package_id)
                password = self.secret_box.decrypt(str(package["password_encrypted"]))
            else:
                if state not in {"PROCESSING", "DELIVERING", "SENT_WITH_ERRORS"}:
                    return
                password = self.secret_box.decrypt(str(package.get("password_encrypted") or ""))

            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
            if package.get("state") == "PROCESSING":
                archive_path = Path(str(package.get("archive_path") or ""))
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                items = await self.repo.get_package_items(self.ctx.bot_id, package_id)
                if not items:
                    await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "EMPTY", "completed_at": utcnow()})
                    return
                if not archive_path.is_file() or archive_path.stat().st_size == 0:
                    free = shutil.disk_usage(archive_path.parent).free
                    required = int(package.get("staged_bytes", 0)) + 64 * _MIB
                    if free < required:
                        await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "CLOSING", "last_error": "insufficient_disk_space"})
                        self.logger.error("room_package_disk_space_low bot_id=%s package_id=%s free_bytes=%s required_bytes=%s", self.ctx.bot_id, package_id, free, required)
                        if not package.get("low_disk_alerted_at"):
                            await self._notify_admin_failure(package, f"No hay espacio libre suficiente para crear el ZIP ({format_bytes(free)} libres; hacen falta aproximadamente {format_bytes(required)}). Los originales ya guardados se conservan en el paquete local.")
                            await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"low_disk_alerted_at": utcnow()})
                        return
                    async with self._archive_lock:
                        await asyncio.to_thread(self._build_encrypted_zip, archive_path, items, password)
                actual_zip_size = archive_path.stat().st_size
                max_upload = 1_950_000_000
                if actual_zip_size > max_upload:
                    await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "FAILED", "last_error": "archive_exceeds_telegram_limit", "archive_size": actual_zip_size})
                    await self.schedule_progress_refresh(package_id)
                    await self._notify_admin_failure(package, f"El ZIP quedó en {format_bytes(actual_zip_size)} y supera el límite seguro de envío.")
                    return
                targets = await self._recipient_ids(package)
                if not targets:
                    await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "FAILED", "last_error": "no_recipients"})
                    await self.schedule_progress_refresh(package_id)
                    await self._notify_admin_failure(package, "La sala no tiene destinatarios para entregar el paquete.")
                    return
                await self.repo.set_package_fields(
                    self.ctx.bot_id, package_id,
                    {"state": "DELIVERING", "archive_size": actual_zip_size, "delivery_user_ids": targets, "delivery_started_at": utcnow()},
                    expected_state="PROCESSING",
                )
                package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                await self.schedule_progress_refresh(package_id)

            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
            if package.get("state") in {"DELIVERING", "SENT_WITH_ERRORS"}:
                await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"last_delivery_attempt_at": utcnow()})
                source = await self.repo.get_delivery_source(self.ctx.bot_id, package_id)
                targets = [int(v) for v in (package.get("delivery_user_ids") or [])]
                already_sent = await self.repo.deliveries_for(self.ctx.bot_id, package_id)
                archive_path = Path(str(package.get("archive_path") or ""))
                password = self.secret_box.decrypt(str(package.get("password_encrypted") or ""))
                if source is None:
                    # Upload exactly once, then copy the stored Telegram message to
                    # every other member/admin. Retry alternate recipients if needed.
                    source = await self._upload_archive_to_first_recipient(package, targets, archive_path, password)
                    if source is None:
                        await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "SENT_WITH_ERRORS", "last_error": "archive_upload_failed", "last_delivery_attempt_at": utcnow()})
                        await self.schedule_progress_refresh(package_id)
                        return
                    already_sent = await self.repo.deliveries_for(self.ctx.bot_id, package_id)
                source_chat, source_message = source
                for user_id in targets:
                    if user_id in already_sent:
                        continue
                    try:
                        await self._pace_outbound_call()
                        async with self._delivery_lock:
                            await self.ctx.bot.copy_message(
                                chat_id=user_id, from_chat_id=source_chat, message_id=source_message,
                                protect_content=True,
                            )
                        await self.repo.mark_delivery(self.ctx.bot_id, package_id, user_id, "SENT")
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        await self.repo.mark_delivery(self.ctx.bot_id, package_id, user_id, "FAILED", type(exc).__name__)
                        self.logger.warning("room_package_delivery_failed bot_id=%s package_id=%s user_id=%s error_type=%s", self.ctx.bot_id, package_id, user_id, type(exc).__name__)
                failed = await self.repo.list_failed_deliveries(self.ctx.bot_id, package_id)
                if failed:
                    await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "SENT_WITH_ERRORS", "delivery_failed_count": len(failed), "last_delivery_attempt_at": utcnow()})
                    await self.schedule_progress_refresh(package_id)
                else:
                    await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"state": "SENT", "completed_at": utcnow(), "delivery_failed_count": 0})
                    await self._cleanup_status_messages(package)
                    source_for_cleanup = await self.repo.get_delivery_source(self.ctx.bot_id, package_id)
                    if source_for_cleanup:
                        try:
                            await self.ctx.bot.delete_message(chat_id=source_for_cleanup[0], message_id=source_for_cleanup[1])
                        except Exception as exc:
                            self.logger.info("room_package_buffer_message_cleanup_failed bot_id=%s package_id=%s error_type=%s", self.ctx.bot_id, package_id, type(exc).__name__)
                    # The encrypted archive contains a durable copy of every item.
                    # Remove local payloads only after all recipients were served.
                    await asyncio.to_thread(shutil.rmtree, self._package_dir(str(package["room_id"]), package_id), True)
                    completed_at = utcnow()
                    await self.repo.set_package_fields(
                        self.ctx.bot_id, package_id,
                        {
                            "files_cleaned": True,
                            "password_encrypted": None,
                            "archive_path": None,
                            # Successful package metadata is retained temporarily;
                            # unresolved/failed packages remain available for repair.
                            "expire_at": completed_at + timedelta(days=90),
                        },
                    )
                    await self.repo.expire_package_items_in(self.ctx.bot_id, package_id, completed_at + timedelta(days=30))
                    await self.repo.expire_deliveries_in(self.ctx.bot_id, package_id, completed_at + timedelta(days=30))
                    self.logger.info("room_package_sent bot_id=%s room_id=%s package_id=%s files=%s archive_bytes=%s recipients=%s", self.ctx.bot_id, package["room_id"], package_id, package.get("staged_count", 0), package.get("archive_size", 0), len(targets))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("room_package_finalize_failed bot_id=%s package_id=%s", self.ctx.bot_id, package_id)
        finally:
            self._finalize_tasks.pop(package_id, None)

    @staticmethod
    def _build_encrypted_zip(archive_path: Path, items: list[dict], password: str) -> None:
        import pyzipper

        archive_path = Path(archive_path)
        temp_path = archive_path.with_suffix(archive_path.suffix + ".part")
        temp_path.unlink(missing_ok=True)
        count = 0
        with pyzipper.AESZipFile(
            temp_path,
            mode="w",
            compression=pyzipper.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            archive.setpassword(password.encode("utf-8"))
            archive.setencryption(pyzipper.WZ_AES, nbits=256)
            for item in items:
                source = Path(str(item.get("staged_path") or ""))
                if not source.is_file():
                    raise FileNotFoundError("Falta un archivo ya confirmado como descargado; se abortó el ZIP para no perder contenido.")
                fallback = f"file-{count + 1}.bin"
                name = safe_archive_filename(item.get("filename"), fallback)
                arcname = f"{count + 1:05d}_{name}"
                archive.write(str(source), arcname=arcname)
                count += 1
        if count == 0:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("El paquete no contiene archivos.")
        os.replace(temp_path, archive_path)
        with archive_path.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        RoomPackageService._fsync_directory(archive_path.parent)

    async def _recipient_ids(self, package: dict) -> list[int]:
        room_id = str(package["room_id"])
        members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
        # Room packages are a delivery feature, not the optional moderation-feed
        # preference. Every configured ADMIN_IDS must receive the same ZIP even
        # when that admin has disabled the separate live feed.
        ids = {int(member["user_id"]) for member in members}
        ids.update(int(uid) for uid in self.ctx.settings.admin_ids)
        try:
            admin_ids = await self.ctx.services.admin_feed.enabled_admins()
            ids.update(int(uid) for uid in admin_ids)
        except Exception:
            self.logger.exception("room_package_admin_recipients_failed bot_id=%s package_id=%s", self.ctx.bot_id, package.get("package_id"))
        return sorted(ids)

    async def _upload_archive_to_first_recipient(self, package: dict, targets: list[int], archive_path: Path, password: str) -> tuple[int, int] | None:
        """Upload one source copy to the configured private buffer, then copy it to recipients.

        The source message itself must not be protected_content, otherwise Telegram
        may prohibit copyMessage. The buffer must therefore be private/admin-only;
        recipient copies are marked protected individually.
        """
        from aiogram.types import FSInputFile

        channel_value = str(getattr(self.ctx.settings, "storage_channel_id", "") or "").strip()
        try:
            buffer_chat_id = int(channel_value)
        except (TypeError, ValueError):
            await self._notify_admin_failure(
                package,
                "No está configurado un storage_channel_id numérico para subir el ZIP al buffer privado; verifica el canal de almacenamiento.",
            )
            return None

        caption = (
            "📦 Paquete multimedia de la sala\n"
            f"📁 Archivos: {int(package.get('staged_count', 0))}\n"
            f"📦 Tamaño: {format_bytes(int(package.get('archive_size', 0)))}\n"
            f"🔐 Contraseña AES-256: <code>{html.escape(password)}</code>\n"
            "Para extraerlo, usa 7-Zip o WinRAR."
        )
        try:
            await self._pace_outbound_call()
            async with self._delivery_lock:
                sent = await self.ctx.bot.send_document(
                    chat_id=buffer_chat_id,
                    document=FSInputFile(str(archive_path), filename=str(package.get("archive_name") or archive_path.name)),
                    caption=caption,
                    request_timeout=int(self.ctx.settings.telegram_api_timeout_seconds),
                )
            source_chat = int(sent.chat.id)
            source_message = int(sent.message_id)
            await self.repo.set_package_fields(
                self.ctx.bot_id, package["package_id"],
                {"delivery_source_chat_id": source_chat, "delivery_source_message_id": source_message, "delivery_source_chat": "storage_buffer", "state": "DELIVERING"},
            )
            return source_chat, source_message
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("room_package_buffer_upload_failed bot_id=%s package_id=%s error_type=%s", self.ctx.bot_id, package["package_id"], type(exc).__name__)
            await self._notify_admin_failure(package, "No se pudo subir el ZIP al canal buffer privado. Verifica que el bot tenga permiso para publicar en storage_channel_id.")
            return None

    async def _cleanup_status_messages(self, package: dict) -> None:
        for user_id_text, message_id in (package.get("status_message_ids") or {}).items():
            try:
                await self.ctx.bot.delete_message(chat_id=int(user_id_text), message_id=int(message_id))
            except Exception:
                pass

    async def _notify_admin_failure(self, package: dict, text: str) -> None:
        for admin_id in self.ctx.settings.admin_ids:
            try:
                await self._pace_outbound_call()
                await self.ctx.bot.send_message(int(admin_id), f"⚠️ No se pudo entregar el paquete {html.escape(str(package.get('package_id')))}: {html.escape(text)}")
            except Exception:
                pass
