# Diagnóstico y optimización de entrega multimedia

## Política de mensajes

- Las salas reciben solamente multimedia. No se copia `caption` ni se envía un mensaje de texto companion.
- El feed de moderación de los administradores también es multimedia-only: no recibe caption, usuario ni texto asociado con la publicación.
- Los errores de entrega se notifican por privado al emisor cuando se puede; nunca se colocan en la sala o en el feed de admin.

## Flujo de video rápido con Local Bot API

1. El hijo obtiene `file_path` del Bot API local.
2. La app valida que el archivo exista bajo el volumen compartido y dentro del subdirectorio del **ID numérico del bot**. El Bot API local organiza sus datos por ID de usuario del bot, no por el token secreto.
3. Si `file_path` es una ruta absoluta válida dentro de `TELEGRAM_API_SERVER_FILES_ROOT`, se pasa como URI `file://` al propio Bot API local. Así se evita copiar el video completo a `/mnt/ramdisk` y retransmitir todos sus bytes entre los dos contenedores.
4. La miniatura predeterminada continúa como un `FSInputFile` pequeño en multipart para que Telegram la aplique. No usar `file_id` para la miniatura: Telegram ignora ese valor para thumbnails personalizados.
5. El Bot API sube el video una sola vez al canal de almacenamiento; los destinatarios reciben copias de ese mensaje con `copy_message`/`copy_messages`.
6. Si no se valida la ruta local, el pipeline usa un `FSInputFile` seguro como fallback. Si falla el buffer, no se entrega el video sin miniatura.

## Requisitos de producción

- El canal de almacenamiento se configura desde Master → Configuración global → Configurar canal de almacenamiento.
- Cada bot hijo que retransmite videos necesita permiso para publicar en ese canal.
- `TELEGRAM_API_LOCAL_MODE=true`.
- `TELEGRAM_API_SERVER_FILES_ROOT` debe coincidir con la ruta absoluta que devuelve `getFile` dentro del contenedor del Bot API local.
- `TELEGRAM_API_HOST_FILES_ROOT` debe apuntar al mismo volumen montado dentro del contenedor de la app (en el override de Vultr: `/srv/tg-bot-api-data`).
- `VIDEO_THUMBNAIL_PATH` debe ser un JPEG válido, menor de 200 KB y de dimensiones máximas 320x320.
- El modo rápido no necesita copiar el video a tmpfs. Las variables antiguas `VIDEO_RAMDISK_PATH` y `VIDEO_REQUIRE_TMPFS` se conservan por compatibilidad con `.env`, pero la ruta local optimizada ya no depende del staging temporal.

## Logs después de enviar un video de prueba

Enviar un video a una sala que tenga al menos otro miembro y consultar inmediatamente:

```bash
docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml logs --since=5m --tail=300 app 2>&1 | grep -Ei 'room_media_queued|room_broadcast_started|room_broadcast_skipped_no_recipients|video_thumbnail_pipeline_unavailable|video_stored_with_thumbnail|video_thumbnail_pipeline_failed_delivery_blocked|room_media_rejected|room_delivery_failed|room_broadcast_completed|room_sender_notice_failed|Traceback|ERROR'
```

Interpretación:

- No aparece `room_media_queued`: el handler no clasificó/encoló el video.
- `room_broadcast_skipped_no_recipients`: no había otros miembros activos además del emisor.
- `video_thumbnail_pipeline_unavailable`: indica variables de configuración faltantes o miniatura inválida.
- `video_thumbnail_pipeline_failed_delivery_blocked`: el fallo real del pipeline aparece con su traceback inmediatamente después; verificar canal de almacenamiento, permisos y mapeo de rutas.
- `video_stored_with_thumbnail`: el archivo llegó al canal buffer; si luego falla, revisar `room_delivery_failed` para las entregas a miembros.
- `room_broadcast_completed ... failed=N`: el log resume cuántos destinatarios fallaron.

## Relación con Screenshotit

El repositorio público de Screenshotit describe generación de capturas de videos sin descargar el archivo completo y está publicado bajo GPL-3.0. Este proyecto no copia código de ese bot ni depende de sus servicios privados: aplica el principio de evitar descargas/copias innecesarias usando el Local Bot API Server. Como aquí la miniatura pedida es una imagen predeterminada, extraer frames con FFmpeg no acelera la preparación y haría algo distinto (miniaturas tomadas del contenido del video). Si más adelante se quiere generar una captura real del video, se puede agregar como modo opcional independiente.
