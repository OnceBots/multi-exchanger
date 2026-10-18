# Empaquetado de multimedia por sala

## Comportamiento

- La sala acumula únicamente los formatos que habilite el administrador del bot.
- Cada mensaje multimedia se registra primero en MongoDB. El worker descarga el archivo a un volumen persistente de la aplicación, comprueba el tamaño, hace `fsync` y confirma la ruta en MongoDB.
- Solo después de confirmar esa copia local intenta borrar el mensaje original del usuario en el chat privado con el bot. Si falla la descarga, la validación o el registro, el mensaje original queda visible.
- El ZIP se cierra al llegar a 1800 MiB de contenido reservado/real o tras 60 segundos sin nuevos archivos y sin descargas pendientes. El máximo de 1800 MiB queda deliberadamente por debajo del límite de subida del Bot API local para dejar margen a cabeceras y cifrado.
- La creación del ZIP usa WinZip AES-256 y `ZIP_STORED` (sin recomprimir vídeos ya comprimidos). Se puede extraer con 7-Zip o WinRAR.
- Se publica una copia fuente en el canal buffer privado y después se copia el mismo mensaje a los miembros de la sala y a los administradores habilitados. La copia final tiene `protect_content=True` cuando Telegram admite esa opción.
- Los miembros ven un único mensaje de progreso, actualizado automáticamente y con botón para consultar manualmente. Solo los usuarios incluidos en `ADMIN_IDS` ven el botón para cerrar el paquete manualmente.
- Si una entrega falla, se conserva el ZIP, la copia fuente y el estado en MongoDB para reintentar. Después de entregar a todos, se borran los payloads locales; los registros de archivos/entregas expiran a los 30 días y el resumen exitoso a los 90 días. Los paquetes con errores no expiran automáticamente.

## Configuración obligatoria antes de aceptar archivos

1. En el bot Master, `📦 Configurar canal de almacenamiento` (o `/storage_channel`), configurá el ID numérico de un canal privado.
2. Añadí cada bot hijo a ese canal con permisos para publicar y borrar sus propios mensajes. El canal debe estar restringido a administradores; el mensaje fuente se publica sin protección para que Telegram permita copiarlo, y solo las copias de destino se marcan como protegidas.
3. La app debe tener el volumen persistente `package-data` montado en `/var/lib/multi-exchanger/packages`.
4. En Vultr, el Bot API local debe estar realmente iniciado con `--local`, y la app necesita lectura del directorio del Bot API montado en `/srv/tg-bot-api-data`.

Si no hay un `storage_channel_id` numérico configurado, el bot rechaza el archivo antes de registrarlo y conserva el mensaje original. No desactives la comprobación para “forzar” el empaquetado.

## Protección contra bloqueos y pérdida de datos

- Webhooks solo registran metadata y encolan trabajo; las descargas, el cifrado ZIP y las copias se ejecutan en tareas de fondo.
- Hay límites globales para descargas, creación de ZIP, fan-out y actualizaciones de estado. Las actualizaciones de progreso se procesan en lotes de 50 miembros.
- Se comprueba el espacio libre antes de descargar y antes de crear el ZIP, dejando una reserva de 512 MiB para el sistema.
- Los items se deduplican por bot/chat/mensaje. El supervisor recupera trabajo pendiente y reconcilia contadores de MongoDB tras interrupciones.
- Los originales no se borran si la descarga local o la confirmación duradera fallan. No se borra a mano el cache del directorio del Bot API; ese directorio lo administra su servicio.
- Los paquetes usan AES-256. En este primer despliegue la contraseña se incluye en el caption del paquete para que el usuario pueda abrirlo sin otro flujo; por tanto, no compartas/reenviés el mensaje de la contraseña por separado, y ten en cuenta que reenviar el mensaje completo también comparte la contraseña del ZIP.

## Variables

- `MEDIA_PACKAGE_DIR=/var/lib/multi-exchanger/packages`
- `MEDIA_PACKAGE_MAX_BYTES=1887436800` (1800 MiB)
- `MEDIA_PACKAGE_IDLE_SECONDS=60`
- `MEDIA_PACKAGE_WORKERS_PER_BOT=2` (se limita internamente a un máximo de 4 workers por bot)

Los archivos con estado de entrega fallida se conservan para reintentos; si muchos destinatarios bloquean el bot o el canal buffer no funciona, revisá el panel de progreso y los logs antes de borrar datos a mano.
