# Deploy v5 — Render + Telegram Mini Apps

## 1. Reemplazo

Reemplaza todo el repositorio por este release. No mezcles archivos de v3/v4.

## 2. Variables mínimas en Render

Mantén estas variables con tus valores actuales:

- `MASTER_BOT_TOKEN`
- `MONGO_URI`
- `DB_NAME=telegram_multibot`
- `WEBHOOK_SECRET`
- `BOT_TOKEN_ENCRYPTION_KEY`
- `ADMIN_IDS`

`WEBHOOK_BASE_URL` y `APP_BASE_URL` siguen siendo soportadas. Si no existen, la aplicación usa `RENDER_EXTERNAL_URL` de Render.

No agregues `PORT` al `render.yaml`: Render proporciona ese valor automáticamente y la aplicación lo consume desde el entorno.

## 3. Qué configura el runtime

- Master: webhook `/telegram/webhook/master`.
- Child: webhook `/telegram/webhook/{bot_id}`.
- Master y child: `MenuButtonWebApp`.
- Inline buttons: `web_app=WebAppInfo(...)`.
- Supervisor: `get_me`, `get_webhook_info`, heartbeat y tareas de runtime.

## 4. Mini App

La Mini App se abre mediante Telegram Web App, no mediante un enlace de navegador ordinario. La URL HTTPS es el recurso HTML que Telegram incrusta dentro del cliente.

La autenticación intenta, por orden:

1. Telegram `initData` por `X-Telegram-Init-Data`.
2. Telegram `Authorization: tma <initData>`.
3. Launch token HMAC de corta duración en `X-Mini-App-Launch-Token`, obtenido desde el fragmento de la URL de un botón personalizado.

El token de fallback no va en la query nueva, para no aparecer en access logs.

## 5. Logs esperados

Después del arranque:

```text
mini_app_menu_ready label=master
webhook_ready label=master
webhook_ready label=child:<id>
child_bot_running bot_id=<id>
children_bootstrap_complete total=... running=... failures=0
platform_ready ...
```

Al abrir la Mini App del child:

```text
GET /api/child/rooms?bot_id=<id> 200 OK
```

Ya no debe aparecer el `401 Unauthorized` del release anterior. Si aparece, el servidor ahora registra `mini_app_auth_failed` sin imprimir ningún token.

## 6. Git

```powershell
cd C:\Users\usuario\Documents\telegram_multibot_platform
git add .
git commit -m "v5 lifecycle and telegram mini app hardening"
git push origin main
```
