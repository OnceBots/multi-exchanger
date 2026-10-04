# Telegram Multi-Bot Platform — Master + Child Bots + Rooms + Admin Feed

Plataforma modular para ejecutar un **BOT MASTER** y una cantidad creciente de **BOTS HIJOS** usando un único core, tenants aislados por `bot_id`, Webhooks, MongoDB Async, Mini App y workers de multimedia.

La versión actual incorpora dos vías independientes dentro de cada Child Bot:

1. **Sistema de salas**: comunidades públicas/privadas, miembros, moderación, permisos, expiración y multimedia anónima.
2. **Admin Feed directo**: cualquier `ADMIN_ID` que entre una vez al Child Bot activa automáticamente su suscripción y recibe el contenido que llegue directamente al bot, sin entrar a ninguna sala.

## Experiencia Master

### Usuario normal

`/start` muestra una interfaz limpia con:

- `➕ Crear mi bot`
- `🤖 Mis bots`
- `📖 Cómo funciona`

Cualquier usuario puede registrar un Child Bot pegando un token válido de BotFather. No se requiere estar en `ADMIN_IDS` para crear el bot.

### Administradores

Los usuarios de `ADMIN_IDS` reciben el panel:

- `🤖 Gestionar bots`
- `➕ Crear bot`
- `❤️ Salud`
- `📊 Estadísticas`
- `📖 Guía`

Cada vez que un usuario crea un nuevo bot, los `ADMIN_IDS` reciben una notificación automática con:

- bot / username
- `bot_id`
- creador
- estado
- botón para abrir el bot
- botón para ver estado
- botón para reiniciar

> Para que Telegram permita al Master iniciar conversaciones con un administrador, cada `ADMIN_ID` debe haber abierto el Master al menos una vez.

## Admin Feed en Child Bots

Cuando un administrador entra al Child Bot y usa `/start`:

- se activa `admin_feeds` en MongoDB;
- la suscripción sobrevive a los reinicios;
- el administrador puede recibir multimedia y contenido directo;
- no necesita crear ni unirse a una sala;
- los mensajes que el propio administrador envía no se reenvían de vuelta al mismo administrador.

Soporta:

- fotos
- vídeos
- documentos
- animaciones
- álbumes
- texto directo no-comando

Los álbumes del feed administrativo se mantienen agrupados cuando Telegram lo permite.

## Child Bot — Interfaz de chat

El menú principal es visual y basado en botones:

- `🌎 Explorar salas`
- `🏠 Mis salas`
- `➕ Crear sala`
- `🚪 Unirme`
- `📱 Abrir Mini App`
- `👤 Mi perfil`
- `📖 Manual`
- para admins: `🛰 Feed admin: ACTIVO`

La interfaz evita mostrar una lista grande de comandos al usuario.

## Salas

Cada sala pertenece a un único tenant (`bot_id`) y puede ser:

- pública
- privada
- activa
- pausada
- expirada
- cerrada

Cada sala admite:

- nombre y descripción
- capacidad
- expiración
- fotos
- vídeos
- archivos
- álbumes
- anonimato de identidad
- owner/admin/member
- selección de sala activa
- código de invitación
- deep link `/start join_<codigo>`

### Moderación

Los owners/admins pueden:

- ver miembros
- silenciar / activar sonido
- bloquear / desbloquear
- expulsar
- pausar
- reanudar
- cerrar
- editar nombre
- editar descripción
- modificar permisos multimedia
- renovar expiración

Un `ADMIN_ID` global puede moderar cualquier sala.

## Mini App

La Mini App se diseñó mobile-first, con soporte del tema de Telegram y safe areas.

Incluye:

- Inicio
- Explorar salas públicas
- Mis salas
- Crear sala
- Perfil
- Idioma
- detalle de sala
- unirse / salir
- compartir
- gestión de sala
- gestión de miembros
- permisos multimedia
- pausa / reanudación / cierre
- edición de sala
- expiración
- feedback tipo toast
- estados vacíos
- skeleton loading
- bottom navigation
- paneles modales tipo bottom-sheet

