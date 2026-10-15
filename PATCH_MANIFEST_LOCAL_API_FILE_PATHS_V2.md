# Hardened local Telegram Bot API file mapping (v2)

This is an installer-style patch because the working copy may contain local edits to `app/bot/master_handlers.py`. It updates only the thumbnail handler and specific config defaults instead of overwriting the full handler file.

## Root cause verified from the VPS

- `docker inspect tg-local-api` showed `--local` enabled.
- Docker mounts `/root/tg-bot-api/data` on the host to `/var/lib/telegram-bot-api` in the container.
- Photos exist below `<host-root>/<bot-token>/photos/file_1.jpg` and `<container-root>/<bot-token>/photos/file_1.jpg`.
- `getFile` is returning a relative per-bot path (`photos/file_1.jpg`) to the application. Aiogram's stock `SimpleFilesPathWrapper` uses `Path(value).relative_to(server_path)`, which raises for that relative path.
- A previous workaround tried `/file/bot...` over HTTP, which returned 404. In Bot API `--local` mode, the documented approach is to consume the local filesystem path, not create a normal HTTP file-download URL.

## What the installer changes

- Sets the container root default to `/var/lib/telegram-bot-api` and host root default to `/root/tg-bot-api/data`, without changing the rest of the settings file.
- Corrects a bare `token_encryption_key` dataclass declaration if found, to avoid the prior `NameError` on startup.
- Replaces `app/bot/api_client.py` with a shared, bot-scoped resolver that handles relative per-bot paths, absolute container paths, and paths already on the host; rejects traversal, wrong-token folders, and files outside the shared root.
- Patches the Master photo handler to use that resolver in local mode, with a 10 MiB source-size limit, and never downloads via `/file/bot...` in local mode.
- Patches the video thumbnail pipeline to use the same resolver with the child bot's token.
- Creates backups of edited source files with `.bak_local_api_paths` suffixes.
- Includes regression tests for the observed relative path, absolute paths, traversal, and cross-bot isolation.

## Apply

From the repository root in the active project Python environment:

```bash
python apply_local_api_path_fix.py --check
python apply_local_api_path_fix.py
python -m compileall -q app tests
python -m pytest -q tests/test_local_api_file_path_mapping.py tests/test_vultr_api_client.py
```

If your `.env` explicitly sets either path root, keep them aligned with the mount:

```env
TELEGRAM_API_SERVER_FILES_ROOT=/var/lib/telegram-bot-api
TELEGRAM_API_HOST_FILES_ROOT=/root/tg-bot-api/data
```

Do not change `BOT_TOKEN_ENCRYPTION_KEY` or delete data from MongoDB.
