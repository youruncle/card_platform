from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.market_data.models import (
    EvidenceRecord,
    Freshness,
    MarketDataResult,
    MarketIdentity,
    ProviderFetchResult,
)
from card_platform.market_data.protection import retry_after_seconds
from card_platform.market_data.service import MarketDataService


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.execute = AsyncMock()

    def transaction(self) -> _AsyncContext:
        return _AsyncContext(self)


class _FakeDatabase:
    def __init__(self) -> None:
        self.connection_value = _FakeConnection()

    def connection(self) -> _AsyncContext:
        return _AsyncContext(self.connection_value)


def _identity(**updates) -> MarketIdentity:
    values = {
        "player_name": "Tim Duncan",
        "season_start": 2021,
        "season_end": 2022,
        "brand_name": "Panini",
        "product_name": "Immaculate",
        "card_number": "TD-21",
        "parallel_name": "Gold",
        "serial_total": 10,
        "is_autograph": True,
        "is_patch": True,
        "grade_company": None,
        "grade_value": None,
    }
    values.update(updates)
    return MarketIdentity(**values)


def test_identity_fingerprint_is_normalized_and_isolated() -> None:
    first = _identity(player_name="  TIM   DUNCAN ", parallel_name="GOLD")
    same = _identity(player_name="tim duncan", parallel_name="gold")
    different = _identity(card_number="TD-22")
    assert first.fingerprint == same.fingerprint
    assert first.fingerprint != different.fingerprint


def test_identity_fingerprint_keeps_grade_and_serial_boundaries() -> None:
    raw = _identity()
    graded = _identity(grade_company="PSA", grade_value=Decimal("10"))
    other_print_run = _identity(serial_total=25)
    assert len({raw.fingerprint, graded.fingerprint, other_print_run.fingerprint}) == 3


def test_popularity_ttl_is_shorter_for_hot_cards() -> None:
    assert MarketDataService.ttl_seconds(100) < MarketDataService.ttl_seconds(20)
    assert MarketDataService.ttl_seconds(20) < MarketDataService.ttl_seconds(0)


def test_sparse_cache_does_not_refresh_before_backoff_deadline() -> None:
    now = datetime.now(timezone.utc)
    cached = MarketDataResult(
        identity_fingerprint="sparse-cache",
        freshness=Freshness.INSUFFICIENT,
        next_refresh_at=now + timedelta(hours=1),
    )

    assert cached.refresh_is_due(now=now) is False
    assert MarketDataService.should_refresh(cached) is False


def test_sparse_cache_refreshes_after_backoff_deadline() -> None:
    now = datetime.now(timezone.utc)
    cached = MarketDataResult(
        identity_fingerprint="expired-sparse-cache",
        freshness=Freshness.INSUFFICIENT,
        next_refresh_at=now - timedelta(seconds=1),
    )

    assert cached.refresh_is_due(now=now) is True
    assert MarketDataService.should_refresh(cached) is True


def test_retry_after_supports_seconds_and_invalid_values() -> None:
    assert retry_after_seconds("120") == 120
    assert retry_after_seconds("invalid") is None
    assert retry_after_seconds(None) is None


