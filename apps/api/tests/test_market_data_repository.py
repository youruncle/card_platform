from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from card_platform.market_data.models import Freshness
from card_platform.market_data.repository import MarketDataRepository


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> None:
        self.calls.append((query, params))


@pytest.mark.asyncio
@pytest.mark.parametrize("freshness", [Freshness.FRESH, Freshness.SOURCE_LIMITED])
async def test_finish_refresh_requeues_valuation_when_verified_evidence_exists(
    freshness: Freshness,
) -> None:
    connection = RecordingConnection()

    await MarketDataRepository().finish_refresh(
        connection,  # type: ignore[arg-type]
        uuid4(),
        "identity-fingerprint",
        freshness,
        ttl_seconds=3600,
        error="ebay unavailable" if freshness == Freshness.SOURCE_LIMITED else None,
    )

    valuation_inserts = [
        query for query, _ in connection.calls if "INSERT INTO valuation_job" in query
    ]
    assert len(valuation_inserts) == 1
    assert "EXISTS" in valuation_inserts[0]


@pytest.mark.asyncio
async def test_finish_refresh_does_not_requeue_valuation_for_empty_market() -> None:
    connection = RecordingConnection()

    await MarketDataRepository().finish_refresh(
        connection,  # type: ignore[arg-type]
        uuid4(),
        "identity-fingerprint",
        Freshness.INSUFFICIENT,
        ttl_seconds=3600,
        error=None,
    )

    assert all("INSERT INTO valuation_job" not in query for query, _ in connection.calls)
