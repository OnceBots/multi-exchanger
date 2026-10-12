# Patch: cambiar miniatura de videos desde el bot Master

Archivos incluidos:
- `app/bot/master_handlers.py`: botón de administrador, comando `/thumbnail` y recepción de foto.
- `app/services/thumbnail_settings.py`: normalización y guardado atómico de la imagen.
- `requirements.txt`: añade Pillow.
- `tests/test_thumbnail_settings.py`: pruebas de imagen y preservación de la miniatura previa ante error.

No incluye `.env`, credenciales ni datos de producción. No reemplaza todo el repositorio: es un parche para superponer sobre la misma versión del proyecto que está desplegada.

## Uso
1. Hacer una copia de seguridad del repositorio actual.
2. Extraer el ZIP en la raíz del repositorio y aceptar la sustitución de `app/bot/master_handlers.py` y `requirements.txt`.
3. Instalar dependencias: `pip install -r requirements.txt`.
4. Verificar: `python -m compileall -q app tests` y `pytest -q tests/test_thumbnail_settings.py`.
5. Commit y push a GitHub; después desplegar/reiniciar el servicio.
6. En el Master, como administrador, pulsar `🖼️ Miniatura de videos` o usar `/thumbnail`, y enviar la imagen como foto.

La imagen se escribe en la ruta de `VIDEO_THUMBNAIL_PATH` (por defecto `assets/thumb.jpg`) y se comparte por los runtimes de bots hijos que usan el mismo proceso y configuración. Solo afecta las próximas subidas procesadas. No cambia videos ya subidos. El pipeline existente mantiene la estrategia de almacenar el video procesado una vez y copiar el mensaje a los miembros.
