# Patch: bots eliminados desde BotFather

Este parche adapta el lifecycle a la política actual de la plataforma: cuando el propietario elimina un bot hijo desde Telegram/@BotFather, Telegram empieza a rechazar su token con `Unauthorized`.

## Comportamiento

- El token rechazado se interpreta como una eliminación del bot en Telegram.
- El bot se archiva automáticamente con `status=ARCHIVED` y `enabled=false`.
- El supervisor deja de intentar reiniciarlo.
- Se conserva la configuración y los datos persistidos de la plataforma.
- El Master recibe una notificación explicando que el bot fue eliminado y que los datos de la plataforma se conservan.
- No se intenta "rotar" ni reemplazar el token de un bot que ya fue eliminado.

## Archivos a reemplazar

```text
app/bot/manager.py
app/services/bot_lifecycle.py
```

Agregar el test:

```text
tests/test_deleted_bot_lifecycle.py
```

No requiere cambios en `.env`, `requirements.txt`, Docker ni Mongo.

## Validación

Se ejecutó:

```text
python -m compileall -q app
python -m pytest -q tests/test_deleted_bot_lifecycle.py tests/test_runtime_hardening.py
```

Resultado: 7 tests pasando.
