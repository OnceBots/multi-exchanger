# v8 — stale callback query hardening

- Do not replay persisted `callback_query` updates after restart.
- Mark stale callback updates as `SKIPPED` instead of retrying them.
- Treat Telegram's expired/invalid callback-query errors as non-fatal.
- Added safe callback answer helper to child and Master handlers.
- Added regression tests for callback replay and stale query handling.
