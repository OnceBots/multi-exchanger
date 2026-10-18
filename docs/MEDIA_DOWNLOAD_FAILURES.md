# Diagnóstico de fallos de descarga multimedia

El empaquetador diferencia los fallos transitorios de los rechazos permanentes de Telegram:

- `bot_api_transport_exhausted` / `bot_api_temporary_error_exhausted`: la conexión o el servicio Bot API no respondió tras varios intentos. Puede ser temporal.
- `telegram_file_id_rejected`: Telegram rechaza el identificador. El contenido debe reenviarse al mismo bot que lo recibió; no se puede arreglar un ID que Telegram considera inválido.
- `telegram_file_too_large_local_mode_check`: Telegram indica que el archivo es demasiado grande; comprobar que el contenedor Bot API se haya iniciado con `--local`.
- `local_bot_api_file_missing`: `getFile` devolvió una ruta, pero el archivo no apareció/leyó en el volumen compartido tras varios intentos. Revisar `TELEGRAM_API_SERVER_FILES_ROOT`, `TELEGRAM_API_HOST_FILES_ROOT` y el bind mount.
- `download_size_mismatch` / `empty_download`: la copia estaba vacía o incompleta; se conserva el mensaje original.
- `insufficient_disk_space` / `local_file_permission_denied`: problema del almacenamiento del servidor.

Por seguridad, los logs no deben imprimir URLs completas de Bot API porque el token aparece en la ruta. No compartir `.env`, tokens ni URLs del método `getFile`.

En todos los fallos permanentes el original permanece visible; el archivo no entra en el ZIP hasta que la copia y su registro estén confirmados.
