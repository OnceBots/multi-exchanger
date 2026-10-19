# Patch manifest — single progress card V5

## Purpose
Prevent duplicate status/progress messages in a member's private chat when an existing status-message edit fails.

## Behavior
- Successful edit: reuse the existing status message.
- `message is not modified`: keep the existing card and do not create another.
- Transient/unknown edit error (network, rate limit, Forbidden): log and wait for the next scheduled refresh; do not create a duplicate card.
- Confirmed stale/uneditable message: send a replacement, persist the new message ID, best-effort pin it, then best-effort unpin/delete the obsolete message.
- Adds regression tests for stale versus non-stale Telegram errors.

## Changed files
- `app/services/room_package_service.py`
- `tests/test_room_package_service.py`

## Deployment safety
The patch does not alter MongoDB records, package contents, archive delivery rules, Bot API paths, or `.env`. Existing duplicate cards already visible in Telegram may need one-time manual cleanup; the patch prevents the known code path from creating further duplicates.
