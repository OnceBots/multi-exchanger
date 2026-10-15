# Telegram Multi-Bot Platform — Rebuild

Plataforma SaaS de Telegram con **MASTER + bots hijos**, un único servidor HTTP, MongoDB compartido y un webhook independiente por bot.

Este repositorio es un **rebuild limpio**. No es un parche incremental del código anterior. Se conserva la funcionalidad principal solicitada: creación pública de bots hijos, aislamiento por `bot_id`, webhooks, salas, membresías, multimedia, álbumes, colas, reintentos, supervisor, chat de Telegram y administración.

### Salas

Cada sala nueva genera automáticamente un código de acceso de 7 caracteres (letras mayúsculas y números). La plataforma no permite crear ni editar descripciones de salas. Cada sala tiene un enlace de invitación de Telegram con el formato `https://t.me/<bot>?start=room_<CODIGO>` que puede compartirse desde el bot o la chat de Telegram.

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
4. Si el `bot_id` no existe, se registra como bot nuevo; si existe y está activo, se rechaza el duplicado.
5. Si el registro está archivado, el propietario original (o un administrador) puede recuperarlo con el mismo token validado.
6. La recuperación renueva el token cifrado y el secreto de webhook, conserva el propietario, la configuración no relacionada con el ciclo de vida y todos los documentos de salas/miembros/historial.
7. Token y secreto de webhook se cifran con Fernet.
8. Se crea/reinicia el runtime sin duplicar el bot.
9. Se configura el webhook `https://host/telegram/webhook/{bot_id}`.
10. Se inicia el worker pool y heartbeat; los administradores reciben notificación de alta o recuperación.

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
- los mensajes distribuidos en salas son solo multimedia, sin captions ni texto adjunto
- los videos y álbumes con video deben pasar correctamente por la miniatura predeterminada y el canal buffer; si el pipeline falla, se bloquea la entrega en lugar de enviar el video directamente
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

## Interfaz de usuario

Toda la administración se realiza dentro del chat de Telegram mediante comandos, mensajes y botones inline. No se sirve ni requiere una Mini App, una página web de administración ni `APP_BASE_URL`.

## Variables de entorno

Copia `.env.example` como `.env` en local. En Render, configura las mismas variables desde Environment.

No subas `.env` al repositorio.

## Render

Render ejecuta el Dockerfile. La aplicación enlaza `0.0.0.0:${PORT}` y usa `10000` como valor local por defecto. En Render, `PORT` es proporcionado por la plataforma.

Health check de proceso preparado para tráfico:

```text
GET /ready
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
pip install -r requirements-dev.txt
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

## v8 — callback queries tras reinicios

Los `callback_query` de Telegram son efímeros y no deben volver a contestarse después de un reinicio. La plataforma ahora omite esos updates durante el replay y trata los errores de consulta caducada como no fatales.


## Final features

### Bot lifecycle
Use the Master or the child owner's panel to choose `Manual`, `5 min`, `15 min`, `30 min`, `1 h`, `6 h`, `12 h`, `24 h`, `48 h`, or `72 h`. When the timer expires, the runtime is stopped and archived while persisted room/content data remains in MongoDB. The owner receives instructions for permanent Telegram deletion through @BotFather.

### Rooms
Rooms have no custom name or description. Each one gets a random 7-character A-Z/0-9 code, a maximum member count, and a shareable Telegram deep-link. Private rooms may require a 4-64 character password; only a PBKDF2 hash is stored.
