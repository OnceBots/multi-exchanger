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

## v14 — webhook/runtime hardening
- Los bots hijos y el Master verifican webhook con reintentos.
- El arranque espera a los runtimes hijos antes de marcar la plataforma como READY.
- `/ready` responde 503 hasta que Mongo, Master y los hijos iniciales estén listos.
- Los webhooks registran recepción, deduplicación y errores de dispatch sin generar `Task exception was never retrieved`.
- Render usa `/ready` como health check.
- Pytest queda configurado con `pythonpath=.`.

## v15 - UTC datetime normalization

- Fixed `TypeError: can't subtract offset-naive and offset-aware datetimes` in `BotManager._supervisor_loop`.
- MongoDB now decodes BSON datetimes as timezone-aware UTC values.
- Supervisor normalizes legacy naive timestamps before heartbeat arithmetic.
- `BotInfo` normalizes `last_started_at` and `last_heartbeat` from old Mongo documents.
- Repositories that were still using `datetime.utcnow()` now write aware UTC timestamps.
- Added regression tests for naive/aware datetime normalization.
