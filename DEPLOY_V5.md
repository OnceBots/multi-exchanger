# Nota de migración: interfaz solo por chat

Esta versión elimina la Mini App y todas las referencias a `APP_BASE_URL`. La creación de bots hijos, salas y administración se realiza en el chat de Telegram mediante comandos y botones inline. Para recibir actualizaciones por webhook, `WEBHOOK_BASE_URL` sigue siendo una URL pública HTTPS obligatoria.
