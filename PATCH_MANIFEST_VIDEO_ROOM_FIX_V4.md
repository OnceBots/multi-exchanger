> NOTA: Este manifiesto describe una revisión anterior del mapeo local y queda supersedido en este punto por `PATCH_MANIFEST_LOCAL_API_PATHS_V4.md`, que admite las estructuras observadas con token completo e ID numérico.

# Manifest V4 — corregir entrega de vídeos en salas

## Causa probable corregida

El mapeo del Bot API local estaba tratando el nombre de la carpeta de cada bot como si fuera el token completo. El servidor oficial del Telegram Bot API organiza su directorio de trabajo por el ID numérico del bot. Eso puede impedir que la app encuentre el fichero compartido, bloquear la miniatura y por política de seguridad detener la distribución a los miembros.

## Cambios

- Resuelve los ficheros locales mediante el ID numérico derivado del prefijo del token, sin usar ni imprimir el token como componente de ruta.
- Envía al Local Bot API el vídeo como URI `file://` verificado. Evita la copia completa en tmpfs y la retransmisión de todo el vídeo desde el contenedor de la app.
- Mantiene la miniatura predeterminada como upload multipart (`FSInputFile`), porque Telegram puede ignorar thumbnails si no se adjuntan como archivo.
- El buffer de almacenamiento sigue siendo obligatorio: si el procesamiento o la miniatura falla, el vídeo no se distribuye sin miniatura.
- Los miembros y el feed de administración reciben multimedia sin captions o texto de la publicación.
- Se añaden logs de encolado y de entrega para distinguir fallo de recepción, mapeo/ruta, publicación en buffer, sala sin destinatarios y errores de entrega individuales.

## Validación

- `python -m compileall -q app tests`: OK.
- Pruebas de ruta local, pipeline de miniatura, feed de administración y broadcast multimedia: 26 passed en pruebas focalizadas utilizando dobles de test para Aiogram. No equivale a una prueba de integración contra un Bot API local real.
- Antes del despliegue, ejecutar en Windows con el entorno real: `python -m pytest -q`.
- Hacer un envío real a una sala con al menos otro miembro y comprobar los logs indicados en `docs/MEDIA_DELIVERY_DIAGNOSTICS.md`.

## Compatibilidad

Preservar `.env`, `BOT_TOKEN_ENCRYPTION_KEY`, `MONGO_URI`, las copias de seguridad y `assets/thumb.jpg` local si fue personalizada.
