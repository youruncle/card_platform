from datetime import timedelta
from uuid import UUID

from card_platform.db import Database
from card_platform.ingestion.cardhobby import CardHobbySearchError
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
            # A failed refresh can leave a full historical cache behind.
            # Its size does not make the interrupted source check complete.
            Freshness.SOURCE_LIMITED,
        }
        return needs_more_evidence and cached.refresh_is_due()

    async def get(
        self,
        identity: MarketIdentity,
        *,
        collection_item_id: UUID | None = None,
        card_definition_id: UUID | None = None,
        confirmed_identity_version_id: UUID | None = None,
        requested_by: UUID | None = None,
        queue_refresh: bool = True,
        public_image_leads: list[dict] | None = None,
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
                    cached.data_as_of = max(
                        item.occurred_at for item in cached.evidence
                    )
                    if cached.freshness == Freshness.INSUFFICIENT:
                        cached.freshness = Freshness.CACHED
                    cached.notes.append(
                        f"按当前身份重新校验 {len(reusable)} 条同卡定义历史成交"
                    )
            # Scoped review leads are not a fresh source check and must not
            # suppress a refresh that the ordinary cache still needs.
            refresh_due = self.should_refresh(cached)
            if collection_item_id is not None and confirmed_identity_version_id is not None:
                reviewed = await self.repository.reviewed_evidence_for_identity(
                    connection, collection_item_id, confirmed_identity_version_id
                )
                existing = {
                    (item.provider_code, item.external_sale_id)
                    for item in cached.evidence
                }
                for item in reviewed:
                    key = (item.provider_code, item.external_sale_id)
                    if key not in existing:
                        cached.evidence.append(item)
                        existing.add(key)
                cached.evidence.sort(key=lambda item: item.occurred_at, reverse=True)
            if public_image_leads:
                discovered = await self.repository.evidence_for_public_image_leads(connection, public_image_leads)
                if discovered:
                    # Catalogue observations may not have a market_evidence
                    # row yet. Persist public facts before the worker links a
                    # comparable; retrieval supplies no identity approval.
                    await self.repository.store_evidence(connection, fingerprint, discovered)
                existing = {(item.provider_code, item.external_sale_id) for item in cached.evidence}
                added = 0
                for item in discovered:
                    key = (item.provider_code, item.external_sale_id)
                    if key not in existing:
                        cached.evidence.append(item)
                        existing.add(key)
                        added += 1
                cached.evidence.sort(key=lambda item: item.occurred_at, reverse=True)
                cached.notes.append(f'本地公开图片索引补充 {added} 条待核验成交候选；相似度不代表同款')
            if cached.evidence:
                await connection.execute(
                    """
                    UPDATE provider_state SET cache_hit_count = cache_hit_count + 1
                    WHERE provider_code = 'cardhobby'
                    """
                )
            if queue_refresh and refresh_due:
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
            public_image_sources = []
            public_gallery_pages = []
            source_limited = False
            errors = []
            diagnostics = []
            for provider in self.providers:
                provider_code = provider.provider.code
                try:
                    fetched = await provider.fetch_sales(identity, since=job["data_as_of"])
                    all_evidence.extend(fetched.evidence)
                    if provider_code == 'cardhobby':
                        public_image_sources.extend(fetched.public_image_sources)
                        public_gallery_pages.extend(page for page in fetched.query_diagnostics
                            if page.get('stage') == 'public_item_source')
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
                            **({
                                "query_diagnostics": [
                                    item.model_dump(mode="json") for item in error.query_diagnostics
                                ],
                                "retryable": error.retryable,
                            } if isinstance(error, CardHobbySearchError) else {}),
                        }
                    )
            async with self.database.connection() as connection, connection.transaction():
                if public_image_sources:
                    from card_platform.public_source_catalog import store_observations
                    await store_observations(connection, public_image_sources)
                await self.repository.store_evidence(
                    connection, job["identity_fingerprint"], all_evidence
                )
                if public_gallery_pages:
                    await self.repository.store_public_gallery_observations(connection, public_gallery_pages)
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
