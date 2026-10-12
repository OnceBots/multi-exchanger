# Patch: fix Master thumbnail upload using the Bot API shared data directory

## What changed
- `app/bot/master_handlers.py`: local Bot API `getFile` responses may return a relative path such as `photos/file_1.jpg`. The handler now safely resolves that relative path under `TELEGRAM_API_SERVER_FILES_ROOT`, maps it to `TELEGRAM_API_HOST_FILES_ROOT`, checks the path stays within the configured roots, verifies the file exists and is at most 10 MB, and reads the bytes directly. This avoids the incorrect `/file/bot...` URL that was returning HTTP 404.

## Apply
Overlay this file at the repository root, then commit/push to `main` and pull on the VPS. Alternatively, copy `app/bot/master_handlers.py` directly to `/root/bot_project/app/bot/master_handlers.py` after backing up the current file.

## Required configuration
The app process must be able to read the photo under `TELEGRAM_API_HOST_FILES_ROOT`. For the current setup the expected values are:

```env
TELEGRAM_API_SERVER_FILES_ROOT=/root/tg-bot-api/data
TELEGRAM_API_HOST_FILES_ROOT=/root/tg-bot-api/data
TELEGRAM_API_LOCAL_MODE=true
```

No token or encryption key changes are required.
