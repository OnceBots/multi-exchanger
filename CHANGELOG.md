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
