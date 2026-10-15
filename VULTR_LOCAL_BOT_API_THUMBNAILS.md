# Miniaturas predeterminadas con Local Telegram Bot API (Vultr)

## Requisitos

- `TELEGRAM_API_LOCAL_MODE=true`.
- `CANAL_STORAGE_ID` debe identificar el canal privado de almacenamiento.
- Cada bot hijo que publica vídeos debe ser administrador del canal y tener permiso para publicar mensajes.
- `TELEGRAM_API_SERVER_FILES_ROOT` debe coincidir con la raíz que devuelve el Bot API local (por defecto `/var/lib/telegram-bot-api`).
- `TELEGRAM_API_HOST_FILES_ROOT` debe apuntar al volumen compartido dentro de `app` (en el override de Vultr: `/srv/tg-bot-api-data`).
- `VIDEO_THUMBNAIL_PATH` debe ser un JPEG válido, menor de 200 KB y de hasta 320x320 px.

## Cómo procesa los vídeos

1. El bot recibe el vídeo y resuelve `getFile` a la ruta que devolvió el Bot API local.
2. El Bot API local organiza los ficheros bajo un directorio con el **ID numérico del bot**. No se utiliza el token secreto como nombre de carpeta.
3. La app valida que la ruta y el fichero existan dentro del volumen compartido y estén dentro del directorio del bot correspondiente.
4. Cuando la ruta local se puede validar, la app envía al Bot API un URI `file://` para que el propio servidor lea el vídeo de su disco. Esto evita copiar el vídeo completo a tmpfs y volver a transferirlo entre contenedores.
5. La miniatura predeterminada sigue siendo un `FSInputFile` pequeño multipart. Telegram ignora las miniaturas personalizadas cuando no se cargan mediante multipart/form-data.
6. El vídeo se almacena una sola vez en el canal buffer. Los miembros reciben una copia de ese mensaje; el código no tiene un fallback que publique el vídeo directo sin miniatura.
7. Si configuración, ruta, publicación en buffer o entrega fallan, el vídeo se bloquea. Se genera un log explicativo y el emisor recibe un aviso privado si el bot puede contactarlo.

El pipeline optimizado no necesita copiar todo el vídeo a `/mnt/ramdisk`. Las variables antiguas `VIDEO_RAMDISK_PATH` y `VIDEO_REQUIRE_TMPFS` se mantienen por compatibilidad con el despliegue, pero no son un requisito de esta ruta de envío rápida.

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
- `video_stored_with_thumbnail`: el vídeo llegó al buffer con la miniatura adjunta; a continuación el log de broadcast debe indicar entregas a miembros.
- `room_delivery_failed` o `room_broadcast_completed ... failed=N`: Telegram rechazó alguna entrega (por ejemplo, el destinatario no inició el bot o lo bloqueó).
