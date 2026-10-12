# Despliegue de multi-exchanger en Vultr (Ubuntu 24.04)

## Importante: datos y secretos

- Conserva el mismo `MONGO_URI` y `DB_NAME` que utiliza la instancia anterior para continuar usando la misma base de datos.
- `BOT_TOKEN_ENCRYPTION_KEY` debe ser **exactamente la misma** clave que se usaba en la instancia anterior. No la generes de nuevo: los tokens hijos guardados en Mongo están cifrados con ella.
- No subas `.env` a GitHub. Manténlo solo en el VPS con permisos `600`.
- `WEBHOOK_BASE_URL` tiene que apuntar al endpoint público HTTPS de este backend para recibir los webhooks de Telegram. La plataforma se administra íntegramente por chat, por lo que no se necesita `APP_BASE_URL`. `TELEGRAM_API_BASE_URL` es el endpoint interno del Bot API local, no el URL público.
- Si `MONGO_URI` corresponde a MongoDB Atlas, agrega la IP pública de Vultr a Network Access de Atlas (solo esa IP, no `0.0.0.0/0`) para que el VPS pueda conectarse.
- El Bot API local acepta HTTP dentro de la red privada Docker. No expongas públicamente el puerto 8081 ni MongoDB 27017.

## 1. Incorporar el parche a GitHub desde Windows

Descarga y extrae este parche en la raíz de una copia local de `https://github.com/OnceBots/multi-exchanger`, después de crear una rama y actualizar `main`:

```powershell
git switch main
git pull --ff-only origin main
git switch -c vultr-local-api-thumbnails
Expand-Archive -LiteralPath "$HOME\Downloads\telegram_multibot_platform_vultr_local_api_thumbnail_patch.zip" -DestinationPath . -Force
git diff --check
git status --short
```

Revisa el diff y confirma que no se haya agregado ningún `.env` real. Luego:

```powershell
git add .env.example Dockerfile docker-compose.yml docker-compose.vultr.override.yml PATCH_MANIFEST.md VULTR_LOCAL_BOT_API_THUMBNAILS.md VULTR_DEPLOY_GUIDE.md app/config.py app/bot/api_client.py app/bot/manager.py app/services/token_service.py app/services/media_service.py app/services/broadcast_service.py app/services/video_thumbnail_pipeline.py app/services/bot_lifecycle.py assets tests scripts/migrate_cloud_tokens_to_local_api.py
git commit -m "Add Vultr local Bot API thumbnail pipeline"
git push -u origin vultr-local-api-thumbnails
```

Fusiona la rama a `main` en GitHub cuando la revisión esté lista.

## 2. VPS y Docker

Entra por SSH y verifica primero lo que ya existe:

```bash
ssh root@IP_PUBLICA_VULTR
docker --version
docker compose version
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
```

Si Docker ya funciona por el contenedor Bot API, no lo reinstales ni reinicies el daemon sin necesidad. Si falta, instala Docker Engine y el plugin Compose siguiendo la documentación oficial: https://docs.docker.com/engine/install/ubuntu/.

Clona el repositorio si no está clonado:

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/OnceBots/multi-exchanger.git
cd /opt/multi-exchanger
```

Si ya existe `/opt/multi-exchanger`, no vuelvas a clonarlo; haz backup de `.env` y ejecuta `git fetch origin` y `git pull --ff-only origin main` después de fusionar la rama en GitHub.

## 3. HTTPS para los webhooks de Telegram

Crea un registro DNS `A` (por ejemplo `bot.tudominio.com`) que apunte a la IP pública de Vultr. Instala Caddy desde su repositorio oficial: https://caddyserver.com/docs/install.

Configura `/etc/caddy/Caddyfile`:

```caddy
bot.tudominio.com {
    reverse_proxy 127.0.0.1:10000
}
```

Después ejecuta:

```bash
sudo systemctl reload caddy
```

El firewall de Vultr/Ubuntu debe permitir SSH, TCP 80 y TCP 443. No abras 8081 ni 27017 al público. El puerto 10000 queda enlazado solo a `127.0.0.1` por el Compose de este parche.

## 4. Red Docker privada entre app y Bot API local

Identifica el nombre del contenedor Bot API existente y su ruta de datos:

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Image}}'
docker inspect --format '{{range .Mounts}}{{println .Source "=>" .Destination}}{{end}}' NOMBRE_CONTENEDOR_BOT_API
docker inspect --format '{{json .Config.Cmd}}' NOMBRE_CONTENEDOR_BOT_API
```

Crea una red compartida y conecta el contenedor existente del Bot API (reemplaza el nombre real):

```bash
docker network create multiexchanger-botapi
docker network connect --alias telegram-bot-api multiexchanger-botapi NOMBRE_CONTENEDOR_BOT_API
```

Si la red ya existe, no la recrees. Comprueba la conexión con `docker network inspect multiexchanger-botapi`. El override conecta la app a esa red y usa `http://telegram-bot-api:8081` sin exponer el puerto local del Bot API a Internet.

### Mapear rutas de archivos

`TELEGRAM_API_SERVER_FILES_ROOT` debe ser el prefijo de ruta que devuelve `getFile` el Bot API local. `TELEGRAM_API_HOST_FILES_ROOT` es el punto equivalente visible **dentro del contenedor de la app**; con el override suministrado es `/srv/tg-bot-api-data`.

