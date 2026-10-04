from __future__ import annotations

import logging

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi


class MongoManager:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("mongo")
        self.client: AsyncMongoClient | None = None
        self.db = None

    async def connect(self) -> None:
        self.client = AsyncMongoClient(
            self.settings.mongo_uri,
            server_api=ServerApi(version="1", strict=True, deprecation_errors=True),
            serverSelectionTimeoutMS=self.settings.mongo_server_selection_timeout_ms,
            connectTimeoutMS=self.settings.mongo_connect_timeout_ms,
            socketTimeoutMS=self.settings.mongo_socket_timeout_ms,
            maxPoolSize=self.settings.mongo_max_pool_size,
            minPoolSize=self.settings.mongo_min_pool_size,
            retryWrites=True,
            retryReads=True,
        )
        self.db = self.client[self.settings.db_name]
        await self.ping()
        self.logger.info("mongodb_connected")

    async def ping(self) -> bool:
        if self.client is None:
            return False
        try:
            await self.client.admin.command("ping")
            return True
        except PyMongoError:
            self.logger.exception("mongodb_ping_failed")
            return False

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None
            self.db = None
            self.logger.info("mongodb_closed")

    def collection(self, name: str):
        if self.db is None:
            raise RuntimeError("MongoDB no está inicializado")
        return self.db[name]
