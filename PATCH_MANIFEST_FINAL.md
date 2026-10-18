# Parche final: miniaturas obligatorias y permisos multimedia de salas

## Cambios incluidos

1. **Feed de administración:** un vídeo no se envía directamente desde el `file_id` original. El bot carga el vídeo y `assets/thumb.jpg` juntos como multipart al canal privado de almacenamiento y el feed copia el mensaje ya procesado. Los álbumes que contienen vídeo pasan por la misma ruta. Si el procesamiento falla, el feed no manda el vídeo original como alternativa.
2. **Salas solo vídeo por defecto:** al no existir una política guardada, se habilitan vídeos y se deshabilitan fotos, archivos, GIF y álbumes. Las salas existentes también obedecen la política global, por lo que los ajustes antiguos no pueden saltarse los permisos.
3. **Control exclusivo por `ADMIN_IDS`:** el botón `🛡️ Moderación` incorpora interruptores globales para vídeo, fotos, archivos, GIF y álbumes. Los cambios quedan persistidos en `config.room_media_policy` de MongoDB y afectan salas actuales y futuras. Un usuario normal no puede habilitar formatos enviando callbacks o modificando los permisos guardados de una sala.
4. **Archivos grandes:** el error de `getFile` “file is too big” ahora registra una explicación accionable que apunta al servidor Telegram Bot API local y al argumento `--local`. La variable `TELEGRAM_API_LOCAL_MODE=true` de la aplicación no activa por sí sola ese modo en el proceso externo del Bot API.
5. Se conservan el almacenamiento de las salas, los IDs de bot, los tokens cifrados, el canal de buffer y el resto de la arquitectura existente. No se incluye `.env` ni ningún secreto.

## Requisito crítico del servidor Bot API

El contenedor/proceso existente de Telegram Bot API debe ejecutarse realmente con `--local`. La documentación oficial indica que ese modo permite descargar archivos sin límite de tamaño y devolver la ruta local con `getFile`. También documenta que el thumbnail puede ignorarse si el vídeo no se carga como multipart, por eso la aplicación sube ambos archivos por multipart:

https://github.com/tdlib/telegram-bot-api#usage
https://docs.aiogram.dev/en/v3.25.0/api/methods/send_video.html

Antes de reemplazar o recrear ese servicio, consulta su comando y sus montajes; conserva el `--api-id`, `--api-hash`, `--dir`, el volumen de datos y el resto de parámetros actuales. Si falta `--local`, el código Python por sí solo no puede superar el rechazo del servidor al pedir `getFile` de vídeos grandes.

## Despliegue

1. Haz copia de seguridad del directorio del proyecto y de MongoDB.
2. Conserva el `.env` actual exactamente como está, especialmente `MONGO_URI`, `DB_NAME`, `BOT_TOKEN_ENCRYPTION_KEY`, `MASTER_BOT_TOKEN`, `WEBHOOK_SECRET`, `ADMIN_IDS`, `TELEGRAM_API_BASE_URL`, `TELEGRAM_API_SERVER_FILES_ROOT`, `TELEGRAM_API_HOST_FILES_ROOT`, `CANAL_STORAGE_ID` y `VIDEO_THUMBNAIL_PATH`.
3. Verifica que todos los bots hijos que publican vídeo sean administradores del canal privado indicado por `CANAL_STORAGE_ID`.
4. Reconstruye solo `app` usando ambos Compose files que ya utilizas en Vultr:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml config -q
   docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml build app
   docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml up -d --no-deps --force-recreate app
   ```

5. En el bot hijo, abre `🛡️ Moderación` con un usuario incluido en `ADMIN_IDS`. Deja todos los interruptores apagados salvo `🎬 Vídeos` para mantener la política predeterminada; habilita otros formatos únicamente cuando realmente los necesites.
6. Prueba un vídeo pequeño y luego uno superior a 20 MB. Comprueba que el feed del admin y los miembros reciban la miniatura configurada. Si aparece el error sobre `--local`, detén ahí las pruebas con vídeos grandes y corrige el servicio Telegram Bot API antes de volver a probar. Nunca se enviará el original como fallback.

## Validación realizada para este ZIP

- `python -m compileall -q app tests`: correcto.
- `node --check app/webapp/static/app.js`: correcto.
- 32 pruebas focalizadas de permisos, pipeline de miniaturas, mapeo de archivos del Bot API, difusión en salas y feed del administrador: todas pasan con dobles de Aiogram para probar la lógica aislada.
- No se pudo ejecutar el conjunto completo de pytest ni construir la imagen Docker en este entorno de empaquetado porque Aiogram no está instalado y el acceso al índice de paquetes no está disponible. Por eso estas pruebas focalizadas no sustituyen una prueba de integración real con Telegram. En el servidor, después de reconstruir `app`, corre `python -m pytest -q` si las dependencias de `requirements.txt` están instaladas.

## Comprobación imprescindible antes de probar vídeos grandes

El error histórico `Bad Request: file is too big` se origina en el servidor Telegram Bot API, no en el botón de miniaturas de la app. En Vultr puedes comprobar el comando sin mostrar variables de entorno ni hashes:

```bash
docker inspect --format '{{.Path}} {{json .Args}}' tg-local-api
```

Los argumentos deben incluir `--local`. Si no aparece, hay que añadirlo al comando original del servicio y recrear **solo ese servicio conservando sus argumentos, volúmenes y credenciales actuales**. `TELEGRAM_API_LOCAL_MODE=true` configura Aiogram; no activa el modo local dentro del contenedor externo. Consulta el README oficial: https://github.com/tdlib/telegram-bot-api#usage
