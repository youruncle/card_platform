from decimal import Decimal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.domain.player_analysis import calculate_player_potential
from card_platform.repository import NotFoundError
from card_platform.schemas import (
    PlayerEventCreate,
    PlayerEventView,
    PlayerIntelligence,
    PlayerMetricUpsert,
    PlayerMetricView,
    PlayerPotential,
    PotentialInput,
)


class IntelligenceRepository:
    async def _require_player(self, connection: AsyncConnection, player_id: UUID) -> dict:
        result = await connection.execute(
            "SELECT id, full_name FROM player WHERE id = %s", (player_id,)
        )
        player = await result.fetchone()
        if player is None:
            raise NotFoundError("player not found")
        return player

    async def upsert_metric(
        self, connection: AsyncConnection, player_id: UUID, payload: PlayerMetricUpsert
    ) -> None:
        await self._require_player(connection, player_id)
        source = await connection.execute("SELECT id FROM data_source WHERE code = 'manual'")
        source_id = (await source.fetchone())["id"]
        existing = await connection.execute(
            """
            SELECT id FROM player_metric
            WHERE player_id = %s AND data_source_id = %s AND season = %s
              AND scope = %s AND scope_date IS NOT DISTINCT FROM %s
            """,
            (player_id, source_id, payload.season, payload.scope, payload.scope_date),
        )
        row = await existing.fetchone()
        if row:
            await connection.execute(
                "UPDATE player_metric SET metrics = %s, captured_at = now() WHERE id = %s",
                (Jsonb(payload.metrics), row["id"]),
            )
        else:
            await connection.execute(
                """
                INSERT INTO player_metric (
                    player_id, data_source_id, season, scope, scope_date, metrics
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    player_id,
                    source_id,
                    payload.season,
                    payload.scope,
                    payload.scope_date,
                    Jsonb(payload.metrics),
                ),
            )

    async def create_event(
        self, connection: AsyncConnection, player_id: UUID, payload: PlayerEventCreate
    ) -> UUID:
        await self._require_player(connection, player_id)
        source = await connection.execute("SELECT id FROM data_source WHERE code = 'manual'")
        source_id = (await source.fetchone())["id"]
        result = await connection.execute(
            """
            INSERT INTO player_event (
                player_id, data_source_id, event_type, occurred_at, headline,
                summary, sentiment, impact_score, source_url
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                player_id,
                source_id,
                payload.event_type,
                payload.occurred_at,
                payload.headline,
                payload.summary,
                payload.sentiment,
                payload.impact_score,
                payload.source_url,
            ),
        )
        return (await result.fetchone())["id"]

    async def assess_potential(
        self, connection: AsyncConnection, player_id: UUID, payload: PotentialInput
    ) -> PlayerPotential:
        await self._require_player(connection, player_id)
        result = calculate_player_potential(**payload.model_dump())
        inputs = {name: str(value) for name, value in payload.model_dump().items()}
        components = {name: str(value) for name, value in result.components.items()}
        await connection.execute(
            """
            INSERT INTO player_potential_snapshot (
                player_id, potential_score, label, components, inputs, model_version
            ) VALUES (%s, %s, %s, %s, %s, '1.0.0')
            """,
            (player_id, result.score, result.label, Jsonb(components), Jsonb(inputs)),
        )
        return PlayerPotential(score=result.score, label=result.label, components=result.components)

    async def get_intelligence(
        self, connection: AsyncConnection, player_id: UUID
    ) -> PlayerIntelligence:
        player = await self._require_player(connection, player_id)
        metric_result = await connection.execute(
            """
            SELECT season, scope, scope_date, metrics, captured_at
            FROM player_metric WHERE player_id = %s
            ORDER BY captured_at DESC LIMIT 12
            """,
            (player_id,),
        )
        event_result = await connection.execute(
            """
            SELECT id, event_type, occurred_at, headline, summary, sentiment,
                   impact_score, source_url
            FROM player_event WHERE player_id = %s
            ORDER BY occurred_at DESC LIMIT 30
            """,
            (player_id,),
        )
        potential_result = await connection.execute(
            """
            SELECT potential_score, label, components
            FROM player_potential_snapshot WHERE player_id = %s
            ORDER BY assessed_at DESC, created_at DESC LIMIT 1
            """,
            (player_id,),
        )
        potential_row = await potential_result.fetchone()
        potential = None
        if potential_row:
            potential = PlayerPotential(
                score=potential_row["potential_score"],
                label=potential_row["label"],
                components={
                    name: Decimal(str(value)) for name, value in potential_row["components"].items()
                },
            )
        return PlayerIntelligence(
            player_id=player_id,
            player_name=player["full_name"],
            metrics=[PlayerMetricView(**row) for row in await metric_result.fetchall()],
            events=[PlayerEventView(**row) for row in await event_result.fetchall()],
            potential=potential,
        )
