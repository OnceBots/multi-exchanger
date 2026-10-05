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
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} debe ser un entero válido") from exc


def _csv_int(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    values: set[int] = set()
    for part in raw.split(","):
        value = part.strip()
        if value:
            try:
                values.add(int(value))
            except ValueError as exc:
                raise ConfigurationError(f"{name} contiene un ID inválido: {value}") from exc
    return frozenset(values)


def _path(name: str, default: str) -> str:
    value = os.getenv(name, default).strip() or default
    return value if value.startswith("/") else f"/{value}"


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
    app_base_url: str
    admin_ids: frozenset[int]
    token_encryption_key: str
    child_webhook_secret_length: int
    heartbeat_interval_seconds: int
    supervisor_interval_seconds: int
    heartbeat_grace_multiplier: int
    max_restarts_per_window: int
    restart_window_seconds: int
    max_restart_delay_seconds: int
    room_queue_maxsize: int
    broadcast_workers_per_bot: int
    broadcast_concurrency_per_bot: int
    broadcast_max_retries: int
    album_debounce_ms: int
    admin_feed_enabled_by_default: bool
    admin_feed_max_caption_length: int
    webapp_auth_max_age_seconds: int
    allow_http_localhost: bool
    webapp_launch_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        master_token = (os.getenv("MASTER_BOT_TOKEN") or os.getenv("MASTER_TOKEN") or "").strip()
        mongo_uri = (os.getenv("MONGO_URI") or "").strip()
        db_name = (os.getenv("DB_NAME") or "telegram_multibot").strip()
        base_url = (os.getenv("WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
        webhook_secret_value = (os.getenv("WEBHOOK_SECRET") or "").strip()
        encryption_key = (os.getenv("BOT_TOKEN_ENCRYPTION_KEY") or "").strip()
        missing_values = []
        if not master_token: missing_values.append("MASTER_BOT_TOKEN")
        if not mongo_uri: missing_values.append("MONGO_URI")
        if not db_name: missing_values.append("DB_NAME")
        if not base_url: missing_values.append("WEBHOOK_BASE_URL")
        if not webhook_secret_value: missing_values.append("WEBHOOK_SECRET")
        if not encryption_key: missing_values.append("BOT_TOKEN_ENCRYPTION_KEY")
        missing = missing_values
        if missing:
            raise ConfigurationError(f"Faltan variables obligatorias: {', '.join(missing)}")

        environment = os.getenv("ENVIRONMENT", "production").strip().lower()
        parsed = urlparse(base_url)
        if not parsed.netloc:
            raise ConfigurationError("WEBHOOK_BASE_URL no es una URL válida")
        if environment == "production" and parsed.scheme != "https":
            raise ConfigurationError("WEBHOOK_BASE_URL debe utilizar HTTPS en production")
        if environment != "production" and parsed.scheme not in {"http", "https"}:
            raise ConfigurationError("WEBHOOK_BASE_URL debe utilizar http o https")
        webhook_secret = webhook_secret_value
        if len(webhook_secret) < 16:
            raise ConfigurationError("WEBHOOK_SECRET debe tener al menos 16 caracteres")
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in webhook_secret):
            raise ConfigurationError("WEBHOOK_SECRET solo puede contener letras, números, _ y -")

        app_base_url = (os.getenv("APP_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or base_url).strip().rstrip("/")
        app_parsed = urlparse(app_base_url)
        if not app_parsed.netloc:
            raise ConfigurationError("APP_BASE_URL no es una URL válida")

        mode = os.getenv("MODE", "webhook").strip().lower()
        if mode not in {"webhook", "polling"}:
            raise ConfigurationError("MODE debe ser webhook o polling")
        if environment == "production" and mode != "webhook":
            raise ConfigurationError("En production MODE=webhook es obligatorio")

        return cls(
            master_bot_token=master_token,
            mongo_uri=mongo_uri,
            db_name=db_name,
            webhook_base_url=base_url,
            master_webhook_path=_path("MASTER_WEBHOOK_PATH", "/telegram/webhook/master"),
            webhook_secret=webhook_secret,
            environment=environment,
            mode=mode,
            port=_int("PORT", 10000),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            service_name=os.getenv("SERVICE_NAME", "telegram-multibot-platform"),
            drop_pending_updates=_bool("DROP_PENDING_UPDATES", False),
            telegram_max_connections=_int("TELEGRAM_MAX_CONNECTIONS", 20),
            mongo_server_selection_timeout_ms=_int("MONGO_SERVER_SELECTION_TIMEOUT_MS", 5000),
            mongo_connect_timeout_ms=_int("MONGO_CONNECT_TIMEOUT_MS", 10000),
            mongo_socket_timeout_ms=_int("MONGO_SOCKET_TIMEOUT_MS", 10000),
            mongo_max_pool_size=_int("MONGO_MAX_POOL_SIZE", 50),
            mongo_min_pool_size=_int("MONGO_MIN_POOL_SIZE", 1),
            health_path=_path("HEALTH_PATH", "/health"),
            ready_path=_path("READY_PATH", "/ready"),
            metrics_path=_path("METRICS_PATH", "/metrics"),
            app_base_url=app_base_url,
            admin_ids=_csv_int("ADMIN_IDS") or _csv_int("SUPER_ADMINS"),
            token_encryption_key=encryption_key,
            child_webhook_secret_length=max(16, _int("CHILD_WEBHOOK_SECRET_LENGTH", 32)),
            heartbeat_interval_seconds=max(5, _int("HEARTBEAT_INTERVAL_SECONDS", 30)),
            supervisor_interval_seconds=max(10, _int("SUPERVISOR_INTERVAL_SECONDS", 15)),
            heartbeat_grace_multiplier=max(2, _int("HEARTBEAT_GRACE_MULTIPLIER", 4)),
            max_restarts_per_window=max(1, _int("MAX_RESTARTS_PER_WINDOW", 8)),
            restart_window_seconds=max(60, _int("RESTART_WINDOW_SECONDS", 300)),
            max_restart_delay_seconds=max(1, _int("MAX_RESTART_DELAY_SECONDS", 60)),
            room_queue_maxsize=max(10, _int("ROOM_QUEUE_MAXSIZE", 1000)),
            broadcast_workers_per_bot=max(1, _int("BROADCAST_WORKERS_PER_BOT", 2)),
            broadcast_concurrency_per_bot=max(1, _int("BROADCAST_CONCURRENCY_PER_BOT", 4)),
            broadcast_max_retries=max(1, _int("BROADCAST_MAX_RETRIES", 3)),
            album_debounce_ms=max(250, _int("ALBUM_DEBOUNCE_MS", 900)),
            admin_feed_enabled_by_default=_bool("ADMIN_FEED_ENABLED_BY_DEFAULT", True),
            admin_feed_max_caption_length=max(50, _int("ADMIN_FEED_MAX_CAPTION_LENGTH", 900)),
            webapp_auth_max_age_seconds=max(60, _int("WEBAPP_AUTH_MAX_AGE_SECONDS", 86400)),
            allow_http_localhost=_bool("ALLOW_HTTP_LOCALHOST", environment != "production"),
            webapp_launch_ttl_seconds=max(120, _int("WEBAPP_LAUNCH_TTL_SECONDS", 900)),
        )
