# Changelog

## Chat-only release
- Eliminada la Mini App, sus páginas HTML, autenticación WebApp y endpoints REST exclusivos de interfaz.
- El Master crea bots hijos dentro del chat: el usuario pulsa «Crear mi bot» y envía el token de BotFather en privado.
- Los bots hijos administran salas y funciones con mensajes, comandos y botones inline de Telegram.
- Se conserva el backend HTTP para health/readiness/metrics y los webhooks públicos de Telegram.
- Se conserva el parche de Vultr, Bot API local, miniaturas y difusión de multimedia.
