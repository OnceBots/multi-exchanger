# Manifest — ZIP multimedia por sala V1

## Propósito

Sustituye la entrega de multimedia individual a salas lógicas por empaquetado en ZIP cifrado. No sustituye la configuración `.env` de cada instalación.

## Cambios

- `app/services/room_package_service.py`: workers, cola recuperable, validación y persistencia local, eliminación segura de originales, estados de progreso, cierres por tamaño/inactividad, AES-256, entrega y reintentos.
- `app/db/repositories/room_packages.py`, `app/db/indexes.py`: metadatos, deduplicación, entregas, recuperación y TTL de los payload records.
- `app/services/media_service.py`, `app/services/room_service.py`, `app/bot/child_handlers.py`: encolado de medios permitidos, política por `ADMIN_IDS`, estado de paquete y botones de administración.
- `app/bot/child_factory.py`, `app/bot/manager.py`: registro del servicio y tareas worker/monitor.
- `app/config.py`, `.env.example`, `requirements.txt`: límites configurables y dependencia `pyzipper`.
- `Dockerfile`, `docker-compose.yml`: permisos de directorio y volumen persistente `package-data`.
- `tests/test_room_package_service.py`: comprobaciones de guardas de canal, mapeo de archivos, estado, aislamiento por bot y manejo de fallos de descarga sin borrar el original; incluye test AES-256 condicional cuando `pyzipper` está instalado.
- `docs/ROOM_MEDIA_ZIP_PACKAGING.md`: configuración y operación.

## Despliegue

1. Configurar el canal buffer privado desde Master y otorgar permisos a los bots hijos.
2. Mantener el `.env` existente; los valores de paquetes tienen defaults seguros.
3. Actualizar/reconstruir únicamente `app` con Compose, sin borrar volúmenes ni recrear MongoDB/Bot API.
4. Probar con un vídeo pequeño y confirmar: archivo guardado, mensaje original borrado después de la confirmación, ZIP entregado y contraseña válida.

## Verificación local

- `python -m compileall -q app tests`: aprobado.
- Suite focalizada ejecutada: 30 passed, 2 skipped (un test requiere `pyzipper` y otro la sesión Aiogram que no está instalada en el entorno de empaquetado).
- El test real AES-256 está presente; requiere instalar `pyzipper==0.4.0` mediante `requirements.txt` durante el build. No pudo ejecutarse en el entorno de empaquetado por no tener conexión al índice de paquetes.
- No se ejecutó una prueba de extremo a extremo contra Telegram/Vultr desde el entorno de empaquetado.

- La ruta temporal por sala se normaliza; la ruta de destino se inicializa antes de empezar la descarga para que un fallo de `getFile` o del mapeo no sea ocultado por `UnboundLocalError`. El fallo se registra y el mensaje original permanece.
