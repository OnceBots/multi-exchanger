# Patch: fix Master thumbnail upload with local Telegram Bot API

## Files
- `app/bot/master_handlers.py`: downloads the uploaded photo through the local Bot API `/file/bot<TOKEN>/<file_path>` endpoint when local mode is enabled. This avoids incorrectly treating `photos/file_1.jpg` as a host path under `/root/tg-bot-api/data`.

## Apply
Overlay this file onto the repository root, commit and push it, then pull on Vultr and restart `telegram-multibot.service`.

No `.env` changes are required. Do not change `BOT_TOKEN_ENCRYPTION_KEY`.