No asumas que la ruta interna del contenedor Bot API es igual a la ruta del host. Usa el destino de montaje y el argumento `--dir` que muestran los dos `docker inspect` anteriores; confirma el valor de `file_path` con `getFile` de una prueba. Configura la raíz interna correcta en `.env` como `TELEGRAM_API_SERVER_FILES_ROOT`.

La carpeta montada del Bot API queda de solo lectura para la app. Asegúrate de que el UID de la app (normalmente 1000, `appuser`) tenga permiso de lectura/travesía sobre los archivos sin hacerlos globalmente escribibles.

## 5. RAM disk

Verifica si el tmpfs ya está montado antes de tocar `/etc/fstab`:

```bash
findmnt /mnt/ramdisk
df -h /mnt/ramdisk
```

Si no está configurado para sobrevivir un reinicio, añade una sola entrada a `/etc/fstab` (ajusta UID/GID al resultado de `docker compose ... run --rm app id`):

```fstab
tmpfs /mnt/ramdisk tmpfs size=4G,mode=1770,uid=1000,gid=1000 0 0
```

Luego ejecuta `sudo mount -a` y vuelve a comprobarlo con `findmnt`. No agregues una segunda entrada si ya existe una.

## 6. Crear/configurar `.env`

Si es un despliegue nuevo sin `.env`, ejecuta `cp .env.example .env`; si ya hay un `.env`, **no lo sobrescribas**. Edita con `nano .env` y establece al menos:

```env
MASTER_BOT_TOKEN=TOKEN_ACTUAL_DEL_MASTER
MONGO_URI=URI_ACTUAL_DE_MONGODB
DB_NAME=telegram_multibot
WEBHOOK_BASE_URL=https://bot.tudominio.com
WEBHOOK_SECRET=SECRETO_LARGO_SOLO_LETRAS_NUMEROS_GUION_BAJO
BOT_TOKEN_ENCRYPTION_KEY=LA_MISMA_CLAVE_FERNET_QUE_USABA_RENDER
ADMIN_IDS=ID_TELEGRAM_ADMIN
TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081
TELEGRAM_API_LOCAL_MODE=true
TELEGRAM_API_TIMEOUT_SECONDS=7200
TELEGRAM_API_SERVER_FILES_ROOT=RUTA_INTERNA_REAL_DEL_BOT_API
TELEGRAM_API_HOST_FILES_ROOT=/srv/tg-bot-api-data
CANAL_STORAGE_ID=-1001234567890
VIDEO_RAMDISK_PATH=/mnt/ramdisk
VIDEO_REQUIRE_TMPFS=true
VIDEO_THUMBNAIL_PATH=assets/thumb.jpg
VIDEO_DELIVERY_DELAY_SECONDS=0.04
```

Sustituye todos los valores de ejemplo. `CANAL_STORAGE_ID` es el ID de un canal privado en el que todos los bots hijos que retransmiten contenido deben poder publicar. Cambia `assets/thumb.jpg` por tu miniatura corporativa (JPEG, hasta 320x320, menos de 200 KB). Protege el archivo:

```bash
chmod 600 .env
```

## 7. Migrar tokens de la API alojada a la API local

Telegram exige llamar `logOut` en `https://api.telegram.org` para pasar un bot ya registrado a un servidor Bot API local. El script incluido solo cifra/descifra en memoria y nunca imprime tokens; por defecto hace un dry-run.

**Planifica una ventana de corte:** haz backup de la base, detén la instancia Render para que no siga usando los tokens y confirma que el contenedor Bot API local responde desde la red de la app. Luego valida el dry-run:

```bash
docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml config -q
docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml build
docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml run --rm app python scripts/migrate_cloud_tokens_to_local_api.py
```

Si muestra los bots esperados, ejecuta la migración una sola vez:

```bash
docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml run --rm app python scripts/migrate_cloud_tokens_to_local_api.py --execute
```

No avances si algún bot activo no queda verificado en la API local. Un bot inactivo/archivado que ya fue eliminado puede aparecer como `WARN`; verifica los activos antes de continuar. El script no modifica ni borra documentos de MongoDB.

## 8. Iniciar y validar

```bash
docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml up -d

docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml logs -f --tail=200 app
curl -fsS http://127.0.0.1:10000/health
curl -fsS http://127.0.0.1:10000/ready
findmnt /mnt/ramdisk
df -h /mnt/ramdisk
```

Busca `mongodb_connected`, `master_ready`, `webhook_ready`, `child_bot_running` y `platform_ready`. Después prueba con un bot de prueba: sube un vídeo y confirma que aparezca `video_stored_with_thumbnail`, que todos salvo el emisor lo reciban y que el álbum siga agrupado.

Comprueba seguridad de puertos:

```bash
sudo ss -lntp | grep -E ':(22|80|443|8081|10000|27017)\\b'
```

Los puertos públicos esperados son 22, 80 y 443 (según tus reglas de firewall). 8081 debe seguir privado; 10000 solo en loopback; 27017 no debe tener un binding público.

## Nota

La migración con `logOut` cambia el servidor Bot API que puede usar cada token. No ejecutes el script varias veces por costumbre; úsalo para la ventana de corte y guárdate sus resultados sin capturar secretos. La validación automática al ingresar nuevos tokens también liberará la sesión de la API alojada antes de probar el token en la API local.
