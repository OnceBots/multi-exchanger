# Empaquetado de multimedia compartido por sala

## Comportamiento vigente

- Cada sala mantiene un acumulador compartido: vídeos, fotos, audios y otros formatos que estén permitidos y hayan enviado **todos los miembros** entran en el mismo paquete de sala. Los archivos de usuarios distintos no se separan en ZIP personales.
- Cada mensaje multimedia se registra primero en MongoDB. El worker descarga el archivo a un volumen persistente de la aplicación, comprueba el tamaño, hace `fsync` y confirma la ruta en MongoDB.
- Solo después de confirmar esa copia local intenta borrar el mensaje original del usuario en el chat privado con el bot. Si falla la descarga, validación o registro, el mensaje original queda visible.
- La cuota es individual por usuario y sala: para recibir un ZIP compartido, cada persona debe haber aportado al menos 10 vídeos únicos correctamente guardados en la ventana móvil de 60 minutos. La cuota determina quién recibe una copia privada, no qué archivos entran al ZIP.
- Al cerrarse un paquete, el ZIP incluye el contenido único de todos los miembros que se haya acumulado en esa tanda. Se publica y conserva en el canal configurado `storage_channel_id`; después se copia al usuario que haya cumplido su cuota y a `ADMIN_IDS`/administradores habilitados. Los miembros que no cumplen la cuota no reciben copia privada aún. El canal de archivo guarda el ZIP común para administración.
- El ZIP se cierra al llegar a 1800 MiB de contenido reservado/real o tras 60 segundos sin nuevos archivos y sin descargas pendientes. Para cierre por inactividad o tamaño debe haber al menos un miembro elegible; de lo contrario el segmento queda `WAITING_QUOTA`. Un cierre manual por `ADMIN_IDS` puede archivar únicamente para el canal y administradores aunque nadie haya alcanzado la cuota.
- El máximo de 1800 MiB queda por debajo del límite de subida del Bot API local para dejar margen a cabeceras y cifrado. Si se alcanza el límite antes de la cuota, se crea otro segmento de la misma sala; los segmentos siguen acumulando contenido de todos los miembros y esperan la cuota cuando corresponda.
- La creación del ZIP usa WinZip AES-256 y `ZIP_STORED` (sin recomprimir vídeos ya comprimidos). Se puede extraer con 7-Zip o WinRAR.
- Los vídeos se comparan por SHA-256 dentro de la combinación bot/sala. Un vídeo idéntico ya guardado no se agrega dos veces ni cuenta para la cuota de quien lo envió; el mensaje original solo se elimina cuando el duplicado ha sido registrado y la copia previa está confirmada.
- La sala tiene una sola tarjeta visible de progreso por miembro. Se actualiza en el mismo mensaje y se intenta fijar en chats privados de los miembros. La consolidación y limpieza de tarjetas se serializan por sala y se releen los IDs actuales desde MongoDB para que la finalización de un segmento viejo no borre una tarjeta que ya se movió al segmento activo. Los segmentos por límite de tamaño pueden terminar en segundo plano sin crear tarjetas paralelas.
- Si una entrega falla, se conserva el ZIP, la copia fuente y el estado en MongoDB para reintentar. Después de entregar a todos los destinatarios elegibles, se borran los payloads locales; los registros de archivos/entregas expiran a los 30 días y el resumen exitoso a los 90 días. Los paquetes con errores no expiran automáticamente.

## Configuración obligatoria antes de aceptar archivos

1. En el bot Master, `📦 Configurar canal de almacenamiento` (o `/storage_channel`), configurá el ID numérico de un canal privado.
2. Añadí cada bot hijo a ese canal con permisos para publicar y borrar sus propios mensajes. El canal debe estar restringido a administradores; el mensaje fuente se publica sin protección para que Telegram permita copiarlo, y solo las copias privadas se marcan como protegidas.
3. La app debe tener el volumen persistente `package-data` montado en `/var/lib/multi-exchanger/packages`.
4. En Vultr, el Bot API local debe estar realmente iniciado con `--local`, y la app necesita lectura del directorio del Bot API montado en `/srv/tg-bot-api-data`.

Si no hay un `storage_channel_id` numérico configurado, el bot rechaza el archivo antes de registrarlo y conserva el mensaje original. No desactives la comprobación para “forzar” el empaquetado.

## Protección contra bloqueos y pérdida de datos

- Webhooks solo registran metadata y encolan trabajo; las descargas, el cifrado ZIP y las copias se ejecutan en tareas de fondo.
- Hay límites globales para descargas, creación de ZIP, fan-out y actualizaciones de estado. Las actualizaciones de progreso se procesan en lotes de 50 miembros.
- Se comprueba el espacio libre antes de descargar y antes de crear el ZIP, dejando una reserva de 512 MiB para el sistema.
- Los eventos se deduplican por bot/chat/mensaje y los vídeos por SHA-256 para evitar duplicar contenido. El supervisor recupera trabajo pendiente y reconcilia contadores de MongoDB tras interrupciones.
- Antes de aceptar archivos se ejecuta una migración idempotente de paquetes activos de la versión V3 por usuario al acumulador compartido por sala. La migración segmenta por el límite de 1800 MiB y no transfiere todos los archivos de golpe a un único ZIP sin límite. Los archivos físicamente ubicados en directorios anteriores se mantienen mientras cualquier segmento activo los necesite; se borran cuando terminan todas las entregas dependientes. Los paquetes heredados con un archivo que excede el límite por archivo se conservan intactos y se notifican al administrador.
- Los originales no se borran si la descarga local o la confirmación duradera fallan. No se borra a mano el cache del directorio del Bot API; ese directorio lo administra su servicio.
- Los paquetes usan AES-256. La contraseña se incluye en el caption del mensaje del canal para que los administradores puedan extraerlo; por eso `storage_channel_id` debe ser un canal privado restringido.

## Variables

- `MEDIA_PACKAGE_DIR=/var/lib/multi-exchanger/packages`
- `MEDIA_PACKAGE_MAX_BYTES=1887436800` (1800 MiB)
- `MEDIA_PACKAGE_IDLE_SECONDS=60`
- `MEDIA_PACKAGE_WORKERS_PER_BOT=2` (se limita internamente a un máximo de 4 workers por bot)

Los archivos con estado de entrega fallida se conservan para reintentos; si muchos destinatarios bloquean el bot o el canal buffer no funciona, revisá el panel de progreso y los logs antes de borrar datos a mano.
