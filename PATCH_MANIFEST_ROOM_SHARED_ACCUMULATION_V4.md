# Patch V4 — acumulación común por sala, cuota personal y migración segura

Base de despliegue: `main` con el sistema de empaquetado V3 y el control de referidos (`b4a8bae` / commits posteriores que lo contengan).

## Comportamiento

- Cada sala acumula en común la multimedia aceptada de todos sus miembros; los ZIP no son individuales.
- La cuota sí es personal: para recibir una copia privada del ZIP común, cada usuario debe aportar 10 vídeos únicos correctamente guardados dentro de la ventana móvil de 60 minutos.
- Los ZIP finales se publican y permanecen en el canal `storage_channel_id`; se copian únicamente a los miembros elegibles y a los administradores configurados.
- Los vídeos idénticos se deduplican por SHA-256 en la sala. Duplicados y descargas fallidas no cuentan para la cuota.
- Se mantiene una sola tarjeta de progreso por sala/miembro. La edición, consolidación y limpieza de esa tarjeta se coordinan mediante un bloqueo por sala para evitar que una tarea vieja borre o recree la tarjeta vigente.
- En el arranque se migran paquetes activos del antiguo modo por usuario a segmentos de sala compartidos. La migración divide los ítems por el límite configurado (1800 MiB por ZIP) en vez de juntar todo en un solo segmento sin límite.
- Los archivos que continúan viviendo en directorios de paquetes anteriores se conservan mientras cualquier segmento activo dependa de ellos. Solo se limpian cuando todas las entregas que los necesitan terminaron.
- Si un elemento heredado supera el límite por archivo, el paquete antiguo se conserva intacto y se notifica al administrador; no se descarta contenido automáticamente.

## Archivos del overlay

- `app/bot/child_handlers.py`
- `app/bot/manager.py`
- `app/db/indexes.py`
- `app/db/repositories/room_packages.py`
- `app/services/room_package_service.py`
- `docs/ROOM_MEDIA_ZIP_PACKAGING.md`
- `tests/test_room_package_service.py`
- `PATCH_MANIFEST_ROOM_SHARED_ACCUMULATION_V4.md`

## Validación local

- `python3 -m compileall -q app tests`
- Pruebas focalizadas de paquetes, referidos y repositorio de medios: pasan; la prueba AES-256 se omite aquí si `pyzipper` no está instalado.
- La suite completa necesita Aiogram en el entorno de prueba; sin esa dependencia, la colección de pruebas relacionadas con webhooks/API falla antes de ejecutar los casos.

## Aplicación

Aplicar el overlay en la raíz del repositorio local, conservar `.env` y los ajustes locales de Vultr, revisar `git diff --check` y los cambios, hacer commit/push y luego actualizar Vultr con `git pull --ff-only`, validación de Compose, `build app` y recreación solo de `app`.
