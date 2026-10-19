# Archivos grandes (500 MB–1 GB o más)

La recepción de archivos grandes requiere que la aplicación use un Telegram Bot API Server que esté ejecutándose en **modo local**. `TELEGRAM_API_LOCAL_MODE=true` en la aplicación solo habilita el tratamiento de rutas locales; no activa por sí solo el modo local del servidor de Telegram.

En modo local, el Bot API permite descargar archivos sin límite de tamaño y subir archivos de hasta 2000 MB. Referencia oficial: <https://github.com/tdlib/telegram-bot-api#usage>.

## Opción A: API local administrada por este Compose

1. Copia `.env.example` a `.env` si todavía no existe.
2. Obtén `TELEGRAM_API_ID` y `TELEGRAM_API_HASH` desde <https://my.telegram.org> y complétalos en `.env`. No los compartas ni los publiques.
3. Revisa que `.env` también incluya los valores actuales de `MASTER_BOT_TOKEN`, `MONGO_URI`, `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET`, `BOT_TOKEN_ENCRYPTION_KEY`, `CANAL_STORAGE_ID` y `SUPER_ADMIN_ID`. No reemplaces tus secretos por los del ejemplo.
4. Valida la configuración y arranca con ambos archivos Compose:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.large-files.yml config -q
   docker compose -f docker-compose.yml -f docker-compose.large-files.yml up -d --build
   ```

Este overlay activa explícitamente `TELEGRAM_LOCAL=1`, mantiene los datos del Bot API en un volumen persistente, lo comparte en solo lectura con la aplicación y no publica sus puertos en la interfaz pública del servidor. Las raíces se configuran conjuntamente mediante `TELEGRAM_API_SERVER_FILES_ROOT` y `TELEGRAM_API_HOST_FILES_ROOT`; si las cambias, deben representar el mismo volumen desde ambas perspectivas.

**Importante al migrar:** no mantengas dos instancias del Bot API registradas simultáneamente para los mismos bots. Detén la instancia anterior y sigue el procedimiento oficial de migración de Telegram antes de cambiar el servidor al que están conectados los tokens. Si ya tienes un Bot API local funcional, normalmente debes corregir la configuración de esa misma instancia en lugar de crear otra. En ese caso, no uses la opción A sin planificar una migración de los bots.

## Opción B: ya tienes un Bot API local separado

En el servicio que ejecuta `aiogram/telegram-bot-api`, establece `TELEGRAM_LOCAL=1` (equivalente a iniciar `telegram-bot-api` con `--local`) y recrea ese servicio. La aplicación debe apuntar a esa misma instancia mediante `TELEGRAM_API_BASE_URL`, y ambas deben ver los mismos datos del Bot API: configura `TELEGRAM_API_SERVER_FILES_ROOT` con la ruta dentro del contenedor de la API y `TELEGRAM_API_HOST_FILES_ROOT` con la ruta equivalente montada en la aplicación. No ejecutes el overlay de la opción A si eso crea una segunda instancia para los mismos bots.

## Límites y espacio en disco

- Un archivo de 1 GB queda por debajo del límite por archivo del proyecto, siempre que `MEDIA_PACKAGE_MAX_BYTES` sea de al menos 1 GB (el valor de ejemplo es aproximadamente 1,8 GiB).
- El ZIP se divide en paquetes si el tamaño acumulado supera el límite configurado; no se intenta subir un ZIP mayor al límite seguro de Telegram.
- Durante el procesamiento coexisten la copia guardada y el ZIP en construcción. Para un vídeo de 1 GB, reserva al menos unos 2,5 GB libres para el archivo y su paquete, además del espacio necesario para el resto del sistema y otras tareas concurrentes.
- El mensaje original solo se elimina después de guardar y verificar la copia persistente. Si el servicio local, el disco o la verificación fallan, el original permanece y el archivo no se marca como añadido al ZIP.

## Diagnóstico de un error de tamaño

Si aparece «El servicio de descargas no está habilitado para archivos grandes», el original sigue en el chat, pero el Bot API usado por la aplicación no está aceptando `getFile` para ese tamaño. Revisa que el servicio real esté en modo local, que `TELEGRAM_API_BASE_URL` apunte a él y que la raíz de archivos esté compartida correctamente. No hace falta volver a enviar el archivo hasta corregir la configuración.
