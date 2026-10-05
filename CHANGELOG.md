
## v11 — Mini App launch/auth compatibility

- Adopted the proven `aprovebot.py` WebApp URL shape (`bot`, `bot_id`, `user_id`, `id`, `v`).
- Added compatibility authentication fallback from `X-Telegram-User-Id` and query `user_id`/`id`.
- Kept Telegram `initData` as the preferred validation path and the signed launch token as the second path.
- Child Mini App now sends the Telegram/query user id on API calls.
- Added regression coverage for query-user authentication.

## v10 - Mini App compatibility with proven launcher

- Child and Master Mini App URLs now follow the working `aprovebot.py` pattern: `bot`, `bot_id`, `user_id`, `v` and a short-lived signed launch token in the query string.
- Removed reliance on URL fragments for the primary launch path.
- Telegram `initData` remains the preferred authentication source; the signed launch token is the fallback.
- Fixed a recursive Master callback answer helper that could prevent Master buttons from responding.
- Kept room codes, share links and no-name/no-description room creation unchanged.

# v9 — Child UX, room identity and Mini App connection

- Fixed recursive callback acknowledgement that made child inline buttons appear unresponsive.
- Child room creation no longer asks for a custom room name.
- New rooms are identified by a server-generated 7-character uppercase alphanumeric code.
- Removed room name editing from the child bot.
- Room creation API rejects custom name fields.
- Added per-user Telegram Mini App menu configuration with short-lived signed fallback launch token.
- Mini App now reads Telegram initData dynamically for every API request.
- Added no-cache headers to Mini App HTML to prevent stale WebView assets after deploys.
- Mini App create-room form now shows the generated 7-character code and share action.
- Room repository prevents later changes to name/invite_code identity fields.
- Preserved share links using `https://t.me/<child>?start=room_<CODE>`.


## v7 — room access code and sharing

- New rooms receive a unique 7-character uppercase alphanumeric access code.
- Room descriptions are no longer supported in the bot flow or Mini App.
- Added shareable Telegram deep links: `https://t.me/<bot>?start=room_<CODE>`.
- Opening a room invite link resolves the 7-character code and joins the user to the room.
- Added share buttons to bot room cards and the Mini App.
- Existing room records remain compatible; legacy description fields are ignored by updates and are no longer rendered.
# Changelog

## v5 — lifecycle + Mini App hardening

- Rebased on the clean rebuild instead of stacking incremental fixes.
- Render Blueprint updated to the current Docker runtime syntax; removed the hardcoded `PORT` variable so Render supplies it.
- Master and child bots now configure a Telegram `MenuButtonWebApp` in addition to inline Mini App buttons.
- Child and Master Mini Apps accept Telegram raw `initData` through both `X-Telegram-Init-Data` and `Authorization: tma ...`.
- Added a short-lived signed launch token fallback for personalized Mini App buttons.
- Expanded the child Mini App to include room browsing, create, join, leave, profile and help.
- Supervisor now checks Master Telegram health, child Telegram health, webhook URL/errors and missing long-lived runtime tasks.
- Legacy environment aliases `MASTER_TOKEN`, `SUPER_ADMINS` and `RENDER_EXTERNAL_URL` remain supported by configuration.
- Docker readiness check aligned with `/ready`.
- Added regression tests for Mini App auth fallback and Render configuration.

## v6 - Async Mongo aggregate fix

- Fixed `RoomRepository.list_for_user()` for PyMongo Async: `aggregate()` is awaited before calling `to_list()`.
- Added regression test `tests/test_rooms_repository.py`.
- No change to Telegram webhook architecture or Mini App launch mechanism.

## v12 - Mini App auth/500 hardening
- Fixed misleading Mini App "Autenticación fallida" status when the API returns HTTP 500.
- Added JSON-safe serialization for MongoDB documents returned by Mini App APIs.
- Excluded MongoDB `_id` values from room/user API payloads.
- Hardened room API error logging.

## Finalization
- Bot lifecycle timers with persistent expiration and user notifications.
- Manual BotFather deletion instructions from Master and child bot.
- Child creation notifies creator via Master and best-effort via the child bot.
- Rooms now enforce max members at creation and support password-protected private rooms.
- Room passwords are stored as PBKDF2 hashes, never plaintext.
- Fixed supervisor webhook false repairs by ignoring historical Telegram last-error text when the configured webhook URL is still correct.
- Archived bot runtimes keep MongoDB room/content data intact.
