# Fix de tokens inválidos + reparación de webhooks

Este addon corrige dos problemas observados en Render:

1. Un token de child revocado o invalidado ya no queda en un ciclo de arranque. Se marca `AUTH_ERROR`, se deshabilita el registro para el supervisor y aparece `🔐 Actualizar token` en el panel Master para el owner/admin.
2. La reparación automática de webhooks tiene cooldown y registra `webhook_conflict_suspected` cuando un webhook desaparece repetidamente. Esto evita una tormenta de `setWebhook`.

## Aplicación

Copiar estos archivos sobre la versión actual:

- `app/core/enums.py`
- `app/db/repositories/bots.py`
- `app/bot/manager.py`
- `app/bot/master_handlers.py`

## Muy importante: causa externa del webhook

En los logs se observa que el webhook aparece configurado y posteriormente vuelve a quedar vacío. Eso normalmente significa que **otro proceso/instancia está usando el mismo token y está llamando a `deleteWebhook` o iniciando polling**.

Después de desplegar este fix, debe existir una sola instancia de cada token:

- Render: un único Web Service para este proyecto.
- No ejecutar simultáneamente `aprovebot.py`, `iobot.py` ni otro bot antiguo con esos mismos tokens.
- Si hay un segundo servicio Render conectado a la misma colección Mongo y/o tokens, detenerlo.
- En producción `MODE=webhook`.

## Tokens AUTH_ERROR

Para cada bot afectado:

1. Abrir el bot en Master.
2. Pulsar `🔐 Actualizar token`.
3. Obtener el token vigente desde `@BotFather`.
4. Enviar el token al Master.
5. El sistema comprueba que el token pertenece al mismo `bot_id`.
6. Se cifra, se guarda y el bot vuelve a arrancar.

Los datos existentes del bot no se eliminan.
