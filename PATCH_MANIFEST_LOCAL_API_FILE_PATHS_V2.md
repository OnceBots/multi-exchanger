# Histórico: mapeo de rutas Local Bot API V2 (SUPEDIDO)

Este documento describe una versión anterior de la corrección. Queda supersedido por `PATCH_MANIFEST_LOCAL_API_PATHS_V4.md`.

**No ejecutes los comandos de instalación antiguos de este documento y no restaures sus valores de `TELEGRAM_API_HOST_FILES_ROOT`.** El instalador `apply_local_api_path_fix.py` quedó deshabilitado deliberadamente para prevenir regresiones.

La configuración vigente de Vultr es:

```env
TELEGRAM_API_SERVER_FILES_ROOT=/var/lib/telegram-bot-api
TELEGRAM_API_HOST_FILES_ROOT=/srv/tg-bot-api-data
```

La implementación vigente acepta tanto directorios con token completo como directorios con ID numérico y comprueba que el archivo pertenezca al bot actual.
