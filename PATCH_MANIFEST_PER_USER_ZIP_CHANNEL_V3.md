# Patch V3 — ZIP final en canal configurado y cuota individual

Base esperada: rama `main` del proyecto `OnceBots/multi-exchanger`, commit `2b7d48e` o posterior que ya incluya el sistema de empaquetado V2.

## Cambios

- Cada paquete nuevo queda identificado por `bot_id + room_id + sender_id`; los archivos de usuarios diferentes no se mezclan.
- La cuota se calcula por usuario y sala: 10 vídeos únicos, descargados y confirmados, con `staged_at` dentro de la ventana móvil de 60 minutos. Fotos, audio y otros formatos permitidos pueden acompañar el ZIP pero no suman a la cuota.
- La deduplicación se hace con SHA-256 dentro de bot/sala/usuario. Un vídeo idéntico omitido no suma para la cuota.
- Cuando se cumple la cuota, el ZIP se cierra por límite de tamaño o por 60 segundos de inactividad (cuando las descargas ya terminaron).
- El ZIP final se sube al `storage_channel_id` configurado y el mensaje fuente se conserva en el canal. Después se copia en privado al usuario que generó el paquete y a `ADMIN_IDS`/administradores habilitados; los paquetes nuevos no se distribuyen a todos los miembros de la sala.
- El mensaje de progreso muestra el contador personal `n/10`; se intenta fijar en el chat privado del propietario. El pin es best-effort y no detiene el procesamiento si Telegram lo rechaza.
- Los comandos de estado de un paquete personal se autorizan solo al propietario y a `ADMIN_IDS`.
- Los textos de inicio/ayuda se actualizaron para explicar la cuota por usuario y la entrega al canal.
- Se conserva la corrección de PyMongo Async para la reconciliación de contadores: esperar a `aggregate()` antes de llamar a `to_list()`.

## Archivos del parche

- `app/bot/child_handlers.py`
- `app/db/indexes.py`
- `app/db/repositories/room_packages.py`
- `app/services/room_package_service.py`
- `docs/ROOM_MEDIA_ZIP_PACKAGING.md`
- `tests/test_room_package_service.py`

## Instalación local y GitHub

1. Descomprimir el overlay en la raíz del repositorio local y reemplazar los archivos cuando se solicite. No reemplazar `.env`.
2. Ejecutar `git diff --check` y revisar `git status --short`.
3. Añadir solo los archivos del parche, hacer commit y `git push origin main`.
4. En Vultr, hacer `git pull --ff-only origin main`, validar `docker compose -f docker-compose.yml -f docker-compose.vultr.override.yml config -q`, reconstruir `app` y recrear únicamente ese servicio.

## Seguridad del canal

El caption del ZIP incluye la contraseña AES-256 para que el usuario pueda extraerlo. Por ese motivo, `storage_channel_id` debe apuntar a un canal privado restringido a los administradores. El mensaje del canal se conserva, incluso después de completar las entregas privadas.

## Validación

Pruebas focalizadas ejecutadas: 34 aprobadas, 2 omitidas por dependencias no instaladas en el entorno de empaquetado (`pyzipper` y Aiogram). La entrega real de un ZIP ya había sido comprobada por el usuario en Vultr; este parche no se ha desplegado aún contra producción.
