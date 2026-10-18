# Estado de la corrección de rutas del Telegram Bot API local

La corrección está integrada en el código de este proyecto. **No ejecutes el instalador histórico** `apply_local_api_path_fix.py`: quedó deshabilitado porque podía restaurar valores de rutas incompatibles.

Consulta `PATCH_MANIFEST_LOCAL_API_PATHS_V5.md` para la causa verificada, los cambios y las validaciones.

## Rutas correctas en Vultr

El contenedor existente `tg-local-api` monta `/root/tg-bot-api/data` del host en `/var/lib/telegram-bot-api`. El contenedor `app` ve ese mismo volumen en `/srv/tg-bot-api-data`.

El archivo `docker-compose.vultr.override.yml` establece explícitamente:

```yaml
environment:
  TELEGRAM_API_SERVER_FILES_ROOT: /var/lib/telegram-bot-api
  TELEGRAM_API_HOST_FILES_ROOT: /srv/tg-bot-api-data
volumes:
  - /root/tg-bot-api/data:/srv/tg-bot-api-data:ro
```

No guardes la ruta host `/root/tg-bot-api/data` en `TELEGRAM_API_HOST_FILES_ROOT` dentro del contenedor de aplicación. Conserva `.env`, tokens, MongoDB y el contenedor de Bot API.
