# Patch V6 — descarga multimedia fiable y errores diagnosticables

## Objetivo

Corregir el manejo de los archivos que fallan antes de guardarse en Vultr. El mensaje original se conserva hasta que la copia se descarga, se verifica, se guarda en el volumen de paquetes y su registro persistente queda confirmado.

## Cambios

- `getFile` ya no llama a `response.raise_for_status()` antes de leer el cuerpo JSON. Los errores HTTP 400 ahora conservan la descripción que devuelve Telegram (por ejemplo, identificador rechazado o límite de tamaño) en vez de quedar reducidos a `HTTPStatusError`.
- Reintentos limitados para timeouts/errores de conexión y respuestas temporales 408, 425, 429 y 5xx, con espera incremental y límite de tiempo.
- Resolución local del `file_path` reintentada brevemente ante una posible carrera entre `getFile` y la aparición del archivo en el volumen montado.
- Mensajes distintos para `file_id` rechazado, límite de tamaño, fallo temporal del Bot API, ruta local ausente, disco sin espacio, permisos y descarga incompleta.
- Persistencia y logs sanitizados: no se guardan excepciones HTTPX completas, que pueden incluir la URL del método Bot API con el token en el path.
- Se baja `httpx`/`httpcore` a `WARNING` para impedir que INFO imprima URLs con credenciales de bot.
- Los errores permanentes no se reintentan sin fin: si Telegram confirma que un `file_id` no es válido, el original permanece en el chat y el miembro recibe instrucciones de volver a enviar el archivo. Un identificador que Telegram rechaza no se puede reparar con reintentos.

## Archivos

- `app/services/room_package_service.py`
- `app/logging_config.py`
- `tests/test_room_package_service.py`
- `docs/MEDIA_DOWNLOAD_FAILURES.md`
- `PATCH_MANIFEST_DOWNLOAD_RELIABILITY_V6.md`

## Validación local

- `python3 -m compileall -q app tests` pasa.
- `pytest -ra tests/test_room_package_service.py tests/test_local_api_file_path_mapping.py tests/test_room_referral_policy.py tests/test_media_repository_append_group.py tests/test_rooms_repository.py`: 55 pruebas aprobadas y 2 omitidas (faltan `pyzipper` y Aiogram en este entorno de empaquetado).
- La suite completa no se pudo recolectar en este entorno porque no está instalado Aiogram; no debe interpretarse como una prueba end-to-end en Telegram/Vultr.

## Despliegue

Aplicar este overlay a la raíz del repositorio, mantener `.env` y `assets/thumb.jpg`, revisar el diff, hacer commit/push y luego `git pull --ff-only`, `docker compose config -q`, `build app` y recrear solo `app`. No borrar volúmenes ni el directorio de datos del Bot API.
