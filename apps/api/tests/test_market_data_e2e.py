import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from card_platform.config import Settings
from card_platform.db import Database
from card_platform.market_data.models import (
    EvidenceRecord,
    Freshness,
    MarketIdentity,
    ProviderFetchResult,
)
from card_platform.market_data.protection import ProtectedProvider, SourceLimitedError
from card_platform.market_data.provider import MarketDataProvider
from card_platform.market_data.repository import MarketDataRepository
from card_platform.market_data.service import MarketDataService


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="requires a migrated PostgreSQL database",
)


class CountingProvider(MarketDataProvider):
    code = "e2e_market_provider"

    def __init__(self, status: int | None = None) -> None:
        self.calls = 0
        self.status = status

    async def fetch_sales(
        self, identity: MarketIdentity, *, since: datetime | None = None
    ) -> ProviderFetchResult:
        self.calls += 1
        if self.status:
            request = httpx.Request("GET", "https://example.com/authorized")
            response = httpx.Response(
                self.status,
                request=request,
                headers={"Retry-After": "60"},
            )
            raise httpx.HTTPStatusError("limited", request=request, response=response)
        return ProviderFetchResult(provider_code=self.code)


def identity() -> MarketIdentity:
    return MarketIdentity(
        player_name="Market Data E2E",
        season_start=2025,
        product_name="Authorized Test",
        card_number="ONE",
    )


@pytest.mark.asyncio
async def test_refresh_coalescing_cache_hit_and_circuit_breaker() -> None:
    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        database_pool_min_size=1,
        database_pool_max_size=20,
        valuation_worker_enabled=False,
    )
    database = Database(settings)
    await database.open()
    repository = MarketDataRepository()
    try:
        async with database.connection() as connection, connection.transaction():
            await connection.execute(
                """
                INSERT INTO data_source (code, name, source_type)
                VALUES ('e2e_market_provider', 'E2E provider', 'authorized_api')
                ON CONFLICT (code) DO NOTHING
                """
            )
            await connection.execute(
                """
                INSERT INTO provider_state (
                    provider_code, concurrent_limit, min_interval_ms, error_budget
                ) VALUES ('e2e_market_provider', 2, 0, 2)
                ON CONFLICT (provider_code) DO UPDATE SET
                    circuit_state = 'closed', active_requests = 0,
                    consecutive_failures = 0, circuit_until = NULL,
                    retry_after_until = NULL
                """
            )
            fingerprint = await repository.ensure_identity(connection, identity())

        async def enqueue() -> str:
            async with database.connection() as connection, connection.transaction():
                job_id, _ = await repository.enqueue_refresh(
                    connection, fingerprint, reason="concurrency_test"
                )
                return str(job_id)

        job_ids = await asyncio.gather(*(enqueue() for _ in range(100)))
        assert len(set(job_ids)) == 1

        now = datetime.now(timezone.utc)
        evidence = EvidenceRecord(
            provider_code="e2e_market_provider",
            external_sale_id="cached-sale",
            raw_title="Cached authorized sale",
            sale_price=Decimal("100"),
            currency="CNY",
            occurred_at=now - timedelta(days=1),
            source_url="https://example.com/cached-sale",
            evidence_level="confirmed_transaction",
            fetched_at=now,
        )
        async with database.connection() as connection, connection.transaction():
            await repository.store_evidence(connection, fingerprint, [evidence])
            await connection.execute(
                """
                UPDATE market_identity_cache
                SET freshness = 'fresh', evidence_count = 1,
                    data_as_of = %s, next_refresh_at = %s
                WHERE identity_fingerprint = %s
                """,
                (evidence.occurred_at, now + timedelta(hours=1), fingerprint),
            )
            await connection.execute(
                """
                UPDATE market_refresh_job SET status = 'succeeded', finished_at = now()
                WHERE identity_fingerprint = %s
                """,
                (fingerprint,),
            )
        provider = CountingProvider()
        cached = await MarketDataService(
            database, [ProtectedProvider(database, provider)]
        ).get(identity())
        assert cached.evidence
        assert provider.calls == 0

        limited_provider = CountingProvider(status=429)
        with pytest.raises(SourceLimitedError):
            await ProtectedProvider(database, limited_provider).fetch_sales(identity())
        async with database.connection() as connection:
            result = await connection.execute(
                """
                SELECT circuit_state, http_429_count, retry_after_until
                FROM provider_state WHERE provider_code = 'e2e_market_provider'
                """
            )
            state = await result.fetchone()
        assert state["circuit_state"] == "open"
        assert state["http_429_count"] >= 1
        assert state["retry_after_until"] > now

        async with database.connection() as connection:
            await connection.execute(
                """
                UPDATE provider_state
                SET circuit_until = now() - interval '1 second',
                    retry_after_until = now() - interval '1 second'
                WHERE provider_code = 'e2e_market_provider'
                """
            )
        recovered_provider = CountingProvider()
        await ProtectedProvider(database, recovered_provider).fetch_sales(identity())
        async with database.connection() as connection:
            result = await connection.execute(
                """
                SELECT circuit_state, consecutive_failures
                FROM provider_state WHERE provider_code = 'e2e_market_provider'
                """
            )
            recovered = await result.fetchone()
        assert recovered["circuit_state"] == "closed"
        assert recovered["consecutive_failures"] == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_finish_refresh_accepts_null_error_with_single_evidence() -> None:
    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        database_pool_min_size=1,
        database_pool_max_size=5,
        valuation_worker_enabled=False,
    )
    database = Database(settings)
    await database.open()
    repository = MarketDataRepository()
    target = identity().model_copy(
        update={"player_name": "Market Refresh Regression", "card_number": "NULL-ERROR"}
    )
    now = datetime.now(timezone.utc)
    try:
        async with database.connection() as connection, connection.transaction():
            await connection.execute(
                """
                INSERT INTO data_source (code, name, source_type)
                VALUES ('e2e_market_provider', 'E2E provider', 'authorized_api')
                ON CONFLICT (code) DO NOTHING
                """
            )
            fingerprint = await repository.ensure_identity(connection, target)
            job_id, _ = await repository.enqueue_refresh(
                connection, fingerprint, reason="null_error_regression"
            )
            claimed = await repository.claim_refresh(connection)
            assert claimed is not None
            evidence = EvidenceRecord(
                provider_code="e2e_market_provider",
                external_sale_id="single-high-quality-sale",
                raw_title="Single high quality sale",
                sale_price=Decimal("88"),
                currency="CNY",
                occurred_at=now,
                source_url="https://example.com/single-high-quality-sale",
                evidence_level="confirmed_transaction",
                fetched_at=now,
            )
            await repository.store_evidence(connection, fingerprint, [evidence])
            await repository.finish_refresh(
                connection,
                job_id,
                fingerprint,
                Freshness.INSUFFICIENT,
                ttl_seconds=3600,
                error=None,
            )
            result = await connection.execute(
                """
                SELECT status, error_message
                FROM market_refresh_job WHERE id = %s
                """,
                (job_id,),
            )
            finished = await result.fetchone()
            assert finished["status"] == "insufficient"
            assert finished["error_message"] is None
    finally:
        await database.close()
