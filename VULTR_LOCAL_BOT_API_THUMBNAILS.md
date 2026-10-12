# Vultr: Bot API local + thumbnail pipeline

Este parche usa el servidor Bot API local para todos los bots y añade un pipeline específico para **vídeos individuales y álbumes mixtos de fotos/vídeos**. Cada vídeo se vuelve a subir una sola vez al canal buffer con la miniatura corporativa; las publicaciones se copian a los destinatarios preservando el orden del álbum.

## Variables nuevas

```env
TELEGRAM_API_BASE_URL=http://127.0.0.1:8081
TELEGRAM_API_LOCAL_MODE=true
TELEGRAM_API_TIMEOUT_SECONDS=7200
TELEGRAM_API_SERVER_FILES_ROOT=/root/tg-bot-api/data
TELEGRAM_API_HOST_FILES_ROOT=/root/tg-bot-api/data
CANAL_STORAGE_ID=-1001234567890
VIDEO_RAMDISK_PATH=/mnt/ramdisk
VIDEO_REQUIRE_TMPFS=true
VIDEO_THUMBNAIL_PATH=assets/thumb.jpg
VIDEO_DELIVERY_DELAY_SECONDS=0.04
VIDEO_MAX_UPLOAD_BYTES=2000000000
VIDEO_RAMDISK_RESERVE_BYTES=536870912
```

`WEBHOOK_BASE_URL` y `APP_BASE_URL` deben seguir usando la URL pública HTTPS de tu VPS/dominio. `TELEGRAM_API_BASE_URL` es solamente la URL privada del Bot API local; no reemplaces la URL pública de webhooks por localhost.

## Configuración del canal buffer

1. Crea un canal privado de almacenamiento.
2. Agrega como administradores a **todos los bots hijos que retransmiten vídeos** y permíteles publicar mensajes.
3. Coloca su ID numérico `-100...` en `CANAL_STORAGE_ID`.
4. Los mensajes de buffer no se borran automáticamente: el canal funciona como archivo. Define una política de retención/limpieza según el volumen de vídeo.

## Archivos y tmpfs

- Se incluye `assets/thumb.jpg` como thumbnail de muestra. Reemplázalo por tu thumbnail corporativo. Debe ser JPEG, menor a 200 KB y de hasta 320x320 píxeles.
- Las peticiones normales al Bot API mantienen timeout de 60 s; `TELEGRAM_API_TIMEOUT_SECONDS` solo se aplica a subidas potencialmente grandes al buffer. El usuario del proceso de Python debe poder escribir en `/mnt/ramdisk`. El pipeline verifica que sea `tmpfs` (salvo que desactives explícitamente `VIDEO_REQUIRE_TMPFS`). Comprueba el montaje con `findmnt /mnt/ramdisk` y el espacio con `df -h /mnt/ramdisk`.
- Si la aplicación también se ejecuta dentro de Docker, monta `/mnt/ramdisk` dentro de ese contenedor y usa una `TELEGRAM_API_BASE_URL` que sea accesible desde ese contenedor (no uses `localhost` salvo que Bot API esté en el mismo namespace de red). Ajusta las dos raíces de archivos del wrapper a rutas visibles desde la aplicación.
- `TELEGRAM_API_SERVER_FILES_ROOT` debe coincidir con la raíz de rutas que devuelve `getFile` en el servidor local; `TELEGRAM_API_HOST_FILES_ROOT` es la ruta equivalente que ve el proceso de la app. Si ambas rutas son iguales, deja los valores por defecto.
- Mantén `WEB_CONCURRENCY=1`/un único worker de Uvicorn: el límite de RAM disk usa un semáforo compartido en memoria y el resto del manager también mantiene runtimes en memoria.

## Flujo aplicado a vídeos individuales y álbumes

1. El vídeo entra en la cola existente, sin bloquear el webhook.
2. Un worker copia de forma asíncrona el archivo desde el volumen local compartido del Bot API a tmpfs (sin bajar el archivo por HTTP), validando tamaño/espacio disponible.
3. Se sube una sola vez al canal buffer con `thumbnail=FSInputFile(...)`.
4. Cuando Telegram devuelve el mensaje con el nuevo `file_id`, el archivo temporal se elimina.
5. Cada miembro salvo el emisor recibe `copy_message` del mensaje almacenado con `protect_content=True`; entre destinos hay un espaciamiento configurable de 0.04 s y se respeta `TelegramRetryAfter`.
6. Si falla el thumbnail o buffer, se registra el error y se usa la retransmisión previa para no perder el contenido.

Para álbumes mixtos, se vuelven a subir juntos al buffer (sin dividirlos) y luego se usa `copy_messages` en cada destinatario para preservar la agrupación y el orden. Si el álbum excede el límite o el espacio seguro del tmpfs, se usa el camino anterior de `send_media_group` sin thumbnail personalizada en ese envío.

## Migración de bots ya existentes al Bot API local

Telegram requiere llamar a `logOut` en el Bot API oficial antes de cambiar ese bot al servidor local. Haz esa transición una vez por cada token siguiendo el procedimiento oficial; no automatices `logOut` en cada arranque. Después, valida un bot de prueba antes de migrar todos.

## Prueba después de desplegar

- En logs, busca `video_stored_with_thumbnail` una vez por vídeo recibido.
- Verifica entregas por `copy_message` y el total de miembros excluyendo al emisor.
- Comprueba que no quedan carpetas `video-thumb-*` tras terminar.
- Comprueba `df -h /mnt/ramdisk` bajo carga.
- Si aparece `video_thumbnail_pipeline_failed_fallback`, revisa permisos del canal buffer, el mapa de rutas del Bot API y la miniatura. El bot entrega por el pipeline anterior mientras se corrige.

## Si la aplicación también corre dentro de Docker

El `localhost` dentro de `app` apunta al propio contenedor, no al host. Como punto de partida hay un override opcional: `docker-compose.vultr.override.yml`.

1. Confirma que el host puede leer los archivos de `/root/tg-bot-api/data` y que el usuario `appuser` del contenedor puede atravesar/leer el volumen. Ajusta permisos/grupo sin hacer esos archivos escribibles por la aplicación.
2. Asegúrate de que el puerto 8081 sea alcanzable desde el gateway Docker. Si publicas el puerto en una interfaz accesible desde contenedores, bloquea el acceso externo al 8081 mediante firewall; no expongas el Bot API local a Internet.
3. El bind mount de `/mnt/ramdisk` debe seguir siendo `tmpfs` dentro del contenedor. Verifícalo con `docker compose exec app findmnt /mnt/ramdisk` y `docker compose exec app df -h /mnt/ramdisk`.
4. Arranca con `docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml up -d --build`. Si el Bot API ya está en una red Docker compartida, es preferible usar el nombre DNS del servicio en esa red en vez de `host.docker.internal`.

No hace falta aplicar este override cuando `app.main` corre directamente en Ubuntu como servicio systemd.
