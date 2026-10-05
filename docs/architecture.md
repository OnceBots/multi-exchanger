# Architecture Notes

## 1. Runtime isolation

`BotManager.registry` mantiene un `BotRuntime` independiente por `bot_id`.

Un fallo en un child se registra como error de ese runtime y no cierra el Master ni otros children.

## 2. One HTTP server

FastAPI monta todas las rutas:

- `/health`
- `/ready`
- `/metrics`
- `/master-app`
- `/app`
- `/telegram/webhook/master`
- `/telegram/webhook/{bot_id}`

No hay un servidor por bot.

## 3. Data isolation

Las colecciones compartidas incluyen `bot_id` en los documentos que pertenecen a tenants.

Los índices únicos siempre consideran `bot_id` cuando corresponde.

## 4. Persistence

Telegram updates de children se registran en `inbound_updates` antes del dispatch. El índice único `(bot_id, update_id)` permite deduplicar. Los updates pendientes se reproducen después del arranque del runtime.

## 5. Backpressure

Cada child dispone de colas acotadas. Los handlers no crean miles de tasks de envío. El worker pool limita la concurrencia.

## 6. Supervisor

El supervisor comprueba:

- runtime ausente
- heartbeat atrasado
- estado persistido
- límite de reinicios

Existe circuit breaker por ventana temporal.

## 7. Date policy

Todos los valores internos nuevos usan UTC aware. MongoDB se consulta con `tz_aware=True`. Los datos históricos naive se normalizan al compararlos.
