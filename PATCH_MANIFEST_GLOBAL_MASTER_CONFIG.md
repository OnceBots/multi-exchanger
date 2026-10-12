# Parche: configuración global desde Master (superadministrador)

Este ZIP es un parche para superponer en la raíz del repositorio `OnceBots/multi-exchanger` que ya tiene el pipeline de miniaturas instalado. No contiene `.env`, tokens ni datos de producción.

## Incluye
- El superadministrador se obtiene directamente del primer ID configurado en `ADMIN_IDS` (o `SUPER_ADMINS` como alias compatible). No hace falta crear `SUPER_ADMIN_ID`. Si solo usás `ADMIN_IDS=8983189714`, ese ID será el superadministrador global.
- Menú `⚙️ Configuración global` visible solo para el superadministrador configurado.
- Configuración del canal de almacenamiento desde el Master (`📦 Configurar canal`) y persistencia en MongoDB (`platform_settings`), sin editar `.env` ni reiniciar.
- Miniatura predeterminada cargada desde el Master; también queda reservada al superadministrador.
- El pipeline de thumbnail puede activarse después de configurar el canal desde el chat, sin necesitar que `CANAL_STORAGE_ID` esté presente al arrancar.
- Los usuarios normales mantienen la opción de crear sus bots hijos.

## Antes de aplicar
1. Hacé copia de seguridad del repositorio y de `.env`.
2. Extraé el ZIP en la raíz del repositorio y aceptá reemplazar los archivos.
3. En `.env`, verificá que `ADMIN_IDS=8983189714` (o tu ID real de Telegram). Si hay varios IDs, el primero será el superadministrador global; los demás conservan sus permisos de administrador normal. No agregues `SUPER_ADMIN_ID`.
4. En el VPS: `cd /root/bot_project && source venv/bin/activate && pip install -r requirements.txt && python -m compileall -q app tests`.
5. Reiniciá: `systemctl restart telegram-multibot.service` y revisá `journalctl -u telegram-multibot.service -n 100 --no-pager`.
6. Abrí el Master con el superadministrador y entrá en `⚙️ Configuración global`.
7. Configurá el canal usando su ID numérico. Cada bot hijo que procese videos debe ser administrador del canal con permiso para publicar; si no, el pipeline fallará y el bot usará la ruta de envío de respaldo.
8. Subí la miniatura como foto desde el menú global. Los próximos videos se almacenarán una sola vez con la miniatura y se distribuirán copiando el mensaje del canal; los archivos temporales locales se limpian al terminar el procesamiento.

## Verificación
- `python -m compileall -q app tests`
- `python -m pytest -q tests/test_thumbnail_settings.py tests/test_video_thumbnail_pipeline.py tests/test_video_broadcast_copy.py -o asyncio_mode=auto`

Nota: si el repositorio tiene cambios locales posteriores a este parche, revisá el diff antes de sobrescribirlos. La fuente de autorización global es el primer ID de `ADMIN_IDS`; no se necesita una variable adicional.
