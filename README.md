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

## Multimedia en salas

La multimedia compatible de cada sala lógica se acumula en un ZIP compartido por sala, protegido con contraseña AES-256. El paquete puede contener archivos aportados por diferentes miembros; quienes reciben una copia son los miembros que cumplen la cuota y los administradores.

- Por defecto se aceptan vídeos, fotos, archivos, GIF/animaciones, álbumes, audio, notas de voz, vídeos circulares y stickers; `ADMIN_IDS` puede restringir formatos.
- Cada archivo se descarga y guarda en un volumen Docker persistente. El mensaje original del miembro se borra únicamente después de verificar el archivo en disco y confirmar la ruta y el tamaño en MongoDB; si falla la descarga, el original se conserva.
- El paquete se cierra al llegar a **1800 MiB** (margen de seguridad respecto al límite de 2 GB) o después de **60 segundos sin nuevos archivos** y sin descargas pendientes.
- Cada miembro recibe un estado actualizable con barra, acumulado y bytes que faltan; puede usar el botón para consultar el estado. `ADMIN_IDS` tiene además un botón para cerrar manualmente el paquete.
- El ZIP se publica en el canal buffer privado y el mismo mensaje se copia a los miembros que cumplen la cuota y a los administradores habilitados; el feed de moderación es independiente de esta entrega.
- La cola es persistente, deduplicada y recuperable tras reinicios. Descargas, cifrado y entrega usan límites de concurrencia, reserva de espacio en disco, procesamiento en lotes y reintentos con espera para evitar saturar el bot.

### Archivos grandes

Para aceptar vídeos de 500 MB–1 GB, el servicio Telegram Bot API debe ejecutarse realmente en modo local. Configura `TELEGRAM_API_ID` y `TELEGRAM_API_HASH` en `.env` y despliega con `docker compose -f docker-compose.yml -f docker-compose.large-files.yml up -d --build`. La API no se publica en Internet y sus archivos se comparten con la app mediante un volumen interno. Consulta [`docs/LARGE_FILES_1GB.md`](docs/LARGE_FILES_1GB.md) antes de migrar una instalación existente.

### Configuración requerida para empaquetado

Antes de recibir multimedia, el Master debe tener configurado un ID numérico de canal buffer privado (`📦 Configurar canal de almacenamiento` o `/storage_channel`). Cada bot hijo necesita permiso para publicar en ese canal. El original se conserva si falta la configuración.

Detalles: [`docs/ROOM_MEDIA_ZIP_PACKAGING.md`](docs/ROOM_MEDIA_ZIP_PACKAGING.md).

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
