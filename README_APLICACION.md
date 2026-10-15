# Aplicar corrección de rutas del Telegram Bot API local

Esta actualización corrige el mapeo de fotos y archivos del Bot API local. No cambia la clave Fernet, los tokens almacenados en MongoDB, los webhooks ni la configuración de acceso del Master.

## Por qué se corrige

En la inspección real del VPS se verificó el montaje:

- Host: `/root/tg-bot-api/data`
- Contenedor: `/var/lib/telegram-bot-api`
- El archivo está guardado bajo `<raíz>/<token-del-bot>/photos/file_1.jpg`.
- El handler recibía `photos/file_1.jpg`, una ruta relativa. El wrapper SimpleFilesPathWrapper por defecto requiere una ruta relativa a una raíz absoluta y fallaba. La alternativa `/file/bot...` devolvió HTTP 404, que es esperable para una ruta absoluta/servidor local en `--local`.

## Pasos en Windows o en Vultr

1. Extraé el contenido del ZIP en la raíz del repositorio `multi-exchanger` (la carpeta que contiene `app/`).
2. Ejecutá primero una verificación sin cambios:

   ```bash
   python apply_local_api_path_fix.py --check
   ```

3. Si termina con `OK`, aplicá la corrección:

   ```bash
   python apply_local_api_path_fix.py
   ```

   El script hace copias de seguridad fuera de la carpeta del repositorio, no modifica `.env` y aborta si no encuentra la estructura esperada.

4. Validá:

   ```bash
   python -m compileall -q app tests
   python -m pytest -q tests/test_local_api_file_path_mapping.py tests/test_vultr_api_client.py
   git diff --check
   git diff --stat
   ```

5. Revisá el diff y subí solamente archivos de código/documentación del parche (no `.env`, secretos ni copias de seguridad):

   ```bash
   git add app/config.py app/bot/api_client.py app/bot/master_handlers.py app/services/video_thumbnail_pipeline.py .env.example tests/test_local_api_file_path_mapping.py
   git diff --cached --stat
   git commit -m "Fix local Bot API file path mapping"
   git push origin main
   ```

## Variables de rutas

Como el `.env` del despliegue puede tener overrides, comprobá que, si estas variables existen, coincidan con el montaje Docker:

```env
TELEGRAM_API_SERVER_FILES_ROOT=/var/lib/telegram-bot-api
TELEGRAM_API_HOST_FILES_ROOT=/root/tg-bot-api/data
```

El script solo actualiza los valores predeterminados en `app/config.py` y `.env.example`; no toca `.env` porque puede contener secretos y valores de producción.

## En el VPS después de publicar en GitHub

Si Git avisa que tus cambios locales serían sobrescritos, no uses `reset --hard` ni borres archivos. Primero guardá el diff y revisá `git status --short`; después integrá las modificaciones de forma explícita. Tras actualizar el código:

```bash
cd /root/bot_project
source venv/bin/activate
python -m compileall -q app tests
python -m pytest -q tests/test_local_api_file_path_mapping.py tests/test_vultr_api_client.py
systemctl restart telegram-multibot.service
journalctl -u telegram-multibot.service -n 60 --no-pager
```

La prueba con ficheros temporales valida el mapeo de rutas, pero la última comprobación debe hacerse en el VPS con Docker y el Bot API local reales.
