# Telegram Multi-Bot Platform

Plataforma modular **Master + múltiples Child Bots** para Telegram, con aiogram 3, FastAPI, PyMongo Async, MongoDB, webhooks, salas, intercambio multimedia anónimo, álbumes, Mini App, supervisor y recuperación automática.

## Qué incluye

- Bot Master como control plane.
- Registro, validación, activación, desactivación, restart y eliminación de bots hijos.
- `BotRegistry`, `BotRuntime`, `BotManager` y aislamiento por tenant mediante `bot_id`.
- Un solo servidor HTTP para todos los webhooks.
- MongoDB compartido con índices multi-tenant.
- Tokens de bots hijos cifrados con Fernet.
- Webhook secret distinto para Master y cada Child.
- Idempotencia de updates y eventos multimedia.
- Salas públicas/privadas, membresías y códigos de invitación.
- Multimedia individual y álbumes de Telegram con debounce.
- Broadcast mediante tareas controladas; el handler no espera a todos los destinatarios.
- Copia anónima del contenido mediante `copy_message` / `send_media_group`.
- Mini App separada con validación real de `Telegram.WebApp.initData`.
- `/health`, `/ready` y `/metrics`.
- Docker, Render y GitHub Actions.

## Requisitos

Python 3.12+ y MongoDB Atlas para producción. PyMongo Async usa `AsyncMongoClient`, que es la API asíncrona oficial actual de PyMongo. La documentación oficial recomienda Stable API para Atlas. [MongoDB Docs](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-targets/)

## 1. Crear el Master Bot

Crea el bot principal con BotFather y obtén su token.

## 2. MongoDB Atlas

Crea un cluster, un usuario de base de datos y agrega la IP de salida correspondiente. Copia la URI en `MONGO_URI`.

## 3. Configurar secretos

Copia `.env.example` a `.env`.

Genera la clave Fernet:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Pon el resultado en `BOT_TOKEN_ENCRYPTION_KEY`.

Genera también un `WEBHOOK_SECRET` largo y aleatorio.

## 4. Ejecutar localmente

Para desarrollo simple puedes usar `MODE=polling` y `ENVIRONMENT=development`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m app.main
```

Para utilizar webhooks localmente necesitas una URL HTTPS pública (por ejemplo, un túnel).

## 5. Docker

```bash
docker compose up --build
```

## 6. Master — administración

Desde el Master:

```text
/start
/bots
/add_bot
/bot_info ID
/bot_start ID
/bot_stop ID
/bot_restart ID
/bot_enable ID
/bot_disable ID
/bot_delete ID
/bot_health
/stats
```

`/add_bot` inicia el flujo de alta del bot hijo. El token se valida con `getMe`, se cifra y luego el runtime se inicializa.

## 7. Child Bot

El usuario puede:

```text
/start
/rooms
/create_room
/join ROOM-ID-o-CODIGO
/my_rooms
/help
```

Después de elegir una sala puede enviar fotos, vídeos, documentos y animaciones.

## 8. Salas

`/create_room Nombre|Descripción|PUBLIC|100` permite crear una sala directamente.

También existe un asistente interactivo desde `/create_room`.

Cada entidad funcional de un hijo se filtra por `bot_id`, de modo que un tenant no consulta los datos de otro.

## 9. Webhook

Producción utiliza:

```text
POST /telegram/webhook/master
POST /telegram/webhook/{bot_id}
```

Cada endpoint valida `X-Telegram-Bot-Api-Secret-Token`.

El Master usa `WEBHOOK_SECRET`; cada Child tiene un secreto diferente almacenado cifrado.

## 10. Render

**Build Command:** lo gestiona el Dockerfile.

**Start Command:**

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Health Check Path:**

```text
/health
```

**Environment Variables:** configura todas las variables de `.env.example` en Render. No subas `.env` a GitHub.

`/health` comprueba que el proceso HTTP esté vivo. `/ready` comprueba dependencias principales y estado básico del Master.

## 11. UptimeRobot

Usa un HTTP(S) Monitor apuntando a:

```text
https://TU-DOMINIO/health
```

No uses el webhook de Telegram como monitor.

## 12. CI

GitHub Actions ejecuta:

- Ruff
- compilación Python
- pytest

## 13. Arquitectura para escalar

El proceso inicial puede alojar Master y Child runtimes en una sola instancia. Para escalar horizontalmente, conecta Redis y agrega locks distribuidos/worker assignment antes de ejecutar réplicas que puedan compartir ownership de un mismo bot.

No se debe interpretar el proyecto como garantía de un número concreto de bots simultáneos: la capacidad real depende del plan de Render, MongoDB, volumen de actualizaciones, cantidad de salas y fan-out.

## 14. Seguridad

Nunca publiques:

- `MASTER_BOT_TOKEN`
- tokens de Child Bots
- `MONGO_URI`
- `WEBHOOK_SECRET`
- `BOT_TOKEN_ENCRYPTION_KEY`
- secretos de Redis

Los tokens de Child se almacenan cifrados; aun así, el usuario que controla el servidor y la clave de cifrado debe considerarse administrador de secretos.
