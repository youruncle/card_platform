from datetime import datetime, timezone

from card_platform.ingestion.cardhobby import (
    CardHobbyProvider as LegacyCardHobbyClient,
    cardhobby_external_id,
)
from card_platform.market_data.models import (
    EvidenceRecord,
    MarketIdentity,
    ProviderFetchResult,
)
from card_platform.market_data.provider import MarketDataProvider


class CardHobbyMarketDataProvider(MarketDataProvider):
    """Compliance adapter for CardHobby's existing public-data client."""

    code = "cardhobby"

    def __init__(self, client: LegacyCardHobbyClient) -> None:
        self.client = client

    async def fetch_sales(
        self,
        identity: MarketIdentity,
        *,
        since: datetime | None = None,
    ) -> ProviderFetchResult:
        result = await self.client.search(identity.model_dump(mode="python"))
        fetched_at = datetime.now(timezone.utc)
        evidence = []
        for sale in result.sales:
            # SearchCommodity returns a bounded relevance snapshot, not an
            # incremental event feed. Re-ingest the full snapshot so matcher,
            # alias or identity-version upgrades can backfill older exact sales;
            # the repository's provider/external-id uniqueness keeps this safe.
            external_id = cardhobby_external_id(
                sale.source_url, sale.title, sale.occurred_at, sale.sale_price
            )
            if external_id is None:
                continue
            evidence.append(
                EvidenceRecord(
                    provider_code=self.code,
                    external_sale_id=external_id,
                    raw_title=sale.title,
                    sale_price=sale.sale_price,
                    shipping_price=sale.shipping_price,
                    currency="CNY",
                    occurred_at=sale.occurred_at,
                    source_url=sale.source_url,
                    evidence_level=sale.evidence_level,
                    grade_company=sale.grade_company,
                    grade_value=sale.grade_value,
                    match_confidence=sale.match_confidence,
                    identity_classification={
                        "matching_fields": sale.matching_fields,
                        "conflicts": sale.conflicts,
                        "source_reliability": str(sale.source_reliability),
                    },
                    raw_payload=sale.model_dump(mode="json"),
                    fetched_at=fetched_at,
                )
            )
        return ProviderFetchResult(
            provider_code=self.code,
            evidence=evidence,
            notes=result.notes,
        )
