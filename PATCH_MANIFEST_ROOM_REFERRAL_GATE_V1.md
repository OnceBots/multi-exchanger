# Patch V1 — bloqueo de creación de salas por referidos

## Política implementada

- `ADMIN_IDS` puede crear salas gratis.
- Los usuarios comunes necesitan 10 referidos válidos disponibles por sala.
- Un referido válido es un usuario nuevo que inicia el bot hijo mediante el enlace `?start=ref_<user_id>` de un invitador existente.
- Cada usuario referido puede atribuirse una sola vez por bot; no se aceptan autorreferidos ni reasignaciones.
- Los créditos se calculan como referidos únicos menos diez por cada sala creada pagando créditos (`referral_cost=10`). Las salas anteriores no reciben cargos retroactivos.
- La comprobación se aplica en `RoomService.create_room`, compartida por el botón de Telegram y la API/Mini App, para evitar saltarse la política mediante una llamada directa.
- La interfaz añade `🎁 Mis referidos`, un enlace personal, contador de créditos y un mensaje de bloqueo antes de abrir el asistente para crear una sala.
- Si la creación de una sala falla al registrar el miembro propietario, se elimina el registro parcial de la sala para no gastar créditos.

## Archivos

- `app/bot/child_factory.py`
- `app/bot/child_handlers.py`
- `app/bot/manager.py`
- `app/db/indexes.py`
- `app/db/repositories/room_referrals.py` (nuevo)
- `app/db/repositories/rooms.py`
- `app/services/room_service.py`
- `app/web/webapp.py`
- `tests/test_room_referral_policy.py` (nuevo)
- `docs/ROOM_REFERRAL_CREDITS.md` (nuevo)

## Consideraciones

- Los usuarios deben llegar desde el enlace personal para contar; el sistema no puede verificar que simplemente hayan usado el botón de compartir.
- Se necesita que el bot hijo mantenga el mismo repositorio Mongo y que `ADMIN_IDS` esté configurado correctamente.
- La exclusión de créditos se decide en el backend, nunca solo por ocultar el botón.
- Despliegue con copia de seguridad: `git pull`, validar Compose, construir y recrear únicamente `app`. No borrar volúmenes.

## Validación local

Se incluyen pruebas de bloqueo sin créditos, cargo de 10 créditos por sala, exención de `ADMIN_IDS` y dos solicitudes simultáneas del mismo usuario.
