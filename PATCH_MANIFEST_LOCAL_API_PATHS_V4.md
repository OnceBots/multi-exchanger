> **Documento histórico:** este correctivo fue ampliado por `PATCH_MANIFEST_LOCAL_API_PATHS_V5.md`, que también resuelve carpetas retenidas tras un cambio de token. No usarlo como guía final de diagnóstico.

# Correctivo de rutas de archivos Local Telegram Bot API (V4)

## Causa comprobada en el servidor

La captura de Vultr muestra el volumen del Bot API montado en el host desde `/root/tg-bot-api/data` hasta `/var/lib/telegram-bot-api` en `tg-local-api`. Los archivos existentes aparecen dentro de una carpeta con el token completo del bot (`<token>/videos/file_*.mp4`). A la vez, la app veía `TELEGRAM_API_HOST_FILES_ROOT` vacío y `TELEGRAM_API_SERVER_FILES_ROOT=/srv/tg-bot-api-data`, una inversión de las rutas.

El arreglo anterior asumía solo carpetas con ID numérico, por eso no podía encontrar los archivos reales incluso si el montaje del volumen existía.

## Cambios incluidos

- `app/bot/api_client.py`: el resolvedor reconoce carpetas con token completo y con ID numérico; solo acepta ficheros existentes dentro del volumen compartido y de la carpeta del bot activo. Los mensajes de error no muestran el token.
- `docker-compose.vultr.override.yml`: fija explícitamente `TELEGRAM_API_SERVER_FILES_ROOT=/var/lib/telegram-bot-api` y `TELEGRAM_API_HOST_FILES_ROOT=/srv/tg-bot-api-data`, para evitar que valores erróneos de `.env` prevalezcan.
- `app/config.py` y `.env.example`: el valor predeterminado de la raíz visible desde la aplicación es `/srv/tg-bot-api-data`, no la ruta host `/root/tg-bot-api/data`.
- Pruebas nuevas para rutas absolutas y relativas con carpeta de token completo, además de conservar compatibilidad con carpeta de ID numérico y el rechazo de rutas de otros bots.
- Documentación corregida para reflejar el pipeline multipart realmente usado.

## Despliegue

Conservar `.env`, datos de MongoDB y el contenedor del Bot API. Después de incorporar el commit al repositorio, en Vultr ejecutar `git pull --ff-only origin main`, `docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml config -q`, construir `app` y recrear solo `app`. No recrear ni borrar `tg-local-api`.

## Validación disponible

- `compileall` de los módulos Python: correcto.
- Pruebas aisladas del mapeador con: ruta absoluta del servidor y token completo, ruta relativa con token completo, ruta absoluta/relativa con ID numérico y rechazo de carpeta de otro bot: correctas.
- YAML Compose parseado y raíces verificadas: correcto.
- No se pudo ejecutar el pytest completo en el entorno de empaquetado porque no está instalada la dependencia `aiogram`; debe ejecutarse en la imagen de la aplicación o en el entorno del proyecto con sus dependencias.
