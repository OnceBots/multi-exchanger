# Parche incremental: Bot API local + thumbnails para Vultr

Este archivo describe el overlay de cambios. Extrae el ZIP desde la raíz del repositorio para conservar la estructura de carpetas. No contiene el proyecto completo ni modifica `.env` automáticamente.

## Archivos incluidos

- `.env.example`: nuevas variables para Bot API local, canal buffer, tmpfs y thumbnail.
- `Dockerfile`: incluye `assets/` en la imagen de la aplicación.
- `docker-compose.yml`: publicación del puerto de aplicación solo en loopback y configuración orientada a MongoDB externo ya existente.
- `docker-compose.vultr.override.yml`: conecta la aplicación y el Bot API local mediante una red Docker externa compartida y monta el volumen de archivos en solo lectura.
- `app/config.py`: carga y valida la configuración de Vultr.
- `app/bot/api_client.py`: cliente `AiohttpSession`/`TelegramAPIServer.from_base` y mapeo entre rutas del Bot API y las visibles para Python.
- `app/bot/manager.py` y `app/services/token_service.py`: usan el cliente configurado para Master, bots hijos y validación de tokens; conservan el manejo de lifecycle/recuperación de webhook incorporado en la última versión de referencia utilizada.
- `app/services/media_service.py`: añade metadatos básicos (tamaño/nombre) al job de vídeo sin cambiar reglas de sala.
- `app/services/broadcast_service.py`: envía vídeos/álbumes mediante copia protegida desde el canal buffer y respeta el espaciado/reintentos existentes.
- `app/services/video_thumbnail_pipeline.py`: procesa una vez vídeos y vídeos dentro de álbumes, copia desde el volumen del Bot API en hilo hacia tmpfs, agrega thumbnail y limpia archivos temporales.
- `app/services/bot_lifecycle.py`: conserva el texto de notificación cuando Telegram devuelve `Unauthorized` porque el usuario eliminó su bot.
- `assets/thumb.jpg` y `assets/README.md`: thumbnail de muestra que debes reemplazar por la corporativa.
- `VULTR_LOCAL_BOT_API_THUMBNAILS.md` y `VULTR_DEPLOY_GUIDE.md`: configuración, despliegue, red, migración de tokens y validación.
- `scripts/migrate_cloud_tokens_to_local_api.py`: dry-run por defecto y migración explícita de tokens existentes a la Bot API local sin imprimirlos.
- `tests/test_vultr_api_client.py`, `tests/test_video_thumbnail_pipeline.py` y `tests/test_video_broadcast_copy.py`: tests del addon.

## Configuración necesaria

Copia las nuevas variables de `.env.example` a tu `.env` y configura `CANAL_STORAGE_ID` con el ID del canal privado. No reemplaces `WEBHOOK_BASE_URL`/`APP_BASE_URL` por localhost: esos son los endpoints públicos HTTPS.

Cada bot hijo debe poder publicar en el canal buffer. Cambia `assets/thumb.jpg` por un JPEG válido, inferior a 200 KB y de máximo 320x320.

## Seguridad y límites

- El código habilita el pipeline solo si el canal, el modo local, la miniatura y el montaje tmpfs son válidos; de lo contrario usa el envío anterior para no perder contenido.
- `VIDEO_DELIVERY_DELAY_SECONDS` establece el espaciado asíncrono entre destinatarios (por defecto 0.04 s).
- El canal buffer conserva los mensajes; establecer una retención es importante para no agotar el almacenamiento NVMe.
- En un Bot API local debe completarse una vez la migración oficial de tokens desde el servidor cloud, llamando `logOut` en el Bot API oficial antes de cambiar al endpoint local.

## Validación realizada aquí

- `python -m compileall` sobre `app/` y `tests/`: correcto.
- Smoke tests aislados con dobles de Bot API: correcto para validación del thumbnail, mapeo de archivo local, limpieza de tmpfs, llamada de buffer y copia protegida de álbum.
- Pytest real no se pudo ejecutar en este entorno porque `aiogram` no está instalado y el índice de paquetes no estuvo accesible. Los tests incluidos quedan listos para correr en el VPS/CI con `requirements-dev.txt`.