@pytest.mark.asyncio
async def test_get_revalidates_same_definition_history_after_fingerprint_change() -> None:
    card_definition_id = uuid4()
    historical = EvidenceRecord(
        provider_code="cardhobby",
        external_sale_id="sale-before-normalization",
        raw_title="2025 Topps Basketball Tim Duncan Daily Dribble",
        sale_price=Decimal("10"),
        currency="CNY",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_url="https://cardhobby.example/sale-before-normalization",
        evidence_level="ended_auction",
        fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    cached = MarketDataResult(
        identity_fingerprint="current-fingerprint",
        freshness=Freshness.INSUFFICIENT,
    )
    repository = SimpleNamespace(
        ensure_identity=AsyncMock(return_value="current-fingerprint"),
        adopt_legacy_evidence=AsyncMock(),
        cached=AsyncMock(return_value=cached),
        historical_evidence_for_card_definition=AsyncMock(
            return_value=[historical]
        ),
        enqueue_refresh=AsyncMock(),
    )
    service = MarketDataService(
        _FakeDatabase(),
        repository=repository,  # type: ignore[arg-type]
    )

    result = await service.get(
        _identity(),
        card_definition_id=card_definition_id,
        queue_refresh=False,
    )

    assert result.freshness == Freshness.CACHED
    assert result.evidence == [historical]
    assert "重新校验 1 条" in result.notes[-1]
    repository.historical_evidence_for_card_definition.assert_awaited_once_with(
        service.database.connection_value,
        "current-fingerprint",
        card_definition_id,
    )


@pytest.mark.asyncio
async def test_process_one_refresh_delegates_already_claimed_job() -> None:
    job = {
        "id": uuid4(),
        "identity_fingerprint": "refresh-fingerprint",
        "normalized_identity": _identity().normalized(),
        "data_as_of": None,
    }
    repository = SimpleNamespace(claim_refresh=AsyncMock(return_value=job))
    service = MarketDataService(
        _FakeDatabase(),
        repository=repository,  # type: ignore[arg-type]
    )
    service.process_claimed_refresh = AsyncMock()  # type: ignore[method-assign]

    assert await service.process_one_refresh() is True

    repository.claim_refresh.assert_awaited_once_with(service.database.connection_value)
    service.process_claimed_refresh.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_process_claimed_refresh_uses_shared_persistence_path() -> None:
    job = {
        "id": uuid4(),
        "identity_fingerprint": "refresh-fingerprint",
        "normalized_identity": _identity().normalized(),
        "data_as_of": None,
    }
    cached = MarketDataResult(
        identity_fingerprint=job["identity_fingerprint"],
        freshness=Freshness.INSUFFICIENT,
    )
    repository = SimpleNamespace(
        store_evidence=AsyncMock(),
        cached=AsyncMock(return_value=cached),
        finish_refresh=AsyncMock(),
        fail_refresh=AsyncMock(),
    )
    fetch_sales = AsyncMock(return_value=ProviderFetchResult(provider_code="cardhobby"))
    provider = SimpleNamespace(
        provider=SimpleNamespace(code="cardhobby"),
        fetch_sales=fetch_sales,
    )
    database = _FakeDatabase()
    service = MarketDataService(
        database,
        [provider],
        repository=repository,  # type: ignore[list-item,arg-type]
    )

    await service.process_claimed_refresh(job)

    fetch_sales.assert_awaited_once()
    repository.store_evidence.assert_awaited_once_with(
        database.connection_value, job["identity_fingerprint"], []
    )
    repository.finish_refresh.assert_awaited_once()
    assert repository.finish_refresh.await_args.args[3] == Freshness.INSUFFICIENT
    repository.fail_refresh.assert_not_awaited()


def test_evidence_model_preserves_shipping_and_raw_identity_result() -> None:
    evidence = EvidenceRecord(
        provider_code="authorized_test",
        external_sale_id="sale-1",
        raw_title="Original listing title",
        sale_price=Decimal("100"),
        shipping_price=Decimal("12"),
        currency="USD",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_url="https://example.com/sale-1",
        evidence_level="confirmed_transaction",
        match_confidence=Decimal("0.9"),
        identity_classification={"tier": "exact", "version": 1},
        raw_payload={"provider_field": "preserved"},
        fetched_at=datetime.now(timezone.utc),
    )
    assert evidence.shipping_price == Decimal("12")
    assert evidence.identity_classification["tier"] == "exact"
    assert evidence.raw_payload["provider_field"] == "preserved"


def test_migration_declares_database_refresh_coalescing_constraint() -> None:
    migration = (
        __import__("pathlib")
        .Path(__file__)
        .parents[3]
        .joinpath("db/migrations/0011_market_data_platform.sql")
        .read_text()
    )
    assert "market_refresh_job_active_identity_uidx" in migration
    assert "WHERE status IN ('queued', 'running')" in migration
    assert "FOR UPDATE OF j SKIP LOCKED" not in migration
