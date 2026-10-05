# Deploy limpio de la plataforma

Esta versión es un rebuild completo. No mezcles archivos de los ZIP v1-v15 anteriores con este contenido.

## 1. Reemplazar el repositorio

Descomprime el ZIP en la raíz del repositorio para que existan directamente `app/`, `tests/`, `Dockerfile`, `render.yaml`, etc.

## 2. Variables obligatorias en Render

- `MASTER_BOT_TOKEN`
- `MONGO_URI`
- `DB_NAME`
- `WEBHOOK_BASE_URL`
- `WEBHOOK_SECRET`
- `ADMIN_IDS`
- `BOT_TOKEN_ENCRYPTION_KEY`
- `APP_BASE_URL`

En producción:

- `ENVIRONMENT=production`
- `MODE=webhook`
- `PORT=10000`

## 3. No cambies los tokens por el rebuild

La versión es compatible con los campos Mongo actuales `token_encrypted` y `webhook_secret_encrypted`. No borres la colección `bots` ni regeneres los secretos a menos que sea necesario.

## 4. Health Check de Render

Usar:

`/health`

`/health` es liveness y no depende de MongoDB. `/ready` comprueba MongoDB y la plataforma.

## 5. Señales esperadas tras el deploy

```text
mongodb_connected
mongodb_indexes_ready
master_ready ...
webhook_configuring label=master ...
webhook_ready label=master ...
webhook_configuring label=child:... ...
webhook_ready label=child:... ...
child_bot_running ...
platform_ready ...
```

Cuando llegue un mensaje:

```text
master_webhook_received ...
master_webhook_dispatch ...
master_dispatch_result ...
master_webhook_processed ...
```

o:

```text
child_webhook_received ...
webhook_dispatch ...
webhook_dispatch_result ...
child_webhook_processed ...
```

Si Telegram entrega el update pero un handler falla, debe aparecer `dispatch_failed` con traceback en lugar de quedar silencioso.

## Final lifecycle and rooms behavior

- Bot child lifecycle supports manual mode plus expiration timers in minutes/hours.
- When a timer expires, the child runtime/webhook is stopped and the bot is archived in the platform without deleting rooms, members, media events, audit data, or other persisted content.
- The owner receives the BotFather deletion steps from the Master and, when the child chat is available, from the child bot as well.
- Manual deletion controls only prepare/disable the platform runtime; definitive Telegram account deletion is performed by the owner in @BotFather via /mybots -> select bot -> Delete Bot.
- Public rooms require no password. Private rooms can require a password of 4-64 characters; the password is stored as a PBKDF2 hash.
- Every room gets a unique 7-character A-Z/0-9 invite code and a Telegram deep-link.
- Regular users only see the user-facing room/privacy UI; technical logs, audit controls, and moderation tooling remain internal to the platform. The product keeps a general privacy/moderation notice rather than hiding data-processing behavior.
