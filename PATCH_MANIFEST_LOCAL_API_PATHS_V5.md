# Corrección V5 — resolución de rutas del Bot API local

## Qué corrige

La versión anterior solo admitía como carpeta el token exacto que tenía la aplicación en ese momento o el ID numérico aislado. En una instalación local de Telegram Bot API pueden existir directorios nombrados `<id>:<token>`; si el nombre de la carpeta almacenada no coincide exactamente con el token activo, el archivo queda sin resolver aunque esté en el volumen compartido.

## Cambios

- `app/bot/api_client.py`: reconoce carpetas con token distinto/antiguo siempre que el prefijo corresponda al mismo ID numérico del bot. También trata rutas absolutas dentro del contenedor, rutas ya traducidas al volumen del host y rutas relativas.
- La resolución sigue limitada al directorio del mismo bot. No lee carpetas de otros IDs, rechaza traversal y no elige arbitrariamente si una ruta relativa coincide con varios archivos.
- `tests/test_local_api_file_path_mapping.py`: añade pruebas para token rotado, ruta absoluta que usa una raíz antigua del host, resolución relativa única y ambigüedad.

## Configuración de Vultr

Se mantiene:

```text
TELEGRAM_API_SERVER_FILES_ROOT=/var/lib/telegram-bot-api
TELEGRAM_API_HOST_FILES_ROOT=/srv/tg-bot-api-data
```

No hace falta recrear `tg-local-api` ni modificar su volumen para aplicar este cambio de código. Conservá `.env` y los datos de MongoDB.

## Pruebas

Las 16 pruebas focalizadas de mapeo pasan ejecutadas con stubs de Aiogram, porque la dependencia no está instalada en el entorno de empaquetado. El test de rutas simuló directorios de token antiguo, ID numérico, rutas absolutas/relativas, ambigüedad y aislamiento entre bots. Esto valida el algoritmo en escenarios controlados; no sustituye una prueba real en Vultr.
