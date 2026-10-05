# Deploy v9

1. Replace the repository contents with this ZIP.
2. Keep the existing Render environment variables.
3. Commit and push to `main`.
4. Open the child bot in a private chat and send `/start`.
5. Tap `📱 Abrir Mini App` from the bot, not a normal browser URL.
6. Create a room: no name is requested; the platform returns a 7-character code.

Expected Render signals:

- `platform_ready`
- `webhook_ready label=child:<BOT_ID>`
- `mini_app_user_menu_ready` after `/start`
- `POST /telegram/webhook/<BOT_ID> ... 200`
- `GET /app?bot_id=<BOT_ID> 200`
- `GET /api/child/rooms?bot_id=<BOT_ID> 200`

