# Corrección V6 — portada visible (cover) de vídeos

## Causa confirmada por la captura

El vídeo ya se estaba publicando en la sala, pero la vista seguía mostrando un fotograma del vídeo original (la playa) en lugar de la imagen de marca `assets/thumb.jpg`. El pipeline solo enviaba el parámetro `thumbnail`. Según la documentación vigente de Telegram Bot API, `thumbnail` puede ignorarse cuando el servidor genera su propia miniatura; el parámetro `cover` es el que permite especificar la portada visible del vídeo.

Fuentes oficiales:
- https://core.telegram.org/bots/api#sendvideo
- https://docs.aiogram.dev/en/latest/api/methods/send_video.html
- https://docs.aiogram.dev/en/latest/api/types/input_media_video.html

## Cambios

- El pipeline `SendVideo` adjunta `thumbnail` y `cover`, ambos usando `FSInputFile`/multipart; `cover` utiliza el JPEG configurable en `VIDEO_THUMBNAIL_PATH`.
- Los vídeos dentro de álbumes reciben también `cover` en `InputMediaVideo`.
- El log identifica `video_stored_with_thumbnail_and_cover` y el modo de carga.
- La entrega a las salas y al feed admin sigue copiando el mensaje del buffer; no envía el vídeo original como fallback.
- Se actualizan las pruebas para exigir que `cover` se adjunte en vídeos individuales y álbumes.

## Requisito de servidor

El servidor local Telegram Bot API debe reconocer el parámetro `cover` (Bot API 8.3 o posterior). Aiogram 3.31.0 soporta `SendVideo.cover` e `InputMediaVideo.cover`. Si el servidor local es anterior, puede ignorar el nuevo campo y seguirá mostrando un fotograma del vídeo, por lo que debe actualizarse ese servicio por separado.

## Validación

Se validaron sintaxis Python y la estructura del YAML. Las pruebas unitarias comprueban que video, thumbnail y cover se entregan al método como archivos multipart, incluido el flujo de álbumes. La confirmación final de que Telegram muestra la portada requiere una prueba real con la instancia Bot API del servidor.
