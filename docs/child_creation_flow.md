# Child Bot Creation Flow

The child provisioning flow is adapted from the supplied `iobot.py` reference.

1. Receive the creator's BotFather token.
2. Validate it against Telegram with `getMe()`.
3. Resolve `bot_id` and username.
4. Reject duplicate registrations.
5. Encrypt token and per-child webhook secret.
6. Persist the child as `STARTING`.
7. Start one isolated `BotRuntime` using the platform webhook server.
8. Mark the bot `RUNNING` and notify administrators.

The reference project uses polling for child runtimes. This project intentionally keeps webhooks in production so the multi-bot platform remains compatible with the architecture requested in the supplied base documents.
