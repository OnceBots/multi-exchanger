# Multi-Exchanger — guía operativa

## Paquetes multimedia por sala

Las salas agrupan la multimedia permitida por el administrador en paquetes ZIP protegidos con contraseña AES-256. El bot acumula hasta **1800 MiB por paquete** (aprox. 1,89 GB decimales / 1,76 GiB) y lo cierra al alcanzar el límite o tras 60 segundos sin contenido nuevo y sin descargas pendientes.

El progreso se publica en un mensaje actualizable en los chats privados de los miembros. Solo `ADMIN_IDS` puede cerrar el paquete manualmente o modificar la política de multimedia. El ZIP se entrega a los miembros que cumplen la cuota y a los administradores configurados.

**El mensaje original se borra únicamente después de descargar el archivo a almacenamiento persistente, sincronizarlo a disco y confirmar su ruta/tamaño en MongoDB.** Si falla la descarga, el registro o la verificación, el original se conserva. El ZIP y el trabajo pendiente permanecen en disco/MongoDB si no se completa la entrega.

Consulta [`docs/ROOM_MEDIA_ZIP_PACKAGING.md`](docs/ROOM_MEDIA_ZIP_PACKAGING.md) para configuración del canal buffer, persistencia, límites y operación.

## Requisito de entrega

Antes de aceptar multimedia, configurá un canal buffer privado desde el bot Master (`📦 Configurar canal de almacenamiento` o `/storage_channel`) y otorgá permisos de publicación a cada bot hijo. La aplicación rechaza los archivos mientras ese canal no esté configurado para no borrar mensajes que no podría empaquetar y entregar.

## Bot API local para archivos grandes

Para descargar archivos de 500 MB–1 GB, el servidor Bot API real debe arrancar en modo local. Configura `TELEGRAM_API_ID` y `TELEGRAM_API_HASH` en `.env` y usa `docker-compose.large-files.yml`; los comandos exactos y la migración segura están en [`docs/LARGE_FILES_1GB.md`](docs/LARGE_FILES_1GB.md). La API queda en la red interna de Docker y no se debe publicar el puerto 8081 en Internet.
