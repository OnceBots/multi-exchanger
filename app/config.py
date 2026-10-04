from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


class ConfigurationError(ValueError):
    pass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError as exc:
        raise ConfigurationError(f"{name} debe ser un entero válido") from exc


def _csv_int(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            try:
                values.add(int(item))
            except ValueError as exc:
                raise ConfigurationError(f"{name} contiene un ID inválido: {item}") from exc
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class Settings:
    master_bot_token: str
    mongo_uri: str
    db_name: str
    webhook_base_url: str
    master_webhook_path: str
    webhook_secret: str
    environment: str
    mode: str
    port: int
    log_level: str
    service_name: str
    drop_pending_updates: bool
    telegram_max_connections: int
    mongo_server_selection_timeout_ms: int
    mongo_connect_timeout_ms: int
    mongo_socket_timeout_ms: int
    mongo_max_pool_size: int
    mongo_min_pool_size: int
    health_path: str
    ready_path: str
    metrics_path: str
    admin_ids: frozenset[int]
    token_encryption_key: str
    child_webhook_secret_length: int
    redis_uri: str | None
    broadcast_workers_per_bot: int
    broadcast_max_concurrency: int
    room_queue_maxsize: int
    album_debounce_ms: int
    max_media_per_user_per_minute: int
    max_albums_per_user_per_minute: int
    max_restarts_per_window: int
    restart_window_seconds: int
    max_restart_delay_seconds: int
    heartbeat_interval_seconds: int
    webapp_auth_max_age_seconds: int
    enable_metrics: bool
    app_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        required = ["MASTER_BOT_TOKEN", "MONGO_URI", "DB_NAME", "WEBHOOK_BASE_URL", "WEBHOOK_SECRET", "BOT_TOKEN_ENCRYPTION_KEY"]
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            raise ConfigurationError(f"Faltan variables obligatorias: {', '.join(missing)}")

        environment = os.getenv("ENVIRONMENT", "production").lower()
        mode = os.getenv("MODE", "webhook").lower()
        if mode not in {"webhook", "polling"}:
            raise ConfigurationError("MODE debe ser webhook o polling")
        if environment == "production" and mode != "webhook":
            raise ConfigurationError("En production, MODE=webhook es obligatorio")

        base = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
        parsed = urlparse(base)
        if environment == "production" and parsed.scheme != "https":
            raise ConfigurationError("WEBHOOK_BASE_URL debe utilizar HTTPS en production")
        if not parsed.netloc:
            raise ConfigurationError("WEBHOOK_BASE_URL no es una URL válida")

        webhook_path = os.getenv("MASTER_WEBHOOK_PATH", "/telegram/webhook/master")
        if not webhook_path.startswith("/"):
            raise ConfigurationError("MASTER_WEBHOOK_PATH debe comenzar con /")
        if len(os.getenv("WEBHOOK_SECRET", "")) < 16:
            raise ConfigurationError("WEBHOOK_SECRET debe tener al menos 16 caracteres")

        return cls(
            master_bot_token=os.environ["MASTER_BOT_TOKEN"],
            mongo_uri=os.environ["MONGO_URI"],
            db_name=os.environ["DB_NAME"],
            webhook_base_url=base,
            master_webhook_path=webhook_path,
            webhook_secret=os.environ["WEBHOOK_SECRET"],
            environment=environment,
            mode=mode,
            port=_int("PORT", 10000),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            service_name=os.getenv("SERVICE_NAME", "telegram-multibot-platform"),
            drop_pending_updates=_bool("DROP_PENDING_UPDATES", False),
            telegram_max_connections=_int("TELEGRAM_MAX_CONNECTIONS", 20),
            mongo_server_selection_timeout_ms=_int("MONGO_SERVER_SELECTION_TIMEOUT_MS", 5000),
            mongo_connect_timeout_ms=_int("MONGO_CONNECT_TIMEOUT_MS", 10000),
            mongo_socket_timeout_ms=_int("MONGO_SOCKET_TIMEOUT_MS", 10000),
            mongo_max_pool_size=_int("MONGO_MAX_POOL_SIZE", 50),
            mongo_min_pool_size=_int("MONGO_MIN_POOL_SIZE", 1),
            health_path=os.getenv("HEALTH_PATH", "/health"),
            ready_path=os.getenv("READY_PATH", "/ready"),
            metrics_path=os.getenv("METRICS_PATH", "/metrics"),
            admin_ids=_csv_int("ADMIN_IDS"),
            token_encryption_key=os.environ["BOT_TOKEN_ENCRYPTION_KEY"],
            child_webhook_secret_length=_int("CHILD_WEBHOOK_SECRET_LENGTH", 32),
            redis_uri=os.getenv("REDIS_URI") or None,
            broadcast_workers_per_bot=_int("BROADCAST_WORKERS_PER_BOT", 4),
            broadcast_max_concurrency=_int("BROADCAST_MAX_CONCURRENCY", 8),
            room_queue_maxsize=_int("ROOM_QUEUE_MAXSIZE", 1000),
            album_debounce_ms=_int("ALBUM_DEBOUNCE_MS", 750),
            max_media_per_user_per_minute=_int("MAX_MEDIA_PER_USER_PER_MINUTE", 20),
            max_albums_per_user_per_minute=_int("MAX_ALBUMS_PER_USER_PER_MINUTE", 5),
            max_restarts_per_window=_int("MAX_RESTARTS_PER_WINDOW", 10),
            restart_window_seconds=_int("RESTART_WINDOW_SECONDS", 300),
            max_restart_delay_seconds=_int("MAX_RESTART_DELAY_SECONDS", 60),
            heartbeat_interval_seconds=_int("HEARTBEAT_INTERVAL_SECONDS", 30),
            webapp_auth_max_age_seconds=_int("WEBAPP_AUTH_MAX_AGE_SECONDS", 86400),
            enable_metrics=_bool("ENABLE_METRICS", True),
            app_base_url=os.getenv("APP_BASE_URL", base),
        )