La autenticación se realiza con `Telegram.WebApp.initData`; el backend no confía en un `user_id` enviado por query string.

## Arquitectura

```text
BOT MASTER
   │
   ├── BotManager
   ├── BotRegistry
   ├── BotRuntime
   └── Control Plane
          │
          ├── Child A ── Admin Feed + Rooms
          ├── Child B ── Admin Feed + Rooms
          └── Child N ── Admin Feed + Rooms

MongoDB
   ├── bots
   ├── users
   ├── rooms
   ├── room_members
   ├── admin_feeds
   ├── media_events
   ├── media_groups
   ├── broadcast_jobs
   ├── broadcast_deliveries
   ├── sessions
   ├── audit_logs
   └── inbound_updates
```

Todos los bots hijos comparten el código del core. No se crean carpetas `bot1/`, `bot2/`, etc.

## Webhooks

En producción el proyecto utiliza Webhooks.

```text
POST /telegram/webhook/master
POST /telegram/webhook/{bot_id}
```

Cada Child Bot tiene su propio `secret_token` cifrado.

Telegram permite establecer un `secret_token` en `setWebhook` y lo envía como `X-Telegram-Bot-Api-Secret-Token`; el proyecto valida ese header antes de aceptar el update. citeturn579914search0turn579914search6

## MongoDB

El proyecto utiliza `AsyncMongoClient` de PyMongo y no Motor. La documentación actual de MongoDB presenta `AsyncMongoClient` como la API asíncrona oficial y documenta el uso con Atlas y Stable API. citeturn787179search0turn787179search5turn787179search8

## Configuración

Copia:

```powershell
copy .env.example .env
```

Variables principales:

```env
MASTER_BOT_TOKEN=
MONGO_URI=
DB_NAME=telegram_multibot
WEBHOOK_BASE_URL=https://tu-servicio.onrender.com
MASTER_WEBHOOK_PATH=/telegram/webhook/master
WEBHOOK_SECRET=
APP_BASE_URL=https://tu-servicio.onrender.com
ADMIN_IDS=123456789,987654321
BOT_TOKEN_ENCRYPTION_KEY=
```

### Generar `BOT_TOKEN_ENCRYPTION_KEY`

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

No publiques:

- `MASTER_BOT_TOKEN`
- tokens de Child Bots
- `MONGO_URI`
- `WEBHOOK_SECRET`
- `BOT_TOKEN_ENCRYPTION_KEY`

## Ejecución local en Windows

El proyecto requiere Python 3.12+; Python 3.13 también es válido.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
python -m app.main
```

Para desarrollo se puede utilizar `MODE=polling`. En producción `MODE=webhook` es obligatorio.

## Render

**Build Command:** Docker

**Start Command:**

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Health Check Path:**

```text
/health
```

## UptimeRobot

Usa un monitor HTTP(S) sobre:

```text
https://TU-DOMINIO/health
```

No utilices el endpoint real de Telegram como monitor.

## Pruebas y validación

Antes del despliegue:

```powershell
python -m compileall -q app tests
pytest -q
```

El entorno de CI ejecuta lint, compilación y pruebas.

## Diseño de escalabilidad

El proceso inicial puede alojar Master y Child Runtimes en una sola instancia. Redis queda preparado para coordinación futura y locks distribuidos. La arquitectura no promete una cantidad fija de bots: la capacidad real depende del volumen de updates, fan-out, salas, MongoDB, Telegram y recursos de Render.

## Nota importante sobre el Admin Feed

`ADMIN_IDS` no significa que el administrador reciba automáticamente el contenido de todos los Child Bots desde el momento de creación. La regla implementada es:

1. el bot se crea;
2. los `ADMIN_IDS` reciben la notificación del nuevo bot en el Master;
3. un administrador abre el Child Bot;
4. `/start` activa su `admin_feed` persistente;
5. desde ese momento recibe el contenido directo del bot sin necesidad de entrar a una sala.
