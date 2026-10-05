# Changelog

## v11.0.0 - Child Bot Provisioning

- Reworked child-bot creation using the proven flow from the supplied `iobot.py` reference:
  - validate token with Telegram `getMe()` before provisioning;
  - reject duplicate `bot_id` registrations;
  - encrypt child token and webhook secret before persistence;
  - persist child configuration in `STARTING` state;
  - start the child runtime only after successful validation;
  - keep failed creations recoverable in MongoDB;
  - notify configured administrators after successful activation.
- Kept production architecture on webhook mode; the source project's polling loop was not copied.
- Added a working `/master-app` Mini App with a Mansia-style create-bot window (name, username/URL, BotFather token).
- Added authenticated `POST /api/master/create-bot` using Telegram WebApp `initData`.
- Fixed owner-facing bot information so the internal administrator feed count is not exposed to bot owners.
- Added diagnostic logging for the creation lifecycle without logging tokens.
