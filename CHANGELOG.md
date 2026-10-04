# Changelog

## v2 — Public Master + Admin Feed + Premium Room UI

- Master Bot is publicly usable for Child Bot creation.
- Any Telegram user can start the Child Bot creation flow.
- `ADMIN_IDS` receive a notification whenever a Child Bot is created.
- Admin notification includes open, health and restart actions.
- `/start` in a Child Bot automatically enables the persistent Admin Feed for `ADMIN_IDS`.
- Admin Feed works without rooms and supports direct photos, videos, documents, animations, albums and non-command text.
- Room and Admin Feed paths can coexist for the same multimedia update.
- Albums remain grouped for the Admin Feed.
- New room creation wizard from chat with visibility, capacity, media permissions and expiration.
- Room management from chat: pause, resume, close, edit, share, permissions and moderation.
- Public/private room deep links are supported.
- Room expiration cleanup runs in the platform supervisor lifecycle.
- Mini App redesigned with Telegram-aware theme variables, mobile-first layout, bottom navigation, skeletons, modals, toasts and room cards.
- Mini App supports explore, my rooms, creation, details, join/leave, edit, permissions, moderation, profile and language preference.
- Documentation updated for the new public Master and Admin Feed behaviour.
