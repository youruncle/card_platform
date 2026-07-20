from collections.abc import Mapping
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.ingestion.providers import NBAStatsProvider, RSSProvider
from card_platform.repository import NotFoundError
from card_platform.schemas import PlayerSyncRequest, PlayerSyncResult


class PlayerSyncService:
    def __init__(self, settings: Settings, providers: Mapping[str, Any] | None = None) -> None:
        self.settings = settings
        self.providers = dict(providers or self._default_providers(settings))

    @staticmethod
    def _default_providers(settings: Settings) -> dict[str, Any]:
        providers: dict[str, Any] = {
            "nba_stats": NBAStatsProvider(
                settings.nba_stats_base_url, settings.provider_timeout_seconds
            )
        }
        if settings.news_rss_url_template:
            providers["news"] = RSSProvider(
                "news_rss",
                settings.news_rss_url_template,
                "news",
                settings.provider_timeout_seconds,
            )
        if settings.injury_rss_url_template:
            providers["injury"] = RSSProvider(
                "injury_rss",
                settings.injury_rss_url_template,
                "injury",
                settings.provider_timeout_seconds,
            )
        return providers

    async def sync(
        self, connection: AsyncConnection, player_id: UUID, request: PlayerSyncRequest
    ) -> PlayerSyncResult:
        player_result = await connection.execute(
            "SELECT id, full_name, nba_person_id FROM player WHERE id = %s", (player_id,)
        )
        player = await player_result.fetchone()
        if player is None:
            raise NotFoundError("player not found")
        nba_person_id = request.nba_person_id or player["nba_person_id"]
        if request.nba_person_id and request.nba_person_id != player["nba_person_id"]:
            await connection.execute(
                "UPDATE player SET nba_person_id = %s, updated_at = now() WHERE id = %s",
                (request.nba_person_id, player_id),
            )

        run_result = await connection.execute(
            """
            INSERT INTO data_sync_run (player_id, requested_sources)
            VALUES (%s, %s) RETURNING id
            """,
            (player_id, request.sources),
        )
        run_id = (await run_result.fetchone())["id"]
        source_results: dict[str, dict] = {}
        successes = 0

        for source in request.sources:
            provider = self.providers.get(source)
            if provider is None:
                source_results[source] = {
                    "status": "not_configured",
                    "count": 0,
                    "message": "provider is not configured",
                }
                continue
            try:
                if source == "nba_stats":
                    if not nba_person_id:
                        raise ValueError("nba_person_id is required for NBA stats sync")
                    records = await provider.fetch(nba_person_id)
                    await self._save_metrics(connection, player_id, provider.code, records)
                else:
                    records = await provider.fetch(player["full_name"])
                    await self._save_events(connection, player_id, provider.code, records)
                source_results[source] = {"status": "completed", "count": len(records)}
                successes += 1
            except Exception as error:
                source_results[source] = {
                    "status": "failed",
                    "count": 0,
                    "message": str(error)[:500],
                }

        requested_count = len(request.sources)
        status = (
            "completed"
            if successes == requested_count
            else "partial"
            if successes > 0
            else "failed"
        )
        await connection.execute(
            """
            UPDATE data_sync_run
            SET status = %s, source_results = %s, completed_at = now()
            WHERE id = %s
            """,
            (status, Jsonb(source_results), run_id),
        )
        return PlayerSyncResult(run_id=run_id, status=status, source_results=source_results)

    async def _save_metrics(
        self, connection: AsyncConnection, player_id: UUID, source_code: str, records: list
    ) -> None:
        source_id = await self._source_id(connection, source_code)
        for record in records:
            existing = await connection.execute(
                """
                SELECT id FROM player_metric
                WHERE player_id = %s AND data_source_id = %s AND season = %s
                  AND scope = %s AND scope_date IS NULL
                """,
                (player_id, source_id, record.season, record.scope),
            )
            row = await existing.fetchone()
            if row:
                await connection.execute(
                    "UPDATE player_metric SET metrics = %s, captured_at = now() WHERE id = %s",
                    (Jsonb(record.metrics), row["id"]),
                )
            else:
                await connection.execute(
                    """
                    INSERT INTO player_metric (
                        player_id, data_source_id, season, scope, metrics
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (player_id, source_id, record.season, record.scope, Jsonb(record.metrics)),
                )

    async def _save_events(
        self, connection: AsyncConnection, player_id: UUID, source_code: str, records: list
    ) -> None:
        source_id = await self._source_id(connection, source_code)
        for record in records:
            await connection.execute(
                """
                INSERT INTO player_event (
                    player_id, data_source_id, event_type, occurred_at,
                    headline, summary, source_url, external_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (data_source_id, external_id) DO UPDATE SET
                    headline = EXCLUDED.headline,
                    summary = EXCLUDED.summary,
                    source_url = EXCLUDED.source_url
                """,
                (
                    player_id,
                    source_id,
                    record.event_type,
                    record.occurred_at,
                    record.headline,
                    record.summary,
                    record.source_url,
                    record.external_id,
                ),
            )

    @staticmethod
    async def _source_id(connection: AsyncConnection, code: str) -> UUID:
        result = await connection.execute("SELECT id FROM data_source WHERE code = %s", (code,))
        row = await result.fetchone()
        if row is None:
            raise RuntimeError(f"data source is not seeded: {code}")
        return row["id"]
