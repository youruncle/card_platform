from datetime import timedelta
from uuid import UUID

from card_platform.db import Database
from card_platform.market_data.models import Freshness, MarketDataResult, MarketIdentity
from card_platform.market_data.protection import (
    ProtectedProvider, ProviderConcurrencyBusy, SourceLimitedError,
)
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

    @classmethod
    def should_refresh(cls, cached: MarketDataResult) -> bool:
        """Return whether evidence needs refresh and its backoff has expired."""
        needs_more_evidence = len(cached.evidence) < cls.MIN_EVIDENCE or cached.freshness in {
            Freshness.STALE,
            Freshness.INSUFFICIENT,
        }
        return needs_more_evidence and cached.refresh_is_due()

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
            if card_definition_id is not None:
                historical = await self.repository.historical_evidence_for_card_definition(
                    connection, fingerprint, card_definition_id
                )
                existing = {
                    (item.provider_code, item.external_sale_id)
                    for item in cached.evidence
                }
                reusable = [
                    item
                    for item in historical
                    if (item.provider_code, item.external_sale_id) not in existing
                ]
                if reusable:
                    cached.evidence.extend(reusable)
                    cached.evidence.sort(
                        key=lambda item: item.occurred_at,
                        reverse=True,
                    )
                    cached.evidence = cached.evidence[:100]
                    cached.data_as_of = max(
                        item.occurred_at for item in cached.evidence
                    )
                    if cached.freshness == Freshness.INSUFFICIENT:
                        cached.freshness = Freshness.CACHED
                    cached.notes.append(
                        f"按当前身份重新校验 {len(reusable)} 条同卡定义历史成交"
                    )
            if cached.evidence:
                await connection.execute(
                    """
                    UPDATE provider_state SET cache_hit_count = cache_hit_count + 1
                    WHERE provider_code = 'cardhobby'
                    """
                )
            if queue_refresh and self.should_refresh(cached):
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
        await self.process_claimed_refresh(job)
        return True

    async def refresh_after_rejected_cache(self, fingerprint: str) -> str | None:
        """A pile of wrong-version historical rows is not usable evidence.

        Recheck the durable TTL under a row lock so a completed empty fetch or
        source-error backoff cannot be repeatedly requeued by old candidates.
        Active refreshes are coalesced by the repository's unique constraint.
        """
        async with self.database.connection() as connection, connection.transaction():
            await connection.execute(
                "SELECT identity_fingerprint FROM market_identity_cache WHERE identity_fingerprint=%s FOR UPDATE",
                (fingerprint,),
            )
            cached = await self.repository.cached(connection, fingerprint)
            if not cached.refresh_is_due():
                return None
            job_id, _ = await self.repository.enqueue_refresh(
                connection, fingerprint,
                reason="cached candidates all rejected under current identity",
            )
            return str(job_id)

    async def process_claimed_refresh(self, job: dict) -> None:
        """Process a refresh that the caller has already claimed.

        Keeping provider I/O outside the claim transaction is intentional.  A
        batch command can atomically enqueue and claim one specific identity,
        commit its ``running`` lease, then execute this shared refresh body with
        the exact same provider, persistence, and audit semantics as the worker.
        """
        try:
            identity = MarketIdentity.model_validate(job["normalized_identity"])
            all_evidence = []
            source_limited = False
            errors = []
            diagnostics = []
            for provider in self.providers:
                provider_code = provider.provider.code
                try:
                    fetched = await provider.fetch_sales(identity, since=job["data_as_of"])
                    all_evidence.extend(fetched.evidence)
                    if fetched.source_error:
                        source_limited = True
                        errors.append(f"{provider_code}: {fetched.source_error}")
                    diagnostics.append(
                        {
                            "provider": provider_code,
                            "status": "source_limited" if fetched.source_error else "succeeded",
                            "evidence_count": len(fetched.evidence),
                            "notes": fetched.notes,
                            "query_diagnostics": fetched.query_diagnostics,
                            "error": fetched.source_error,
                        }
                    )
                except ProviderConcurrencyBusy:
                    # Nothing was sent to the source: yield the local job,
                    # without consuming network attempts or publishing an
                    # empty/source-limited market result.
                    async with self.database.connection() as connection, connection.transaction():
                        await self.repository.defer_refresh_for_capacity(connection, job["id"])
                    return
                except SourceLimitedError as error:
                    source_limited = True
                    message = f"{provider_code}: {str(error)}"
                    errors.append(message)
                    diagnostics.append(
                        {
                            "provider": provider_code,
                            "status": "source_limited",
                            "evidence_count": 0,
                            "error": str(error)[:300],
                        }
                    )
                except Exception as error:
                    # A provider failure is not a zero-result response. Preserve
                    # that distinction and avoid declaring the market empty.
                    source_limited = True
                    message = f"{provider_code}: {type(error).__name__}: {str(error)[:240]}"
                    errors.append(message)
                    diagnostics.append(
                        {
                            "provider": provider_code,
                            "status": "failed",
                            "evidence_count": 0,
                            "error": f"{type(error).__name__}: {str(error)[:240]}",
                        }
                    )
            async with self.database.connection() as connection, connection.transaction():
                await self.repository.store_evidence(
                    connection, job["identity_fingerprint"], all_evidence
                )
                cached = await self.repository.cached(connection, job["identity_fingerprint"])
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
                    result_summary={
                        "providers": diagnostics,
                        "fetched_candidate_count": len(all_evidence),
                        "retained_evidence_count": count,
                    },
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
