# Thumbnail upload fix for local Telegram Bot API

## What changed
- Reads uploaded photos from the local Bot API shared data directory when `get_file` returns a relative filesystem path such as `photos/file_1.jpg`.
- Falls back to aiogram's normal download method when the local file is not available.
- Optional environment variable: `LOCAL_BOT_API_DATA_DIR` (default `/root/tg-bot-api/data`).
- Keeps the existing JPG normalization and size validation.

## Apply
Overlay this ZIP at the repository root, commit and push, then pull on the VPS. Ensure the app host can read the same data directory used by the `tg-local-api` container. If the volume is mounted at another host path, set `LOCAL_BOT_API_DATA_DIR` to that host path.
