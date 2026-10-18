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
from app.core.datetime import ensure_utc, utcnow

_MIB = 1024 * 1024
_MIN_VIDEOS_PER_USER_HOUR = 10
_VIDEO_QUOTA_WINDOW = timedelta(hours=1)
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
_GET_FILE_MAX_ATTEMPTS = 3
_GET_FILE_RETRY_BASE_SECONDS = 0.35


class MediaDownloadError(RuntimeError):
    """A safe, classified download error that never contains the Bot API URL/token."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        user_message: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.user_message = str(user_message or message)
        self.http_status = int(http_status) if http_status is not None else None


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
        # Coordinate card moves/cleanup across size-split packages in the same room.
        self._room_status_locks: dict[str, asyncio.Lock] = {}
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

    @staticmethod
    def _user_scope_key(room_id: str, sender_id: int) -> str:
        """Legacy per-user package lock key, retained for old package records."""
        return f"{room_id}:{int(sender_id)}"

    @staticmethod
    def _room_scope_key(room_id: str) -> str:
        """One assignment/download scope per room so members share one accumulator."""
        return f"room:{room_id}"

    async def _transfer_status_messages(self, source_package_id: str, target_package_id: str) -> None:
        """Move progress-card ownership to the room's current package, deleting extras."""
        source_package_id, target_package_id = str(source_package_id), str(target_package_id)
        if not source_package_id or source_package_id == target_package_id:
            return
        source = await self.repo.get_package(self.ctx.bot_id, source_package_id)
        target = await self.repo.get_package(self.ctx.bot_id, target_package_id)
        if not source or not target:
            return
        source_ids = {str(uid): int(mid) for uid, mid in (source.get("status_message_ids") or {}).items()}
        target_ids = {str(uid): int(mid) for uid, mid in (target.get("status_message_ids") or {}).items()}
        if not source_ids:
            return
        duplicate_messages: list[tuple[int, int]] = []
        for uid_text, message_id in source_ids.items():
            if uid_text not in target_ids:
                target_ids[uid_text] = message_id
            elif target_ids[uid_text] != message_id:
                duplicate_messages.append((int(uid_text), message_id))
        await self.repo.set_package_fields(self.ctx.bot_id, target_package_id, {"status_message_ids": target_ids})
        await self.repo.set_package_fields(self.ctx.bot_id, source_package_id, {"status_message_ids": {}})
        for user_id, message_id in duplicate_messages:
            try:
                await self.ctx.bot.delete_message(chat_id=user_id, message_id=message_id)
            except Exception:
                pass

    async def _primary_room_package(self, room_id: str) -> dict | None:
        active = await self.repo.list_active_room_packages(self.ctx.bot_id, str(room_id))
        if not active:
            return None
        open_packages = [p for p in active if str(p.get("state") or "") == "OPEN"]
        def activity_timestamp(package: dict) -> float:
            value = package.get("last_item_at") or package.get("created_at")
            normalized = ensure_utc(value) if value is not None else None
            return normalized.timestamp() if normalized is not None else 0.0

        if open_packages:
            return max(open_packages, key=activity_timestamp)
        priority = {"CLOSING": 0, "PROCESSING": 1, "DELIVERING": 2, "SENT_WITH_ERRORS": 3, "WAITING_QUOTA": 4}
        return min(
            active,
            key=lambda p: (
                priority.get(str(p.get("state") or ""), 99),
                -activity_timestamp(p),
            ),
        )

    async def _consolidate_room_status_cards(
        self, room_id: str, keep_package_id: str, *, room_status_lock_held: bool = False
    ) -> None:
        """Keep one editable progress card per member, even across size-split ZIPs."""
        if room_status_lock_held:
            await self._consolidate_room_status_cards_unlocked(room_id, keep_package_id)
            return
        async with self._lock(self._room_status_locks, str(room_id)):
            await self._consolidate_room_status_cards_unlocked(room_id, keep_package_id)

    async def _consolidate_room_status_cards_unlocked(self, room_id: str, keep_package_id: str) -> None:
        for active in await self.repo.list_active_room_packages(self.ctx.bot_id, str(room_id)):
            other_id = str(active.get("package_id") or "")
            if other_id and other_id != str(keep_package_id):
                await self._transfer_status_messages(other_id, str(keep_package_id))

    async def _mark_legacy_package_migrated(
        self, legacy: dict, target_package_id: str | None
    ) -> None:
        package_id = str(legacy.get("package_id") or "")
        fields = {
            "state": "MIGRATED",
            "package_mode": "legacy_per_user_migrated",
            "migrated_at": utcnow(),
            "expire_at": utcnow() + timedelta(days=90),
        }
        if target_package_id:
            fields["migrated_to_package_id"] = str(target_package_id)
        await self.repo.set_package_fields(self.ctx.bot_id, package_id, fields)

    async def migrate_legacy_packages(self) -> None:
        """Fold old V3 per-user batches into a shared room accumulator safely.

        Legacy item records are split across shared ZIP segments so migration never
        reserves more than the configured segment limit. Original staged paths stay
        in their legacy directory until all segments referencing that directory have
        been delivered. Progress-card IDs are transferred to the room's primary card.
        """
        legacy_packages = await self.repo.list_active_packages_for_migration(self.ctx.bot_id)
        room_ids = {str(p.get("room_id") or "") for p in legacy_packages if p.get("room_id")}
        migrated = 0
        for legacy in legacy_packages:
            source_id = str(legacy.get("package_id") or "")
            room_id = str(legacy.get("room_id") or "")
            if not source_id or not room_id or legacy.get("package_mode") == "shared_room":
                continue
            if legacy.get("sender_id") is None:
                await self.repo.set_package_fields(
                    self.ctx.bot_id, source_id,
                    {"package_mode": "shared_room", "migrated_to_shared_mode_at": utcnow()},
                )
                migrated += 1
                self.logger.info(
                    "room_package_legacy_room_package_migrated bot_id=%s room_id=%s package_id=%s",
                    self.ctx.bot_id, room_id, source_id,
                )
                continue
            if legacy.get("archive_path") or legacy.get("delivery_source_message_id"):
                # Never rewrite a ZIP whose processing/upload might be resumable.
                continue

            async with self._lock(self._assignment_locks, self._room_scope_key(room_id)):
                source_items = await self.repo.list_migratable_items(self.ctx.bot_id, source_id)
                if not source_items:
                    existing_target = await self.repo.get_open_package(self.ctx.bot_id, room_id)
                    if existing_target:
                        await self._transfer_status_messages(source_id, str(existing_target["package_id"]))
                        target_id = str(existing_target["package_id"])
                    else:
                        await self._cleanup_status_messages(legacy)
                        target_id = None
                    await self.repo.reconcile_package_counters(self.ctx.bot_id, source_id)
                    await self._mark_legacy_package_migrated(legacy, target_id)
                    migrated += 1
                    continue

                # A legacy item larger than the per-file/package limit cannot be
                # safely folded into a normal segment. Leave its source package and
                # data untouched and alert admins for explicit review.
                oversized = []
                for item in source_items:
                    status = str(item.get("status") or "")
                    size = max(0, int((item.get("staged_size") if status in {"STAGED", "STAGED_DELETE_PENDING"} else item.get("expected_size")) or 0))
                    if size > self.max_bytes:
                        oversized.append((str(item.get("item_id") or ""), size))
                if oversized:
                    self.logger.error(
                        "room_package_legacy_migration_item_exceeds_limit bot_id=%s room_id=%s package_id=%s oversized_items=%s",
                        self.ctx.bot_id, room_id, source_id, len(oversized),
                    )
                    await self._notify_admin_failure(
                        legacy,
                        "Un paquete antiguo contiene archivos que superan el límite configurado. Se conservó intacto para revisión; no se borraron los originales.",
                    )
                    continue

                target = await self.repo.get_open_package(self.ctx.bot_id, room_id)
                if target is None:
                    target = await self.repo.create_package(self.ctx.bot_id, room_id)
                target_id = str(target["package_id"])
                await self.repo.reconcile_package_counters(self.ctx.bot_id, target_id)
                target = await self.repo.get_package(self.ctx.bot_id, target_id) or target
                used_bytes = max(0, int(target.get("reserved_bytes", 0) or 0))
                item_count = max(0, int(target.get("item_count", 0) or 0))
                pending_item_ids: list[str] = []
                pending_bytes = 0
                target_ids: list[str] = []

                async def flush_pending() -> None:
                    nonlocal pending_item_ids, pending_bytes, used_bytes, item_count
                    if not pending_item_ids:
                        return
                    moved = await self.repo.move_items_to_package(
                        self.ctx.bot_id, source_id, target_id, pending_item_ids
                    )
                    await self.repo.reconcile_package_counters(self.ctx.bot_id, target_id)
                    if moved:
                        target_ids.append(target_id)
                    current_target = await self.repo.get_package(self.ctx.bot_id, target_id) or {}
                    item_count = max(0, int(current_target.get("item_count", item_count + moved) or 0))
                    used_bytes = max(0, int(current_target.get("reserved_bytes", used_bytes + pending_bytes) or 0))
                    pending_item_ids = []
                    pending_bytes = 0

                for item in source_items:
                    item_id = str(item.get("item_id") or "")
                    if not item_id:
                        continue
                    status = str(item.get("status") or "")
                    size = max(0, int((item.get("staged_size") if status in {"STAGED", "STAGED_DELETE_PENDING"} else item.get("expected_size")) or 0))
                    projected = used_bytes + pending_bytes + size
                    existing_count = item_count + len(pending_item_ids)
                    if existing_count > 0 and projected > self.max_bytes:
                        await flush_pending()
                        current_target = await self.repo.get_package(self.ctx.bot_id, target_id) or {}
                        if int(current_target.get("item_count", 0) or 0) > 0:
                            await self.repo.close_package(self.ctx.bot_id, target_id, "legacy_migration_size_split")
                            target = await self.repo.create_package(self.ctx.bot_id, room_id)
                        target_id = str(target["package_id"])
                        used_bytes = 0
                        item_count = 0
                        pending_item_ids = []
                        pending_bytes = 0
                    pending_item_ids.append(item_id)
                    pending_bytes += size

                await flush_pending()
                await self.repo.reconcile_package_counters(self.ctx.bot_id, source_id)
                for target_package_id in dict.fromkeys(target_ids):
                    await self.repo.reconcile_package_counters(self.ctx.bot_id, target_package_id)

                if target_ids:
                    status_target_id = target_ids[-1]
                    await self._transfer_status_messages(source_id, status_target_id)
                else:
                    open_target = await self.repo.get_open_package(self.ctx.bot_id, room_id)
                    status_target_id = str(open_target["package_id"]) if open_target else None
                    if status_target_id:
                        await self._transfer_status_messages(source_id, status_target_id)
                    else:
                        await self._cleanup_status_messages(legacy)
                await self._mark_legacy_package_migrated(legacy, status_target_id)
                migrated += 1
                self.logger.info(
                    "room_package_legacy_user_package_migrated bot_id=%s room_id=%s old_package_id=%s shared_package_ids=%s",
                    self.ctx.bot_id, room_id, source_id, len(set(target_ids)),
                )

        # Collapse any existing V3 status cards to the single active room card.
        for room_id in sorted(room_ids):
            primary = await self._primary_room_package(room_id)
            if primary:
                primary_id = str(primary.get("package_id") or "")
                await self._consolidate_room_status_cards(room_id, primary_id)
                await self.schedule_progress_refresh(primary_id)
        if migrated:
            self.logger.info("room_package_legacy_migration_complete bot_id=%s migrated=%s", self.ctx.bot_id, migrated)

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
        sender_id = int(message.from_user.id)
        scope_key = self._room_scope_key(str(room_id))
        item_id = hashlib.sha256(f"{self.ctx.bot_id}:{chat_id}:{message_id}".encode()).hexdigest()[:32]
        item_existing = await self.repo.get_item(self.ctx.bot_id, item_id)
        if item_existing:
            self._queue_item(item_id)
            await self.schedule_progress_refresh(str(item_existing.get("package_id") or ""))
            return True, item_id, None

        lock = self._lock(self._assignment_locks, scope_key)
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
                    # Close this ZIP segment at the safe size cap, but reuse its
                    # progress message for the next open segment in the same room.
                    await self.repo.close_package(self.ctx.bot_id, package["package_id"], "size_limit")
                    package = None
            if package is None:
                package = await self.repo.create_package(self.ctx.bot_id, room_id)
                await self._consolidate_room_status_cards(room_id, str(package["package_id"]))

            name = safe_archive_filename(filename, self._default_filename(media_type, message_id))
            item = {
                "item_id": item_id,
                "package_id": str(package["package_id"]),
                "room_id": str(room_id),
                "sender_id": sender_id,
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
        """Recover pending work and manage shared room batches and per-user eligibility."""
        while True:
            try:
                await self._recover_pending_items()
                for package in await self.repo.list_packages_to_check(self.ctx.bot_id, 100):
                    package_id = str(package.get("package_id") or "")
                    state = str(package.get("state") or "")
                    if not package_id:
                        continue
                    room_id = str(package.get("room_id") or "")
                    sender_id = package.get("sender_id")
                    shared_room = package.get("package_mode") == "shared_room"

                    last_sync = self._last_counters_reconcile.get(package_id, 0.0)
                    if time.monotonic() - last_sync >= 12.0 and state not in {"SENT", "FAILED", "EMPTY", "MIGRATED"}:
                        scope_key = self._room_scope_key(room_id) if shared_room else (
                            self._user_scope_key(room_id, int(sender_id)) if sender_id is not None else self._room_scope_key(room_id)
                        )
                        async with self._lock(self._assignment_locks, scope_key):
                            await self.repo.reconcile_package_counters(self.ctx.bot_id, package_id)
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or state)
                            self._last_counters_reconcile[package_id] = time.monotonic()

                    eligible_user_ids: list[int] = []
                    recent_video_count = None
                    if shared_room:
                        counts = await self.repo.recent_user_video_counts(
                            self.ctx.bot_id, room_id, utcnow() - _VIDEO_QUOTA_WINDOW
                        )
                        eligible_user_ids = sorted(uid for uid, count in counts.items() if count >= _MIN_VIDEOS_PER_USER_HOUR)
                        package["recent_eligible_user_ids"] = eligible_user_ids
                        package["recent_user_video_counts"] = counts
                    elif sender_id is not None:
                        recent_video_count = await self.repo.count_recent_user_videos(
                            self.ctx.bot_id, room_id, int(sender_id), utcnow() - _VIDEO_QUOTA_WINDOW
                        )
                        package["recent_video_count"] = recent_video_count
                        if recent_video_count >= _MIN_VIDEOS_PER_USER_HOUR and not package.get("quota_met_at"):
                            await self.repo.set_package_fields(
                                self.ctx.bot_id, package_id, {"quota_met_at": utcnow(), "quota_video_count": recent_video_count}
                            )
                            package["quota_met_at"] = utcnow()
                            await self.schedule_progress_refresh(package_id)

                    already_eligible = [int(uid) for uid in package.get("eligible_user_ids", [])]
                    quota_met = bool(already_eligible or eligible_user_ids) if shared_room else (
                        sender_id is None or bool(package.get("quota_met_at"))
                    )
                    if state == "OPEN" and int(package.get("pending_count", 0)) == 0:
                        last = package.get("last_item_at") or package.get("created_at") or utcnow()
                        idle_for = (utcnow() - last).total_seconds()
                        if int(package.get("item_count", 0)) > 0 and idle_for >= self.idle_seconds and quota_met:
                            snapshot = eligible_user_ids if shared_room else None
                            await self.repo.close_package(
                                self.ctx.bot_id, package_id, "idle_timeout", eligible_user_ids=snapshot
                            )
                            await self.schedule_progress_refresh(package_id)
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or "")
                        elif int(package.get("item_count", 0)) <= 0 and idle_for >= self.idle_seconds:
                            # Empty/duplicate-only collections should not stay open forever.
                            await self.repo.set_package_fields(
                                self.ctx.bot_id, package_id,
                                {"state": "EMPTY", "completed_at": utcnow(), "last_error": "no_staged_items"},
                                expected_state="OPEN",
                            )
                            await self.schedule_progress_refresh(package_id)
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or "")
                        elif int(package.get("item_count", 0)) > 0 and idle_for >= self.idle_seconds and not quota_met:
                            # Keep the shared accumulator open until at least one member qualifies.
                            if not package.get("quota_wait_notice_at"):
                                await self.repo.set_package_fields(
                                    self.ctx.bot_id, package_id, {"quota_wait_notice_at": utcnow()}
                                )
                            await self.schedule_progress_refresh(package_id)

                    if state == "CLOSING" and int(package.get("pending_count", 0)) <= 0 and int(package.get("staged_count", 0)) > 0:
                        if shared_room and package.get("close_reason") != "admin_manual" and not package.get("eligible_user_ids"):
                            if eligible_user_ids:
                                await self.repo.set_package_fields(
                                    self.ctx.bot_id, package_id, {"eligible_user_ids": eligible_user_ids}
                                )
                                package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            else:
                                await self.repo.set_package_fields(
                                    self.ctx.bot_id, package_id,
                                    {"state": "WAITING_QUOTA", "waiting_quota_since": utcnow()},
                                    expected_state="CLOSING",
                                )
                                package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                                state = str(package.get("state") or "")
                                await self.schedule_progress_refresh(package_id)
                        elif not shared_room and sender_id is not None and not quota_met:
                            await self.repo.set_package_fields(
                                self.ctx.bot_id, package_id,
                                {"state": "WAITING_QUOTA", "waiting_quota_since": utcnow()},
                                expected_state="CLOSING",
                            )
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or "")
                            await self.schedule_progress_refresh(package_id)

                    if state == "WAITING_QUOTA" and int(package.get("pending_count", 0)) <= 0 and int(package.get("staged_count", 0)) > 0:
                        if shared_room and eligible_user_ids:
                            await self.repo.set_package_fields(
                                self.ctx.bot_id, package_id,
                                {"state": "CLOSING", "eligible_user_ids": eligible_user_ids,
                                 "quota_met_at": utcnow(), "close_reason": package.get("close_reason") or "room_member_hourly_quota"},
                                expected_state="WAITING_QUOTA",
                            )
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or "")
                            await self.schedule_progress_refresh(package_id)
                        elif not shared_room and quota_met:
                            await self.repo.set_package_fields(
                                self.ctx.bot_id, package_id,
                                {"state": "CLOSING", "quota_met_at": package.get("quota_met_at") or utcnow(), "close_reason": "user_hourly_quota"},
                                expected_state="WAITING_QUOTA",
                            )
                            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                            state = str(package.get("state") or "")
                            await self.schedule_progress_refresh(package_id)

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
                            self._schedule_finalize(package_id)
                        elif state in {"DELIVERING", "SENT_WITH_ERRORS"}:
                            last_attempt = package.get("last_delivery_attempt_at")
                            if last_attempt and (utcnow() - last_attempt).total_seconds() < 300:
                                continue
                            self._schedule_finalize(package_id)
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
        if item.get("status") in {"STAGED", "FAILED", "DUPLICATE"}:
            return
        room_id = str(item.get("room_id") or "")
        sender_id = int(item.get("sender_id", 0) or 0)
        scope_key = self._room_scope_key(room_id)
        async with self._lock(self._download_locks, scope_key):
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
                content_sha256 = None
                if str(item.get("media_type") or "") == "video":
                    content_sha256 = await asyncio.to_thread(self._sha256_file, incoming_path)
                    duplicate = await self.repo.find_staged_duplicate(
                        self.ctx.bot_id, room_id, sender_id, content_sha256
                    )
                    if duplicate:
                        duplicate_package = await self.repo.get_package(
                            self.ctx.bot_id, str(duplicate.get("package_id") or "")
                        )
                        duplicate_path = Path(str(duplicate.get("staged_path") or ""))
                        durable_duplicate = bool(
                            duplicate_path.is_file()
                            and duplicate_path.stat().st_size == int(duplicate.get("staged_size", -1))
                        ) or bool(duplicate_package and (
                            duplicate_package.get("state") == "SENT"
                            or duplicate_package.get("delivery_source_message_id")
                            or (duplicate_package.get("archive_path") and Path(str(duplicate_package["archive_path"])).is_file())
                        ))
                        if durable_duplicate:
                            async with self._lock(self._assignment_locks, scope_key):
                                duplicate_marked = await self.repo.mark_item_duplicate(
                                    self.ctx.bot_id, item, str(duplicate["item_id"]), content_sha256
                                )
                            if duplicate_marked:
                                incoming_path.unlink(missing_ok=True)
                                duplicate_item = await self.repo.get_item(self.ctx.bot_id, item_id) or item
                                await self._delete_source_after_duplicate(duplicate_item)
                                await self.schedule_progress_refresh(str(item.get("package_id") or ""))
                                self.logger.info(
                                    "room_package_duplicate_video_omitted bot_id=%s room_id=%s sender_id=%s item_id=%s duplicate_of=%s",
                                    self.ctx.bot_id, room_id, sender_id, item_id, duplicate.get("item_id"),
                                )
                                return
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
                async with self._lock(self._assignment_locks, scope_key):
                    staged_ok = await self.repo.mark_staged(self.ctx.bot_id, item, str(target_path), actual_size, content_sha256)
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
                safe_error = self._safe_download_error(exc)
                if current and current.get("status") in {"QUEUED", "DOWNLOADING"}:
                    async with self._lock(self._assignment_locks, scope_key):
                        await self.repo.mark_item_failed(self.ctx.bot_id, current, safe_error)
                    await self._notify_source_failure(item, exc)
                    await self.schedule_progress_refresh(str(current.get("package_id") or ""))
                elif not current or current.get("status") not in {"STAGED", "STAGED_DELETE_PENDING"}:
                    await self._notify_source_failure(item, exc)
                self.logger.warning(
                    "room_package_item_failed bot_id=%s room_id=%s item_id=%s media_type=%s error_type=%s error_code=%s http_status=%s detail=%s",
                    self.ctx.bot_id, room_id, item_id, item.get("media_type"), type(exc).__name__,
                    getattr(exc, "code", "download_or_stage_failed"), getattr(exc, "http_status", None), safe_error[:180],
                )

    def _safe_download_error(self, exc: Exception) -> str:
        """Return a short persisted/logged detail without leaking tokens or request URLs."""
        if isinstance(exc, MediaDownloadError):
            detail = f"{exc.code}: {exc}"
        elif isinstance(exc, httpx.HTTPError):
            # HTTPX exceptions include the full URL, which contains the bot token.
            detail = f"httpx_{type(exc).__name__}"
        elif isinstance(exc, FileNotFoundError):
            detail = "local_file_not_found_or_not_readable"
        elif isinstance(exc, PermissionError):
            detail = "local_file_permission_denied"
        elif isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
            detail = "insufficient_disk_space"
        elif isinstance(exc, OSError):
            # Filesystem exception strings often include the source path, which in
            # local Bot API mode may contain an old token-named data directory.
            detail = f"filesystem_io_error_errno={getattr(exc, 'errno', None)}"
        else:
            detail = f"{type(exc).__name__}: {exc}"
        token = str(getattr(self.ctx.bot, "token", "") or "").strip()
        if token:
            detail = detail.replace(token, "[bot-token-redacted]")
        detail = re.sub(r"https?://\S+", "[url-redacted]", detail)
        return detail[:360]

    def _telegram_getfile_error(self, status: int, payload: dict, response_text: str = "") -> MediaDownloadError:
        """Translate Telegram's JSON error body before handling HTTP status.

        Calling response.raise_for_status() first discards the useful Bot API
        description and leaves only a generic HTTPStatusError in item logs.
        """
        description = str(payload.get("description") or response_text or "sin descripción")
        token = str(getattr(self.ctx.bot, "token", "") or "").strip()
        if token:
            description = description.replace(token, "[bot-token-redacted]")
        description = re.sub(r"https?://\S+", "[url-redacted]", description).strip()[:220]
        lower = description.lower()
        api_code = payload.get("error_code", status)
        if "file is too big" in lower:
            if bool(self.ctx.settings.telegram_api_local_mode):
                return MediaDownloadError(
                    "Telegram rechazó getFile por tamaño; comprobar que el servidor Bot API se inició realmente con --local.",
                    code="telegram_file_too_large_local_mode_check",
                    user_message="Telegram rechazó este archivo por su tamaño. El original se conserva. El administrador debe verificar que el Bot API local se ejecute con --local y que el límite de descarga esté configurado correctamente.",
                    http_status=status,
                )
            return MediaDownloadError(
                "Telegram rechazó getFile por el límite de tamaño del Bot API alojado.",
                code="telegram_file_too_large_cloud_api",
                user_message="Este archivo supera el límite de descarga del Bot API alojado. El original se conserva; avisá al administrador.",
                http_status=status,
            )
        if "wrong file identifier" in lower or "file identifier" in lower or "file_id" in lower:
            return MediaDownloadError(
                f"Telegram rechazó el identificador del archivo (HTTP {status}, código {api_code}): {description}",
                code="telegram_file_id_rejected",
                user_message="Telegram no reconoce el identificador de este archivo para este bot. El original se conserva. Probá reenviar el vídeo o archivo; si vuelve a ocurrir, avisá al administrador.",
                http_status=status,
            )
        return MediaDownloadError(
            f"Telegram rechazó getFile (HTTP {status}, código {api_code}): {description}",
            code="telegram_getfile_rejected",
            user_message="Telegram rechazó la descarga de este archivo. El original se conserva y no se añadió al ZIP. Probá reenviarlo; si vuelve a fallar, avisá al administrador.",
            http_status=status,
        )

    async def _get_file_metadata(self, url: str, file_id: str, timeout: int) -> dict:
        """Request getFile with bounded retries and retain Telegram's exact safe error."""
        request_timeout = min(max(30, int(timeout)), 120)
        last_transport_error: Exception | None = None
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            for attempt in range(_GET_FILE_MAX_ATTEMPTS):
                try:
                    response = await client.post(url, json={"file_id": str(file_id)})
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_transport_error = exc
                    if attempt + 1 < _GET_FILE_MAX_ATTEMPTS:
                        await asyncio.sleep(_GET_FILE_RETRY_BASE_SECONDS * (2 ** attempt))
                        continue
                    raise MediaDownloadError(
                        "No se pudo contactar con el Bot API después de varios intentos.",
                        code="bot_api_transport_exhausted",
                        user_message="El servicio de Telegram no respondió después de varios intentos. El original se conserva; esperá un momento y volvé a enviarlo.",
                    ) from None

                try:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        payload = {}
                except (ValueError, TypeError):
                    payload = {}
                api_code = payload.get("error_code", response.status_code)
                try:
                    api_code_int = int(api_code or 0)
                except (TypeError, ValueError):
                    api_code_int = 0
                retryable = response.status_code in {408, 425, 429, 500, 502, 503, 504} or api_code_int in {408, 425, 429, 500, 502, 503, 504}
                if retryable and attempt + 1 < _GET_FILE_MAX_ATTEMPTS:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = _GET_FILE_RETRY_BASE_SECONDS * (2 ** attempt)
                    await asyncio.sleep(max(0.0, min(delay, 4.0)))
                    continue

                if response.status_code >= 400 or not payload.get("ok"):
                    if retryable:
                        description = str(payload.get("description") or "servicio temporalmente no disponible")[:160]
                        raise MediaDownloadError(
                            f"Bot API temporalmente no disponible después de {_GET_FILE_MAX_ATTEMPTS} intentos (HTTP {response.status_code}): {description}",
                            code="bot_api_temporary_error_exhausted",
                            user_message="Telegram tuvo un problema temporal al descargar este archivo. Se hicieron varios intentos y el original se conserva; probá reenviarlo en unos minutos.",
                            http_status=response.status_code,
                        )
                    try:
                        response_text = response.text
                    except Exception:
                        response_text = ""
                    try:
                        api_error_status = int(api_code or response.status_code)
                    except (TypeError, ValueError):
                        api_error_status = response.status_code
                    error_status = response.status_code if response.status_code >= 400 else api_error_status
                    raise self._telegram_getfile_error(error_status, payload, response_text)

                result = payload.get("result")
                if not isinstance(result, dict):
                    raise MediaDownloadError(
                        "La respuesta de getFile no contiene result válido.",
                        code="bot_api_invalid_getfile_response",
                        user_message="Telegram devolvió una respuesta de descarga incompleta. El original se conserva; probá reenviar el archivo.",
                        http_status=response.status_code,
                    )
                return payload
        # Kept as a defensive guard in case the retry loop is edited later.
        raise MediaDownloadError(
            f"No se pudo consultar getFile ({type(last_transport_error).__name__ if last_transport_error else 'unknown'}).",
            code="bot_api_getfile_failed",
            user_message="No se pudo consultar el archivo en Telegram. El original se conserva; probá reenviarlo.",
        )

    async def _resolve_local_api_file_with_retry(self, file_path: str) -> Path:
        """Allow short filesystem propagation delays after local Bot API getFile."""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await asyncio.to_thread(self._resolve_local_api_file, file_path)
            except FileNotFoundError as exc:
                last_error = exc
                if "otro bot" in str(exc).lower() or attempt == 2:
                    break
                await asyncio.sleep(_GET_FILE_RETRY_BASE_SECONDS * (attempt + 1))
        raise MediaDownloadError(
            "Telegram devolvió file_path, pero el archivo no apareció en el volumen compartido tras tres comprobaciones.",
            code="local_bot_api_file_missing",
            user_message="Telegram identificó el archivo, pero Vultr no encontró su copia en el volumen compartido. El original se conserva y se avisó al administrador; probá reenviarlo si persiste.",
        ) from last_error

    async def _download_to_local(self, item: dict, destination: Path) -> int:
        """Download through the configured Bot API with retries and safe diagnostics.

        Local mode maps the Bot API server file path to the read-only host mount;
        cloud mode downloads through Aiogram's HTTP file endpoint.
        """
        base = str(self.ctx.settings.telegram_api_base_url).rstrip("/")
        token = str(getattr(self.ctx.bot, "token", "") or "").strip()
        if not token:
            raise MediaDownloadError(
                "El bot no tiene token disponible para descargar el archivo.",
                code="bot_token_missing",
                user_message="El servicio de descarga de este bot no está configurado correctamente. El original se conserva; avisá al administrador.",
            )
        url = f"{base}/bot{quote(token, safe=':')}/getFile"
        timeout = max(30, int(getattr(self.ctx.settings, "telegram_api_timeout_seconds", 7200)))
        payload = await self._get_file_metadata(url, str(item["file_id"]), timeout)
        file_path = str(payload["result"].get("file_path") or "")
        if not file_path:
            raise MediaDownloadError(
                "Telegram no devolvió la ruta del archivo.",
                code="bot_api_file_path_missing",
                user_message="Telegram no devolvió la ubicación del archivo. El original se conserva; probá reenviarlo.",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected = int(payload["result"].get("file_size") or item.get("expected_size") or 0)
        if expected > self.max_bytes:
            raise MediaDownloadError(
                f"El archivo supera el máximo del paquete ({format_bytes(self.max_bytes)}).",
                code="file_exceeds_package_limit",
                user_message=f"Este archivo supera el máximo permitido por archivo ({format_bytes(self.max_bytes)}). El original se conserva.",
            )
        # Reserve disk space across all child bots before starting large copies.
        # ZIP creation later needs a second copy of roughly the same size.
        reservation = max(expected, 32 * _MIB)
        await self._reserve_disk_space(reservation)
        try:
            if bool(self.ctx.settings.telegram_api_local_mode):
                source_path = await self._resolve_local_api_file_with_retry(file_path)
                actual_source_size = await asyncio.to_thread(lambda: source_path.stat().st_size)
                if actual_source_size > self.max_bytes:
                    raise MediaDownloadError(
                        f"El archivo supera el máximo del paquete ({format_bytes(self.max_bytes)}).",
                        code="file_exceeds_package_limit",
                        user_message=f"Este archivo supera el máximo permitido por archivo ({format_bytes(self.max_bytes)}). El original se conserva.",
                    )
                if actual_source_size > reservation:
                    # Update the reservation if the Bot API did not report a size.
                    await self._increase_disk_reservation(actual_source_size - reservation)
                    reservation = actual_source_size
                await asyncio.to_thread(self._copy_durable, source_path, destination)
            else:
                # In cloud mode the standard Bot API requires downloadFile; use the
                # configured Aiogram session, which is not wrapped in local mode.
                try:
                    await self.ctx.bot.download_file(file_path, destination=destination, timeout=timeout)
                except Exception as exc:
                    raise MediaDownloadError(
                        f"La descarga HTTP de Telegram falló ({type(exc).__name__}).",
                        code="telegram_file_download_failed",
                        user_message="Telegram rechazó o interrumpió la descarga. El original se conserva; probá reenviarlo.",
                    ) from None
                # Aiogram closes the file after download, but fsync makes the staged
                # data durable before we record it and delete the user's message.
                await asyncio.to_thread(self._fsync_file, destination)
            actual = destination.stat().st_size
            if actual <= 0:
                raise MediaDownloadError(
                    "El archivo descargado está vacío.",
                    code="empty_download",
                    user_message="La descarga llegó vacía o incompleta. El original se conserva; probá reenviarlo.",
                )
            if actual > self.max_bytes:
                raise MediaDownloadError(
                    f"El archivo supera el máximo del paquete ({format_bytes(self.max_bytes)}).",
                    code="file_exceeds_package_limit",
                    user_message=f"Este archivo supera el máximo permitido por archivo ({format_bytes(self.max_bytes)}). El original se conserva.",
                )
            if expected and actual != expected:
                raise MediaDownloadError(
                    f"El tamaño descargado no coincide con el informado por Telegram ({actual} != {expected}).",
                    code="download_size_mismatch",
                    user_message="La descarga llegó incompleta o con un tamaño inconsistente. El original se conserva; probá reenviarlo.",
                )
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
        scope_key = self._room_scope_key(room_id)
        async with self._lock(self._assignment_locks, scope_key):
            current = await self.repo.get_item(self.ctx.bot_id, str(item["item_id"])) or item
            package = await self.repo.get_package(self.ctx.bot_id, str(current["package_id"]))
            if not package:
                package = await self.repo.create_package(self.ctx.bot_id, room_id)
                await self.repo.update_item_package(self.ctx.bot_id, str(current["item_id"]), str(package["package_id"]), actual_size)
                current["package_id"] = str(package["package_id"])
                current["expected_size"] = actual_size
                # Recover counters for an orphaned queue item and reuse the room's
                # one progress card instead of leaving a card on a stale package.
                await self.ctx.repositories.packages.packages.update_one(
                    {"bot_id": self.ctx.bot_id, "package_id": current["package_id"]},
                    {"$inc": {"item_count": 1, "pending_count": 1, "reserved_bytes": actual_size}},
                )
                await self._consolidate_room_status_cards(room_id, str(package["package_id"]))
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
                await self._consolidate_room_status_cards(room_id, str(new_package["package_id"]))
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
            scope_key = self._room_scope_key(str(current.get("room_id") or ""))
            async with self._lock(self._assignment_locks, scope_key):
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

    async def _delete_source_after_duplicate(self, item: dict) -> None:
        """Delete an exact duplicate only after both its hash and durable prior copy are recorded."""
        current = await self.repo.get_item(self.ctx.bot_id, str(item.get("item_id") or ""))
        if not current or current.get("status") != "DUPLICATE" or not current.get("duplicate_of_item_id"):
            return
        try:
            await self.ctx.bot.delete_message(
                chat_id=int(current["original_chat_id"]),
                message_id=int(current["original_message_id"]),
            )
            await self.repo.mark_duplicate_source_deleted(self.ctx.bot_id, current["item_id"], deleted=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.repo.mark_duplicate_source_deleted(
                self.ctx.bot_id, current["item_id"], deleted=False, error=type(exc).__name__
            )
            try:
                await self.ctx.bot.send_message(
                    chat_id=int(current["sender_id"]),
                    text="ℹ️ Este vídeo ya está guardado en tu paquete y no se añadirá dos veces, pero Telegram no permitió borrar el mensaje duplicado.",
                )
            except Exception:
                pass

    async def _notify_source_failure(self, item: dict, error: Exception | str) -> None:
        # The original remains visible until the download is fully staged.
        if isinstance(error, MediaDownloadError):
            reason = error.user_message
        elif isinstance(error, OSError) and getattr(error, "errno", None) == 28:
            reason = "No hay espacio libre suficiente en Vultr. El original se conserva; avisá al administrador."
        elif isinstance(error, PermissionError):
            reason = "Vultr no tiene permiso para leer o guardar este archivo. El original se conserva; avisá al administrador."
        elif isinstance(error, FileNotFoundError):
            reason = "No se encontró la copia local del archivo en el Bot API. El original se conserva; probá reenviarlo y avisá al administrador si persiste."
        else:
            reason = "Ocurrió un error al descargar el archivo. El original se conserva; probá reenviarlo. Si vuelve a fallar, avisá al administrador."
        message = f"❌ No pude descargar y guardar este archivo en Vultr. {reason} No se borró ni añadió al ZIP."
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
        if package.get("package_mode") == "shared_room":
            room_id = str(package.get("room_id") or "")
            async with self._lock(self._room_status_locks, room_id):
                await self._refresh_progress_all_impl(package_id, room_status_lock_held=True)
        else:
            await self._refresh_progress_all_impl(package_id, room_status_lock_held=False)

    async def _refresh_progress_all_impl(self, package_id: str, *, room_status_lock_held: bool) -> None:
        package = await self.repo.get_package(self.ctx.bot_id, package_id)
        if not package:
            return
        lock = self._lock(self._status_locks, package_id)
        async with lock:
            package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
            room_id = str(package["room_id"])
            shared_room = package.get("package_mode") == "shared_room"
            if shared_room:
                state = str(package.get("state") or "")
                if state not in {"OPEN", "CLOSING", "WAITING_QUOTA", "PROCESSING", "DELIVERING", "SENT_WITH_ERRORS"}:
                    await self._cleanup_status_messages(package, room_status_lock_held=room_status_lock_held)
                    await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"status_message_ids": {}})
                    return
                primary = await self._primary_room_package(room_id)
                if primary:
                    primary_id = str(primary.get("package_id") or "")
                    if primary_id and primary_id != package_id:
                        await self._transfer_status_messages(package_id, primary_id)
                        await self.schedule_progress_refresh(primary_id)
                        return
                    await self._consolidate_room_status_cards(room_id, package_id, room_status_lock_held=room_status_lock_held)
                    package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
            member_ids: set[int] = set()
            if shared_room:
                members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
                member_ids = {int(member["user_id"]) for member in members}
                # One aggregate query supplies individualized quota counts for every
                # member; do not issue one MongoDB aggregation per recipient.
                counts = await self.repo.recent_user_video_counts(
                    self.ctx.bot_id, room_id, utcnow() - _VIDEO_QUOTA_WINDOW
                )
                package["recent_user_video_counts"] = counts
            elif package.get("sender_id") is not None:
                package["recent_video_count"] = await self.repo.count_recent_user_videos(
                    self.ctx.bot_id, room_id, int(package["sender_id"]), utcnow() - _VIDEO_QUOTA_WINDOW
                )
                package["recent_video_quota"] = _MIN_VIDEOS_PER_USER_HOUR
            if package.get("sender_id") is not None:
                recipients = {int(package["sender_id"])}
            else:
                if not shared_room:
                    # Unknown old room packages preserve their member status audience.
                    members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
                    member_ids = {int(member["user_id"]) for member in members}
                recipients = set(member_ids)
            recipients.update(int(uid) for uid in self.ctx.settings.admin_ids)
            try:
                recipients.update(int(uid) for uid in await self.ctx.services.admin_feed.enabled_admins())
            except Exception:
                self.logger.debug("room_package_status_admin_recipients_unavailable bot_id=%s", self.ctx.bot_id)
            semaphore = asyncio.Semaphore(2)

            async def update_one(uid: int) -> None:
                async with semaphore, _GLOBAL_STATUS_SEMAPHORE:
                    viewer_package = dict(package)
                    if shared_room:
                        viewer_package["recent_video_count"] = int(
                            (package.get("recent_user_video_counts") or {}).get(uid, 0)
                        )
                        viewer_package["viewer_user_id"] = uid
                    text = self._status_text(viewer_package)
                    old_id = (package.get("status_message_ids") or {}).get(str(uid))
                    markup = self._status_markup(package_id, uid)
                    replace_old_id: int | None = None
                    if old_id:
                        try:
                            await self._pace_outbound_call()
                            await asyncio.wait_for(
                                self.ctx.bot.edit_message_text(chat_id=uid, message_id=int(old_id), text=text, reply_markup=markup),
                                timeout=20,
                            )
                            return
                        except Exception as exc:
                            error_text = str(exc).lower()
                            if "message is not modified" in error_text:
                                return
                            if self._is_stale_status_message_error(exc):
                                # Replace only when Telegram confirms the old card is
                                # missing/uneditable. For transient/network failures, do
                                # not send another card: the next refresh will retry edit.
                                replace_old_id = int(old_id)
                                self.logger.info(
                                    "room_package_status_card_stale bot_id=%s room_id=%s user_id=%s old_message_id=%s error_type=%s",
                                    self.ctx.bot_id, room_id, uid, replace_old_id, type(exc).__name__,
                                )
                            else:
                                self.logger.warning(
                                    "room_package_status_edit_failed bot_id=%s room_id=%s package_id=%s user_id=%s message_id=%s error_type=%s",
                                    self.ctx.bot_id, room_id, package_id, uid, int(old_id), type(exc).__name__,
                                )
                                return
                    try:
                        await self._pace_outbound_call()
                        sent = await asyncio.wait_for(self.ctx.bot.send_message(uid, text, reply_markup=markup), timeout=20)
                        # Persist the replacement ID before attempting cleanup. This way,
                        # a deletion failure cannot make later refreshes create yet more cards.
                        await self.repo.set_status_message(self.ctx.bot_id, package_id, uid, int(sent.message_id))
                        # Pin the room's shared progress card in member chats. Pinning is
                        # best-effort and a rejection never blocks downloads or delivery.
                        should_pin = (shared_room and uid in member_ids) or (
                            package.get("sender_id") is not None and uid == int(package["sender_id"])
                        )
                        if should_pin:
                            try:
                                await self._pace_outbound_call()
                                await asyncio.wait_for(
                                    self.ctx.bot.pin_chat_message(
                                        chat_id=uid,
                                        message_id=int(sent.message_id),
                                        disable_notification=True,
                                    ),
                                    timeout=15,
                                )
                            except Exception as pin_exc:
                                self.logger.debug(
                                    "room_package_status_pin_failed bot_id=%s package_id=%s user_id=%s error_type=%s",
                                    self.ctx.bot_id, package_id, uid, type(pin_exc).__name__,
                                )
                        if replace_old_id is not None:
                            # Replace an obsolete pinned card; never leave the previous
                            # card as a second visible status message.
                            if should_pin:
                                try:
                                    await self._pace_outbound_call()
                                    await asyncio.wait_for(
                                        self.ctx.bot.unpin_chat_message(chat_id=uid, message_id=replace_old_id),
                                        timeout=15,
                                    )
                                except Exception:
                                    pass
                            try:
                                await self._pace_outbound_call()
                                await asyncio.wait_for(
                                    self.ctx.bot.delete_message(chat_id=uid, message_id=replace_old_id),
                                    timeout=15,
                                )
                            except Exception as delete_exc:
                                self.logger.warning(
                                    "room_package_status_old_card_cleanup_failed bot_id=%s room_id=%s user_id=%s old_message_id=%s error_type=%s",
                                    self.ctx.bot_id, room_id, uid, replace_old_id, type(delete_exc).__name__,
                                )
                    except Exception as exc:
                        self.logger.debug("room_package_status_send_failed bot_id=%s room_id=%s user_id=%s error_type=%s", self.ctx.bot_id, room_id, uid, type(exc).__name__)

            batch_size = 50
            recipient_list = sorted(recipients)
            for start in range(0, len(recipient_list), batch_size):
                batch = recipient_list[start:start + batch_size]
                await asyncio.gather(*(update_one(uid) for uid in batch), return_exceptions=True)

    @staticmethod
    def _is_stale_status_message_error(exc: Exception) -> bool:
        """True only for Telegram errors that justify replacing the existing card.

        Any other edit failure is treated as transient and does not create a second
        progress message, preventing duplicate cards during rate limits/timeouts.
        """
        text = str(exc).lower()
        stale_markers = (
            "message to edit not found",
            "message_id_invalid",
            "message identifier is not specified",
            "message can't be edited",
            "message cannot be edited",
            "message to unpin not found",
        )
        return any(marker in text for marker in stale_markers)

    def _status_text(self, package: dict) -> str:
        used = max(int(package.get("staged_bytes", 0)), int(package.get("reserved_bytes", 0)))
        count = max(0, int(package.get("item_count", 0)))
        staged = max(0, int(package.get("staged_count", 0)))
        pending = max(0, int(package.get("pending_count", 0)))
        failed = max(0, int(package.get("failed_count", 0)))
        last = package.get("last_item_at") or package.get("created_at") or utcnow()
        idle_left = max(0, self.idle_seconds - int((utcnow() - last).total_seconds()))
        state = str(package.get("state") or "OPEN")
        shared_room = package.get("package_mode") == "shared_room"
        recent_video_count = package.get("recent_video_count")
        quota_line = ""
        if shared_room or package.get("sender_id") is not None:
            recent_video_count = max(0, int(recent_video_count or 0))
            quota_met = recent_video_count >= _MIN_VIDEOS_PER_USER_HOUR
            remaining_videos = max(0, _MIN_VIDEOS_PER_USER_HOUR - recent_video_count)
            quota_line = (
                f"🎬 Tus vídeos únicos válidos en la última hora: <b>{min(recent_video_count, _MIN_VIDEOS_PER_USER_HOUR)}/{_MIN_VIDEOS_PER_USER_HOUR}</b>\n"
                + ("✅ Ya cumpliste el mínimo para recibir el ZIP común de la sala.\n" if quota_met else f"📌 Te faltan <b>{remaining_videos}</b> vídeo(s) válido(s) para poder recibirlo.\n")
            )
        if state == "OPEN":
            if shared_room:
                if pending > 0:
                    close_text = "⏳ Se están guardando archivos de todos los miembros; el cierre espera a que terminen las descargas."
                elif not package.get("recent_eligible_user_ids") and not package.get("eligible_user_ids"):
                    close_text = f"⏳ La sala sigue acumulando. Se podrá cerrar tras {self.idle_seconds} s sin actividad cuando al menos un miembro cumpla su cuota personal."
                else:
                    close_text = f"⏳ Cierre por inactividad en aproximadamente <b>{idle_left} s</b>; se incluirá la multimedia de todos los miembros."
            elif package.get("sender_id") is not None and not (bool(package.get("quota_met_at")) or int(recent_video_count or 0) >= _MIN_VIDEOS_PER_USER_HOUR):
                close_text = ("⏳ Esperando a que llegues al mínimo de vídeos únicos para habilitar la entrega." if pending == 0
                              else "⏳ Esperando que terminen las descargas; el mínimo se cuenta solo con tus vídeos guardados.")
            else:
                close_text = f"⏳ Cierre por inactividad en aproximadamente <b>{idle_left} s</b>." if pending == 0 else "⏳ El temporizador de inactividad espera a que terminen las descargas pendientes."
        elif state == "WAITING_QUOTA":
            close_text = "⏳ El ZIP común contiene el contenido ya guardado de todos los miembros y espera a que al menos uno cumpla 10 vídeos válidos en la última hora."
        elif state == "CLOSING":
            close_text = "📦 Tanda cerrándose cuando terminen las descargas pendientes."
        elif state in {"PROCESSING", "DELIVERING"}:
            close_text = "🔐 Preparando o enviando el ZIP protegido de la sala."
        elif state == "SENT_WITH_ERRORS":
            close_text = "⚠️ El ZIP se generó, pero hay entregas pendientes; el sistema volverá a intentarlo automáticamente."
        elif state == "FAILED":
            close_text = "❌ El paquete necesita revisión del administrador; los archivos locales se conservan."
        elif state == "EMPTY":
            close_text = "ℹ️ No quedaron archivos válidos en este paquete."
        else:
            close_text = "✅ Paquete procesado."
        percent = min(100, int(used * 100 / max(1, self.max_bytes)))
        shared_note = "👥 El ZIP acumula contenido único de todos los miembros de esta sala.\n" if shared_room else ""
        return (
            "<b>📦 Paquete multimedia de la sala</b>\n\n"
            f"{shared_note}"
            f"{progress_bar(used, self.max_bytes)} <b>{percent}%</b>\n"
            f"📊 Acumulado: <b>{format_bytes(used)}</b> / {format_bytes(self.max_bytes)}\n"
            f"📉 Falta para completar: <b>{format_bytes(max(0, self.max_bytes - used))}</b>\n"
            f"📁 En paquete: <b>{count}</b> · descargados: <b>{staged}</b> · pendientes: <b>{pending}</b> · fallidos: <b>{failed}</b> · duplicados: <b>{int(package.get('duplicate_count', 0) or 0)}</b>\n"
            f"{quota_line}"
            f"{close_text}\n\n"
            "El original se borra solamente después de guardarse en Vultr. Si la descarga falla, se conserva."
        )

    def _status_markup(self, package_id: str, user_id: int):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        rows = [[InlineKeyboardButton(text="🔄 Actualizar estado", callback_data=f"pack:status:{package_id}")]]
        if user_id in self.ctx.settings.admin_ids:
            rows.append([InlineKeyboardButton(text="📦 Cerrar paquete ahora", callback_data=f"pack:close:{package_id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def status_text(self, package_id: str, viewer_user_id: int | None = None) -> tuple[str, dict | None]:
        package = await self.repo.get_package(self.ctx.bot_id, package_id)
        if not package:
            return "Este paquete ya no está disponible.", None
        count_user_id = int(viewer_user_id) if viewer_user_id is not None else (
            int(package["sender_id"]) if package.get("sender_id") is not None else None
        )
        if count_user_id is not None and (
            package.get("package_mode") == "shared_room" or package.get("sender_id") is not None
        ):
            package["recent_video_count"] = await self.repo.count_recent_user_videos(
                self.ctx.bot_id, str(package.get("room_id") or ""), count_user_id, utcnow() - _VIDEO_QUOTA_WINDOW
            )
        return self._status_text(package), package

    async def close_now(self, package_id: str) -> bool:
        package = await self.repo.get_package(self.ctx.bot_id, package_id)
        if not package or package.get("state") != "OPEN":
            return False
        if package.get("package_mode") == "shared_room":
            counts = await self.repo.recent_user_video_counts(
                self.ctx.bot_id, str(package.get("room_id") or ""), utcnow() - _VIDEO_QUOTA_WINDOW
            )
            eligible = sorted(uid for uid, count in counts.items() if count >= _MIN_VIDEOS_PER_USER_HOUR)
            await self.repo.close_package(self.ctx.bot_id, package_id, "admin_manual", eligible_user_ids=eligible)
        else:
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
            if state == "CLOSING" and package.get("package_mode") == "shared_room" and package.get("close_reason") != "admin_manual":
                eligible_snapshot = [int(uid) for uid in (package.get("eligible_user_ids") or [])]
                if not eligible_snapshot:
                    counts = await self.repo.recent_user_video_counts(
                        self.ctx.bot_id, str(package.get("room_id") or ""), utcnow() - _VIDEO_QUOTA_WINDOW
                    )
                    eligible_snapshot = sorted(
                        uid for uid, count in counts.items() if count >= _MIN_VIDEOS_PER_USER_HOUR
                    )
                if not eligible_snapshot:
                    await self.repo.set_package_fields(
                        self.ctx.bot_id, package_id,
                        {"state": "WAITING_QUOTA", "waiting_quota_since": utcnow()},
                        expected_state="CLOSING",
                    )
                    await self.schedule_progress_refresh(package_id)
                    return
                await self.repo.set_package_fields(
                    self.ctx.bot_id, package_id,
                    {"eligible_user_ids": eligible_snapshot, "quota_met_at": package.get("quota_met_at") or utcnow()},
                    expected_state="CLOSING",
                )
                package = await self.repo.get_package(self.ctx.bot_id, package_id) or package
                state = str(package.get("state") or state)
            if state == "CLOSING" and package.get("sender_id") is not None and not package.get("quota_met_at"):
                recent_video_count = await self.repo.count_recent_user_videos(
                    self.ctx.bot_id, str(package.get("room_id") or ""), int(package["sender_id"]),
                    utcnow() - _VIDEO_QUOTA_WINDOW,
                )
                if recent_video_count < _MIN_VIDEOS_PER_USER_HOUR:
                    await self.repo.set_package_fields(
                        self.ctx.bot_id, package_id,
                        {"state": "WAITING_QUOTA", "waiting_quota_since": utcnow(), "recent_video_count": recent_video_count},
                        expected_state="CLOSING",
                    )
                    await self.schedule_progress_refresh(package_id)
                    return
                await self.repo.set_package_fields(
                    self.ctx.bot_id, package_id,
                    {"quota_met_at": utcnow(), "quota_video_count": recent_video_count},
                )
                package["quota_met_at"] = utcnow()
            if state == "CLOSING":
                if int(package.get("pending_count", 0)) > 0 or int(package.get("staged_count", 0)) == 0:
                    return
                password = secrets.token_urlsafe(15)
                safe_room_id = self._safe_path_component(str(package["room_id"]), "room")
                owner_suffix = f"-user-{int(package['sender_id'])}" if package.get("sender_id") is not None else ""
                archive_path = self._package_dir(str(package["room_id"]), package_id) / f"room-{safe_room_id}{owner_suffix}-{package_id}.zip"
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
                    # The configured channel is the permanent archive destination.
                    # Keep its ZIP post for future retrieval/audit; only local files are cleaned.
                    # The encrypted archive also remains available to the sender and admins.
                    # Remove local payloads only after all recipients were served.
                    cleanup_package_ids = [package_id]
                    deferred_legacy_cleanup: list[str] = []
                    for legacy_id in [
                        str(value) for value in (package.get("merged_legacy_package_ids") or []) if str(value) != package_id
                    ]:
                        still_referenced = await self.repo.has_active_package_reference_to_legacy_dir(
                            self.ctx.bot_id, legacy_id, package_id
                        )
                        if still_referenced:
                            deferred_legacy_cleanup.append(legacy_id)
                            self.logger.info(
                                "room_package_legacy_cleanup_deferred bot_id=%s room_id=%s package_id=%s legacy_package_id=%s",
                                self.ctx.bot_id, package.get("room_id"), package_id, legacy_id,
                            )
                        else:
                            cleanup_package_ids.append(legacy_id)
                    for cleanup_id in cleanup_package_ids:
                        await asyncio.to_thread(shutil.rmtree, self._package_dir(str(package["room_id"]), cleanup_id), True)
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
                            "merged_legacy_package_ids": deferred_legacy_cleanup,
                        },
                    )
                    for legacy_id in cleanup_package_ids[1:]:
                        await self.repo.set_package_fields(
                            self.ctx.bot_id, legacy_id,
                            {"files_cleaned": True, "archive_path": None, "password_encrypted": None,
                             "expire_at": completed_at + timedelta(days=90)},
                        )
                    await self.repo.expire_package_items_in(self.ctx.bot_id, package_id, completed_at + timedelta(days=30))
                    await self.repo.expire_deliveries_in(self.ctx.bot_id, package_id, completed_at + timedelta(days=30))
                    self.logger.info("room_package_sent bot_id=%s room_id=%s sender_id=%s package_id=%s files=%s archive_bytes=%s recipient_count=%s channel_post_kept=%s", self.ctx.bot_id, package["room_id"], package.get("sender_id"), package_id, package.get("staged_count", 0), package.get("archive_size", 0), len(targets), bool(package.get("delivery_source_message_id")))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("room_package_finalize_failed bot_id=%s package_id=%s", self.ctx.bot_id, package_id)
        finally:
            self._finalize_tasks.pop(package_id, None)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

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
        if package.get("package_mode") == "shared_room":
            if "eligible_user_ids" in package:
                ids = {int(uid) for uid in (package.get("eligible_user_ids") or [])}
            else:
                counts = await self.repo.recent_user_video_counts(
                    self.ctx.bot_id, room_id, utcnow() - _VIDEO_QUOTA_WINDOW
                )
                ids = {uid for uid, count in counts.items() if count >= _MIN_VIDEOS_PER_USER_HOUR}
        elif package.get("sender_id") is not None:
            # Compatibility for an archive that started under the former V3 mode.
            ids = {int(package["sender_id"])}
        else:
            # Compatibility for pre-quota room-wide packages.
            members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
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
            f"🏠 Sala: <code>{html.escape(str(package.get('room_id') or ''))}</code>\n"
            + ("👥 Contenido acumulado de todos los miembros de la sala\n" if package.get("package_mode") == "shared_room" else "")
            + (f"👤 Usuario ID: <code>{int(package['sender_id'])}</code>\n" if package.get("sender_id") is not None else "")
            + (f"🎬 Usuarios que cumplieron el mínimo: {len(package.get('eligible_user_ids') or [])}\n" if package.get("package_mode") == "shared_room" else "")
            + f"📁 Archivos: {int(package.get('staged_count', 0))}\n"
            f"📦 Tamaño: {format_bytes(int(package.get('archive_size', 0)))}\n"
            f"🔐 Contraseña AES-256: <code>{html.escape(password)}</code>\n"
            "📌 Copia final conservada en este canal. Para extraerlo, usa 7-Zip o WinRAR."
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
                {"delivery_source_chat_id": source_chat, "delivery_source_message_id": source_message, "delivery_source_chat": "configured_archive_channel", "channel_archive_kept": True, "state": "DELIVERING"},
            )
            return source_chat, source_message
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("room_package_buffer_upload_failed bot_id=%s package_id=%s error_type=%s", self.ctx.bot_id, package["package_id"], type(exc).__name__)
            await self._notify_admin_failure(package, "No se pudo subir el ZIP al canal buffer privado. Verifica que el bot tenga permiso para publicar en storage_channel_id.")
            return None

    async def _cleanup_status_messages(self, package: dict, *, room_status_lock_held: bool = False) -> None:
        """Delete only status cards still owned by this package in MongoDB.

        A stale in-memory package can outlive a size rollover that already moved its
        card IDs to the room's active segment. Re-reading and clearing while holding
        the room card lock prevents an older segment from deleting the current card.
        """
        package_id = str(package.get("package_id") or "")
        room_id = str(package.get("room_id") or "")

        async def cleanup_unlocked() -> None:
            latest = await self.repo.get_package(self.ctx.bot_id, package_id) if package_id else None
            current = latest or package
            message_ids = dict(current.get("status_message_ids") or {})
            if not message_ids:
                return
            if package_id and latest is not None:
                await self.repo.set_package_fields(self.ctx.bot_id, package_id, {"status_message_ids": {}})
            for user_id_text, message_id in message_ids.items():
                try:
                    await self.ctx.bot.delete_message(chat_id=int(user_id_text), message_id=int(message_id))
                except Exception:
                    pass

        if room_status_lock_held:
            await cleanup_unlocked()
        else:
            async with self._lock(self._room_status_locks, room_id):
                await cleanup_unlocked()

    async def _notify_admin_failure(self, package: dict, text: str) -> None:
        for admin_id in self.ctx.settings.admin_ids:
            try:
                await self._pace_outbound_call()
                await self.ctx.bot.send_message(int(admin_id), f"⚠️ No se pudo entregar el paquete {html.escape(str(package.get('package_id')))}: {html.escape(text)}")
            except Exception:
                pass
