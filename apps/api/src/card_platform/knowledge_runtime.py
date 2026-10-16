from __future__ import annotations

import asyncio
from typing import Any

from psycopg.errors import UndefinedTable

from card_platform.db import Database
from card_platform.knowledge import KnowledgeResolver


class KnowledgeRuntime:
    """Atomically exposes the currently published immutable resolver."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._resolver = KnowledgeResolver()
        self._version_id: str | None = None
        self._lock = asyncio.Lock()

    @property
    def resolver(self) -> KnowledgeResolver:
        return self._resolver

    @property
    def version_id(self) -> str | None:
        return self._version_id

    async def refresh(self) -> KnowledgeResolver:
        async with self._lock:
            try:
                async with self.database.connection() as connection:
                    result = await connection.execute(
                        """
                        SELECT kw.published_version_id, kv.snapshot
                        FROM knowledge_workspace kw
                        LEFT JOIN knowledge_version kv ON kv.id = kw.published_version_id
                            AND kv.id IS DISTINCT FROM %s::uuid
                        WHERE kw.name = 'Card Platform'
                        """,
                        (self._version_id,),
                    )
                    row: dict[str, Any] | None = await result.fetchone()
            except UndefinedTable:
                row = None
            if row and row["published_version_id"] and str(row["published_version_id"]) == self._version_id:
                return self._resolver
            snapshot = row["snapshot"] if row and row["snapshot"] else {}
            self._resolver = KnowledgeResolver(snapshot)
            self._version_id = (
                str(row["published_version_id"])
                if row and row["published_version_id"]
                else None
            )
            return self._resolver

    def activate(self, snapshot: dict[str, Any], version_id: object) -> KnowledgeResolver:
        self._resolver = KnowledgeResolver(snapshot)
        self._version_id = str(version_id)
        return self._resolver
