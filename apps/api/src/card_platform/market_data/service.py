from datetime import timedelta
from uuid import UUID

from card_platform.db import Database
from card_platform.market_data.models import Freshness, MarketDataResult, MarketIdentity
from card_platform.market_data.protection import ProtectedProvider, SourceLimitedError
from card_platform.market_data.repository import MarketDataRepository


class MarketDataService:
    MIN_EVIDENCE = 3

    def __init__(
        self,
        database: Database,
        providers: list[ProtectedProvider] | None = None,
        repository: MarketDataRepository | None = None,
    ) -> None:
        self.database = database
        self.providers = providers or []
        self.repository = repository or MarketDataRepository()

    @staticmethod
    def ttl_seconds(popularity_score: int) -> int:
        if popularity_score >= 100:
            return int(timedelta(hours=2).total_seconds())
        if popularity_score >= 20:
            return int(timedelta(hours=8).total_seconds())
        return int(timedelta(days=2).total_seconds())

    async def get(
        self,
        identity: MarketIdentity,
        *,
        collection_item_id: UUID | None = None,
        card_definition_id: UUID | None = None,
        requested_by: UUID | None = None,
        queue_refresh: bool = True,
    ) -> MarketDataResult:
        async with self.database.connection() as connection, connection.transaction():
            fingerprint = await self.repository.ensure_identity(
                connection, identity, collection_item_id
            )
            if card_definition_id is not None:
                await self.repository.adopt_legacy_evidence(
                    connection, fingerprint, card_definition_id
                )
            cached = await self.repository.cached(connection, fingerprint)
            needs_refresh = (
                len(cached.evidence) < self.MIN_EVIDENCE
                or cached.freshness in {Freshness.STALE, Freshness.INSUFFICIENT}
            )
            if cached.evidence:
                await connection.execute(
                    """
                    UPDATE provider_state SET cache_hit_count = cache_hit_count + 1
                    WHERE provider_code = 'cardhobby'
                    """
                )
            if queue_refresh and needs_refresh:
                job_id, _ = await self.repository.enqueue_refresh(
                    connection,
                    fingerprint,
                    requested_by=requested_by,
                    reason=cached.freshness.value,
                )
                cached.refresh_job_id = str(job_id)
                cached.notes.append("市场证据刷新已排队；本次估值使用现有缓存")
            if cached.freshness == Freshness.SOURCE_LIMITED and cached.evidence:
                cached.notes.append("来源当前受限，使用历史缓存")
            return cached

    async def process_one_refresh(self) -> bool:
        async with self.database.connection() as connection, connection.transaction():
            job = await self.repository.claim_refresh(connection)
        if job is None:
            return False
        try:
            identity = MarketIdentity.model_validate(job["normalized_identity"])
            all_evidence = []
            source_limited = False
            errors = []
            for provider in self.providers:
                try:
                    fetched = await provider.fetch_sales(identity, since=job["data_as_of"])
                    all_evidence.extend(fetched.evidence)
                except SourceLimitedError as error:
                    source_limited = True
                    errors.append(str(error))
                except Exception as error:
                    errors.append(str(error)[:300])
            async with self.database.connection() as connection, connection.transaction():
                await self.repository.store_evidence(
                    connection, job["identity_fingerprint"], all_evidence
                )
                cached = await self.repository.cached(
                    connection, job["identity_fingerprint"]
                )
                count = len(cached.evidence)
                if source_limited:
                    freshness = Freshness.SOURCE_LIMITED
                elif count >= self.MIN_EVIDENCE:
                    freshness = Freshness.FRESH
                else:
                    freshness = Freshness.INSUFFICIENT
                await self.repository.finish_refresh(
                    connection,
                    job["id"],
                    job["identity_fingerprint"],
                    freshness,
                    ttl_seconds=self.ttl_seconds(0),
                    error="; ".join(errors) or None,
                )
        except Exception as error:
            message = f"{type(error).__name__}: {str(error)}"[:500]
            async with self.database.connection() as connection, connection.transaction():
                await self.repository.fail_refresh(
                    connection,
                    job["id"],
                    job["identity_fingerprint"],
                    message,
                )
            raise
        return True
