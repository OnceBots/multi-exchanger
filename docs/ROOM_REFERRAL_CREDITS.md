# Referidos para crear salas

## Regla

- Las cuentas incluidas en `ADMIN_IDS` crean salas sin consumir referidos.
- Cada sala nueva de un usuario común cuesta 10 créditos de referido.
- Un crédito se obtiene cuando un usuario realmente nuevo inicia el bot hijo mediante el enlace personal `https://t.me/<bot>?start=ref_<user_id>`.
- Cada usuario invitado cuenta como máximo una vez por bot, no puede referirse a sí mismo y no se le reasigna a otro invitador.
- Una sala antigua o creada por un administrador conserva `referral_cost=0`; no se cobran costos de manera retroactiva.
- Crear una sala desde el botón del bot o desde la API/Mini App pasa por `RoomService.create_room`, que aplica la misma regla de servidor.

## Saldo

El saldo disponible se deriva del total de referidos únicos menos 10 créditos por cada sala nueva registrada con `referral_cost=10`. No se mantiene un contador mutable separado que pueda duplicarse por reintentos. Las solicitudes de creación simultáneas del mismo usuario se serializan dentro del proceso de aplicación.

## Límites de la atribución

Telegram no puede demostrar que un usuario haya compartido un enlace en el selector de contactos. Por eso se acredita el referido cuando una cuenta nueva abre el enlace personal e inicia el bot con el parámetro `ref_<user_id>`. Abrir el enlace con una cuenta que ya usaba el bot no da crédito.

## Despliegue

El índice único de `room_referrals` se crea al iniciar con `ensure_indexes`. El parche requiere reconstruir únicamente el servicio `app`. No eliminar volúmenes ni colecciones existentes.
