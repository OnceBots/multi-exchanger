
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
