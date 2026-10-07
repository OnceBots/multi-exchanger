# Add-on: Contribution + Moderation v1

Este ZIP contiene **solo archivos nuevos**. No reemplaza ni modifica archivos del proyecto base.

## Qué agrega

- Créditos por contribución aprobada.
- Coste de descarga configurable por bot.
- Reputación y niveles.
- Ratio aporte/consumo.
- Cola de aportes pendientes.
- Detección de duplicados mediante `file_unique_id` / fingerprint.
- Reportes.
- Sanciones temporales.
- Ranking.
- Panel de moderación.
- API para Mini App.
- Mini App independiente en `/contribution-app`.

## Importante

Al ser un paquete **aditivo**, el ZIP no toca `child_handlers.py`, `child_factory.py`, `manager.py`, `webapp.py`, `routes.py` ni `media_service.py`.

Por ello, la funcionalidad queda lista para conectar, pero **no queda activada automáticamente** hasta hacer las integraciones mínimas indicadas abajo.

## Integración mínima

### 1. Repositorio + servicio

En el constructor de servicios de cada child, crear:

```python
from app.features.contribution_moderation import ContributionRepository, ContributionService

contribution_repo = ContributionRepository(ctx.db)
await contribution_repo.ensure_indexes()
contribution_service = ContributionService(contribution_repo)
await contribution_service.initialize(ctx.bot_id)
```

Luego exponerlo en `ctx.services`, por ejemplo:

```python
ctx.services = SimpleNamespace(
    rooms=rooms,
    media=media,
    broadcast=broadcast,
    admin_feed=admin_feed,
    contribution=contribution_service,
)
```

### 2. Router Telegram

Donde actualmente se incluye el router principal del child:

```python
from app.features.contribution_moderation.telegram_router import build_contribution_router

dispatcher.include_router(build_child_router(ctx))
dispatcher.include_router(build_contribution_router(ctx))
```

### 3. API + Mini App

En el router HTTP de la plataforma:

```python
from app.features.contribution_moderation.web_router import build_contribution_web_router

app.include_router(build_contribution_web_router(platform))
```

Eso agrega `/contribution-app` y las APIs de contribución/moderación.

### 4. Registrar aportes reales

Después de aceptar un media válido en una sala, llamar:

```python
await ctx.services.contribution.register_contribution(
    bot_id=ctx.bot_id,
    uploader_id=user_id,
    room_id=room_id,
    media_type=media_type,
    file_id=file_id,
    fingerprint=str(message.photo[-1].file_unique_id) if message.photo else None,
    caption=message.caption,
    event_key=f"{ctx.bot_id}:{message.message_id}",
)
```

Para álbumes: usar un fingerprint con todos los `file_unique_id` y registrar **un solo aporte por álbum**.

### 5. Autorizar descargas

Antes de una descarga que deba consumir créditos:

```python
decision = await ctx.services.contribution.authorize_download(ctx.bot_id, user_id)
if not decision.allowed:
    ...
```

## Valores por defecto

- Usuario nuevo: 15 créditos.
- Aporte aprobado: +10 créditos y +10 reputación.
- Descarga: -5 créditos.
- Rechazo: -2 reputación.
- Ratio mínimo orientativo: 0.25.

Se almacenan por bot en `contribution_settings`.

## Colecciones nuevas

- `user_economy`
- `contribution_items`
- `content_reports`
- `user_sanctions`
- `contribution_settings`
- `moderation_actions`

Todas incluyen `bot_id`.

## Nota de activación

Este paquete deliberadamente no modifica el código existente. Por esa razón, la parte de integración queda separada y no altera el comportamiento actual hasta que se conecte.
