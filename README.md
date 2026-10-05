# Telegram Multi-Bot Platform — Rebuild

Plataforma SaaS de Telegram con **MASTER + bots hijos**, un único servidor HTTP, MongoDB compartido y un webhook independiente por bot.

Este repositorio es un **rebuild limpio**. No es un parche incremental del código anterior. Se conserva la funcionalidad principal solicitada: creación pública de bots hijos, aislamiento por `bot_id`, webhooks, salas, membresías, multimedia, álbumes, colas, reintentos, supervisor, Mini App y administración.

## Arquitectura

```text
Telegram
  │
  ├── /telegram/webhook/master ──────► Master Dispatcher
  │                                      │
  │                                      └── BotManager
  │                                           ├── Child #1 Runtime
  │                                           ├── Child #2 Runtime
  │                                           └── Child #N Runtime
  │
  └── /telegram/webhook/{bot_id} ─────► Child Dispatcher

FastAPI / Uvicorn
        │
        └── MongoDB Atlas (PyMongo Async)
```

Cada child tiene su propio `Bot`, `Dispatcher`, colas, workers, heartbeat y secreto de webhook. No se levanta un servidor HTTP por bot y no se usa polling en producción.

## Flujo de creación de bot

1. Usuario entra al Master.
2. Pulsa **Crear mi bot** o usa `/add_bot`.
3. Se valida el token con `getMe`.
4. Se comprueba que el `bot_id` no exista.
5. Token y secreto de webhook se cifran con Fernet.
6. Se persiste el tenant.
7. Se crea el runtime.
8. Se configura el webhook `https://host/telegram/webhook/{bot_id}`.
9. Se inicia el worker pool y heartbeat.
10. Los administradores reciben una notificación del nuevo bot.

## Webhooks

La plataforma verifica el `X-Telegram-Bot-Api-Secret-Token` antes de pasar el update a aiogram. El update se entrega mediante `Dispatcher.feed_raw_update()`, la API documentada por aiogram para aceptar un `dict` bruto. El request espera al dispatcher y cualquier excepción queda visible en Render en lugar de perderse dentro de una task anónima.

## Fechas y MongoDB

Todas las fechas propias de la plataforma se generan con `datetime.now(timezone.utc)`. El cliente MongoDB usa `CodecOptions(tz_aware=True, tzinfo=UTC)` para leer fechas como timezone-aware. Además existe una función de normalización para datos antiguos guardados como naive.

Esto evita el error que había aparecido en el supervisor:

```text
TypeError: can't subtract offset-naive and offset-aware datetimes
```

## Multimedia

- foto
- vídeo
- documento
- GIF/animation
- álbumes de fotos/vídeos con debounce
- captions
- distribución anónima
- exclusión del emisor
- cola acotada
- workers
- concurrencia limitada
- `RetryAfter`
- reintentos
- idempotencia de updates/eventos

Los álbumes se agregan en MongoDB durante una pequeña ventana de debounce y se entregan como un único álbum cuando Telegram lo permite.

## Moderación

El sistema dispone de un **feed de moderación para `ADMIN_IDS`**. El producto muestra una advertencia de privacidad general indicando que el contenido puede procesarse para seguridad/moderación. La función es configurable por administrador.

## Mini App

- `/master-app` — creación de bots hijos.
- `/app?bot_id=...` — vista de salas del bot hijo.
- Validación de `Telegram.WebApp.initData`.
- Diseño mobile-first.

## Variables de entorno

Copia `.env.example` como `.env` en local. En Render, configura las mismas variables desde Environment.

No subas `.env` al repositorio.

## Render

Render debe ejecutar el Dockerfile. El servicio expone `${PORT}` (por defecto `10000`).

Health check:

```text
GET /health
```

Readiness real:

```text
GET /ready
```

URL de webhook del Master:

```text
https://TU_DOMINIO/telegram/webhook/master
```

URLs de child:

```text
https://TU_DOMINIO/telegram/webhook/BOT_ID
```

## Local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

## Tests y comprobaciones

```powershell
python -m compileall -q app tests
python -m pytest -q
```

## Nota sobre pruebas de integración

Las pruebas que requieren Telegram real o MongoDB real deben ejecutarse con credenciales de entorno y servicios disponibles. Este repositorio no afirma haber realizado pruebas externas contra Telegram desde el entorno de generación.

## Despliegue limpio

Consulta `DEPLOY_CLEAN_REBUILD.md` antes de reemplazar el contenido de un repositorio existente. Esta versión debe desplegarse como un conjunto completo; no combines módulos de releases anteriores.
