# Miniaturas predeterminadas con Local Telegram Bot API (Vultr)

## Requisitos

- `TELEGRAM_API_LOCAL_MODE=true`.
- `CANAL_STORAGE_ID` debe identificar el canal privado de almacenamiento.
- Cada bot hijo que publica vídeos debe ser administrador del canal y tener permiso para publicar mensajes.
- En Vultr, el override fija `TELEGRAM_API_SERVER_FILES_ROOT=/var/lib/telegram-bot-api`, que es el destino observado del montaje del contenedor `tg-local-api`.
- `TELEGRAM_API_HOST_FILES_ROOT=/srv/tg-bot-api-data` es el mismo volumen visto desde el contenedor `app`; no uses la ruta host `/root/tg-bot-api/data` dentro de la app.
- `VIDEO_THUMBNAIL_PATH` debe ser un JPEG válido, menor de 200 KB y de hasta 320x320 px.
- El servidor Telegram Bot API local debe ser compatible con el parámetro `cover` (Bot API 8.3 o posterior) para mostrar la imagen de marca como portada visible del vídeo.

## Cómo procesa los vídeos

1. El bot recibe el vídeo y resuelve `getFile` a la ruta que devolvió el Bot API local.
2. La ruta real del servidor mostrado en el despliegue tiene la forma `<directorio-de-datos>/<TOKEN_DEL_BOT>/videos/...`. Otras distribuciones pueden usar el ID numérico. El mapeo admite ambos formatos, pero solo dentro del directorio correspondiente al bot que está procesando el mensaje.
3. La app traduce la ruta absoluta desde `/var/lib/telegram-bot-api` a `/srv/tg-bot-api-data`, comprueba que el fichero exista y verifica que permanezca dentro del directorio del bot.
4. La app valida el tamaño y existencia del vídeo antes de procesarlo. Los tokens nunca se escriben en los mensajes de error.
5. El vídeo, el parámetro heredado `thumbnail` y el parámetro `cover` se cargan juntos como `FSInputFile` multipart al canal buffer. `thumbnail` puede ser reemplazada por un fotograma generado en el servidor; `cover` es la portada visible del mensaje y requiere Bot API 8.3 o posterior. Por eso el pipeline no usa `file_id`/`file://` para el archivo que se publica en buffer.
6. El vídeo se almacena una sola vez en el canal buffer. Las salas y el feed del administrador reciben una copia de ese mensaje ya procesado; el código no tiene un fallback que publique el vídeo directo sin portada procesada.
7. Si la configuración, la ruta, la publicación en buffer o la entrega fallan, el vídeo se bloquea. Se genera un log explicativo y el emisor recibe un aviso privado si el bot puede contactarlo.

El pipeline no necesita copiar el vídeo a `/mnt/ramdisk`; lee el fichero del volumen compartido y lo transmite como multipart dentro de la red privada Docker. Las variables antiguas `VIDEO_RAMDISK_PATH` y `VIDEO_REQUIRE_TMPFS` se mantienen por compatibilidad.

## Salas y feed de administración

- Las publicaciones de sala son multimedia-only: el caption original no se reenvía ni se copia.
- El feed de administración también recibe solamente multimedia, sin caption, texto de la publicación, ID/username del emisor ni mensaje auxiliar.
- Los mensajes de texto no se publican en una sala ni se incorporan al feed de multimedia.

## Diagnóstico después de enviar un vídeo de prueba

Ejecutar en Vultr:

```bash
docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml logs --since=5m --tail=300 app 2>&1 | grep -Ei 'room_media_queued|room_broadcast_started|room_broadcast_skipped_no_recipients|video_thumbnail_pipeline_unavailable|video_stored_with_thumbnail|video_thumbnail_pipeline_failed_delivery_blocked|room_media_rejected|room_delivery_failed|room_broadcast_completed|room_sender_notice_failed|Traceback|ERROR'
```

Interpretación:

- No hay `room_media_queued`: el handler no clasificó o no encoló el vídeo.
- `room_broadcast_skipped_no_recipients`: no hay otros miembros activos (el emisor se excluye de la distribución).
- `video_thumbnail_pipeline_unavailable`: revisar ajustes globales, canal buffer y miniatura predeterminada.
- `video_thumbnail_pipeline_failed_delivery_blocked`: revisar el traceback posterior; suele revelar el error al mapear el archivo, el permiso de publicación en el canal buffer o el formato de miniatura.
- `video_stored_with_thumbnail_and_cover`: el vídeo llegó al buffer con el cover personalizado enviado; luego el log de broadcast debe indicar las entregas. Si el mensaje muestra un fotograma del contenido en vez del diseño de marca, revisá que `tg-local-api` use una versión de Bot API 8.3 o posterior.
- `video_stored_with_thumbnail`: marcador compatible con versiones anteriores del log.
- `room_delivery_failed` o `room_broadcast_completed ... failed=N`: Telegram rechazó alguna entrega (por ejemplo, el destinatario no inició el bot o lo bloqueó).
