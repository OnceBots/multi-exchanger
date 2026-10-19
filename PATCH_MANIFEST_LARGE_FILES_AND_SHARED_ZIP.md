# Patch: archivos grandes, ZIP compartido y mensajes coherentes

## Cambios

- Añadido `docker-compose.large-files.yml`, que activa el modo local del Telegram Bot API, comparte su volumen de datos con la app y deja la API en la red interna de Docker.
- Añadida `docs/LARGE_FILES_1GB.md` con configuración, migración segura y diagnóstico.
- Actualizados los valores de ejemplo para credenciales de Telegram API.
- Los tipos de adjunto compatibles (vídeo, foto, documento, animación, audio, voz, vídeo circular, sticker y álbum) quedan habilitados por defecto.
- Migración versionada de la política antigua video-only: convierte el valor predeterminado heredado a la nueva política completa una sola vez, pero conserva los permisos personalizados administrados explícitamente.
- Corregida la detección de tamaño y nombre cuando un vídeo o audio se envía como documento.
- Corregidos bienvenida, ayuda, detalle de sala y panel de moderación para explicar que el ZIP es compartido por sala y puede incluir archivos de otros miembros.
- Eliminadas menciones al proveedor de alojamiento de los mensajes visibles del bot.
- Los errores por límite del Bot API preservan el mensaje original y explican el problema en términos aptos para usuarios.
- Las actualizaciones de webhooks se reclaman atómicamente con leases y tokens; también se deduplican las actualizaciones del Master, y los trabajos abandonados se recuperan periódicamente sin pisar el resultado de otro worker.
- Antes de copiar un ZIP compartido, se vuelve a comprobar la membresía actual de cada usuario elegible; quienes salieron o fueron retirados ya no reciben ese paquete.
- Se agregaron tests para evitar referencias al proveedor en mensajes visibles y para impedir que vuelva a prometerse un ZIP privado.

## Validación

Ejecutar `python -m compileall -q app tests` y la suite completa con las dependencias instaladas. Las pruebas con archivos reales de 500 MB–1 GB requieren un despliegue de integración con un Bot API local en modo `--local`, espacio de disco suficiente y un canal buffer de prueba.
