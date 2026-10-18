# Changelog

## Encrypted room media packages V1
- La multimedia de las salas se descarga en segundo plano a un volumen Docker persistente, se valida y se registra en MongoDB antes de borrar el mensaje original del miembro.
- Se crean ZIP con cifrado WinZip AES-256 sin recomprimir los medios; cierre por tamaño de 1800 MiB o 60 segundos de inactividad.
- Se publica y actualiza un estado por miembro con barra de progreso, botón de actualización y cierre manual exclusivo de `ADMIN_IDS`.
- Los ZIP se entregan a miembros y administradores desde el canal buffer privado. Los fallos conservan los archivos/estado para reintentar.
- Se añaden controles de concurrencia, reserva de espacio en disco, recuperación de colas tras reinicio, reconciliación de contadores y entregas por lotes para no saturar el bot.
- El bot rechaza archivos sin borrar el original si el canal buffer no está configurado.
- Los errores de descarga/ruta quedan registrados sin ocultarse por errores secundarios del manejador; se mantienen seguros los componentes de las rutas temporales.


## Video cover fix V6
- Se cambia el envío del diseño de marca desde solo `thumbnail` a `thumbnail` + `cover`. Telegram puede ignorar `thumbnail` y generar un fotograma propio; `cover` es la portada visible de los vídeos.
- Los vídeos individuales y los vídeos de álbumes se cargan al canal buffer con ambos campos vía multipart y luego las salas/feed de administración copian ese mensaje procesado.
- Requiere un Telegram Bot API local compatible con `cover` (Bot API 8.3 o posterior).

## Archived bot recovery and media-only rooms
- Se pueden recuperar bots hijos archivados enviando su token validado desde el Master; se conserva el propietario, la configuración no relacionada con el ciclo de vida y los datos asociados. Solo el propietario original o un administrador puede reactivarlos.
- Se renuevan de forma segura el token cifrado y el secreto del webhook al reactivar, y se mantiene el rechazo de IDs activos duplicados.
- Todos los envíos a salas son multimedia sin captions. Los mensajes de texto no se distribuyen como publicaciones de sala.
- Los videos nativos y los videos enviados como documento pasan por la miniatura predeterminada y el buffer. Si la configuración o la subida falla, la distribución se bloquea sin fallback directo.

## Chat-only release
- Eliminada la Mini App, sus páginas HTML, autenticación WebApp y endpoints REST exclusivos de interfaz.
- El Master crea bots hijos dentro del chat: el usuario pulsa «Crear mi bot» y envía el token de BotFather en privado.
- Los bots hijos administran salas y funciones con mensajes, comandos y botones inline de Telegram.
- Se conserva el backend HTTP para health/readiness/metrics y los webhooks públicos de Telegram.
- Se conserva el parche de Vultr, Bot API local, miniaturas y difusión de multimedia.
