from __future__ import annotations

import inspect
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


class ReturningResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self.row

    async def fetchall(self) -> list[dict[str, Any]]:
        return [] if self.row is None else [self.row]


class ReturningConnection(RecordingConnection):
    def __init__(self, row: dict[str, Any] | None) -> None:
        super().__init__()
        self.row = row

    async def execute(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> ReturningResult:
        self.calls.append((query, params))
        return ReturningResult(self.row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "freshness",
    [Freshness.FRESH, Freshness.SOURCE_LIMITED, Freshness.INSUFFICIENT],
)
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
async def test_insufficient_refresh_revaluation_requires_retained_evidence() -> None:
    connection = RecordingConnection()

    await MarketDataRepository().finish_refresh(
        connection,  # type: ignore[arg-type]
        uuid4(),
        "identity-fingerprint",
        Freshness.INSUFFICIENT,
        ttl_seconds=3600,
        error=None,
    )

    valuation_inserts = [
        query for query, _ in connection.calls if "INSERT INTO valuation_job" in query
    ]
    assert len(valuation_inserts) == 1
    assert "FROM market_evidence evidence" in valuation_inserts[0]
    assert "WHERE evidence.identity_fingerprint = b.identity_fingerprint" in valuation_inserts[0]


@pytest.mark.asyncio
async def test_concurrent_refresh_followup_is_bounded_to_new_evidence_window() -> None:
    followup_id = uuid4()
    source_job_id = uuid4()
    connection = ReturningConnection({"id": followup_id})

    result = await MarketDataRepository().enqueue_revaluation_after_concurrent_refresh(
        connection,  # type: ignore[arg-type]
        source_job_id,
    )

    assert result == followup_id
    query, params = connection.calls[0]
    assert params == (source_job_id,)
    assert "refresh.finished_at >= current.started_at" in query
    assert "refresh.finished_at <= current.finished_at" in query
    assert "fetched_candidate_count" in query
    assert "current.status = 'insufficient_evidence'" in query
    assert "'insufficient'" in query


@pytest.mark.asyncio
async def test_adopt_reviewed_evidence_moves_only_explicit_acceptances() -> None:
    evidence_id = uuid4()
    version_id = uuid4()
    connection = ReturningConnection({"id": evidence_id})

    adopted = await MarketDataRepository().adopt_reviewed_evidence(
        connection,  # type: ignore[arg-type]
        "corrected-fingerprint",
        version_id,
    )

    assert adopted == 1
    move_query, move_params = connection.calls[0]
    assert "decision.decision = 'accepted'" in move_query
    assert "decision.identity_version_id = %s" in move_query
    assert "'reviewed_identity_accepted', true" in move_query
    assert "'reviewed_identity_version_id'" in move_query
    assert "'reviewed_collection_item_id'" in move_query
    assert move_params == (
        "corrected-fingerprint",
        version_id,
        "corrected-fingerprint",
    )


@pytest.mark.asyncio
async def test_store_evidence_does_not_steal_reviewed_sale_from_accepted_identity() -> None:
    connection = RecordingConnection()

    await MarketDataRepository().store_evidence(
        connection,  # type: ignore[arg-type]
        "broad-query-fingerprint",
        [],
    )

    # The empty batch verifies without writes; inspect the production SQL so a
    # future conflict rewrite cannot silently remove the accepted-decision guard.
    source = inspect.getsource(MarketDataRepository.store_evidence)
    assert "decision.decision = 'accepted'" in source
    assert "market_evidence.identity_fingerprint" in source
    assert "identity_fingerprint = CASE" in source


def test_historical_evidence_is_read_only_and_scoped_to_card_definition() -> None:
    source = inspect.getsource(
        MarketDataRepository.historical_evidence_for_card_definition
    )

    assert "JOIN market_evidence" in source
    assert "comparable.card_definition_id = %s" in source
    assert "evidence.identity_fingerprint <> %s" in source
    assert "UPDATE" not in source
    assert "DELETE" not in source
