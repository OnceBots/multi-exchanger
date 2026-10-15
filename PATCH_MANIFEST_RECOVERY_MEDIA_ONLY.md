# Patch manifest — archived bot recovery and media-only rooms

This patch is based on the supplied `multi-exchanger-main` source tree.

## Changes

### 1. Reactivate an archived child by submitting its same valid token

- `ChildBotProvisioner` distinguishes archived records from active duplicates.
- The token is validated with Telegram first; the Telegram bot ID is then used as the stable identity.
- Only the original owner or a configured administrator may reactivate an archived record.
- Reactivation renews the encrypted bot token and webhook secret and resets runtime status to `STARTING`/`RUNNING`.
- The original owner, `created_at`, restart counter, non-lifecycle configuration, room/member/history collections and other persisted data are not replaced.
- Archive/timer lifecycle markers are cleared so the bot can be started normally.
- Active duplicate bot IDs remain rejected. A process-local lock plus MongoDB's unique `bot_id` index avoids duplicate registrations.
- The Master responds “BOT RECUPERADO Y ACTIVO” and records `BOT_REACTIVATED` in audit logs for a successful recovery.

### 2. Video thumbnail pipeline is mandatory for room deliveries

- Standalone videos and albums containing videos must successfully pass through `VideoThumbnailPipeline` and be uploaded to the storage channel with the configured default thumbnail before delivery begins.
- The old “fallback to direct send_video/send_media_group if thumbnail processing fails” is removed.
- If the pipeline is unavailable or fails, no recipient receives that video/album; the broadcast is recorded as failed.
- The low-level send helpers also reject an unbuffered video as defense in depth.
- Videos uploaded as documents (video MIME type or known video extension) are classified as videos so they cannot bypass thumbnail processing.

### 3. Room posts are media-only and have no captions

- Room broadcasts send photos, animations, and documents with `caption=None`.
- Native photo albums contain no captions.
- The thumbnail buffer upload for videos and mixed albums also uses no captions; copied buffer messages therefore remain media-only.
- Media event records for room publication store no caption. Admin-feed behavior remains separate and is not changed by this room-only policy.
- Text-only messages are not added to the room broadcast queue.

## Files changed

- `app/services/child_bot_provisioner.py`
- `app/db/repositories/bots.py`
- `app/bot/manager.py`
- `app/bot/master_handlers.py`
- `app/services/broadcast_service.py`
- `app/services/media_service.py`
- `app/services/video_thumbnail_pipeline.py`
- `tests/test_archived_bot_reactivation.py`
- `tests/test_room_media_only_policy.py`
- `tests/test_video_thumbnail_pipeline.py`

## Validation

- `python -m compileall -q app tests` passed in the packaging environment.
- The archived-reactivation policy tests were exercised with a small isolated harness (reactivation/preservation, owner authorization, active duplicate rejection and the repository update filter): all passed.
- The room media-policy tests were exercised with lightweight aiogram/pipeline stubs (captions stripped, direct video blocked, pipeline-failure closed, video album fail-closed, and video-document detection): all passed. This checks the targeted control-flow branches, not Telegram integration.
- The full `pytest` suite could not be collected in this packaging environment because dependencies such as `aiogram` are not installed and package-index access was unavailable. Run the full suite in the project's normal environment:

  ```bash
  python -m pytest -q
  ```

## Deployment notes

1. Keep the current `.env`, `BOT_TOKEN_ENCRYPTION_KEY`, and MongoDB URI/database unchanged.
2. Back up MongoDB and the current project before deploying these changes.
3. Build/recreate only the app service using the existing Compose files.
4. Verify Master `/start`, then recover the archived child's token from the Master. Confirm that its old rooms/configuration return and its webhook reaches the child runtime.
5. Test a video with a caption and an album with at least one video. Room recipients should receive no caption, and the video must appear with the default thumbnail.
6. Temporarily make the storage/thumbnail pipeline unavailable in a test environment and verify the video is rejected rather than sent directly.
