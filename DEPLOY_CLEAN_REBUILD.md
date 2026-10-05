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
