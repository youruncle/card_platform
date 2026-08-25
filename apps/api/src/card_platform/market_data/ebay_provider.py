import base64
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import (
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
)
from card_platform.market_data.models import (
    EvidenceRecord,
    MarketIdentity,
    ProviderFetchResult,
)
from card_platform.market_data.provider import MarketDataProvider


EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"
GRADE_PATTERN = re.compile(r"\b(PSA|BGS|SGC|CGC)\s*(\d+(?:\.\d+)?)\b", re.I)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_grade(title: str) -> tuple[str | None, Decimal | None]:
    match = GRADE_PATTERN.search(title)
    if not match:
        return None, None
    return match.group(1).upper(), Decimal(match.group(2))


def _price(row: dict) -> tuple[Decimal, str] | None:
    price = (
        row.get("lastSoldPrice")
        or row.get("soldPrice")
        or row.get("itemSalePrice")
        or row.get("price")
    )
    if not isinstance(price, dict):
        return None
    try:
        value = Decimal(str(price.get("value")))
    except (InvalidOperation, TypeError):
        return None
    currency = str(price.get("currency") or "").upper()
    return (value, currency) if value > 0 and len(currency) == 3 else None


class EbayMarketplaceInsightsProvider(MarketDataProvider):
    """Official eBay sold-item adapter; never falls back to page scraping."""

    code = "ebay"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        marketplace_id: str = "EBAY_US",
        token_url: str = "https://api.ebay.com/identity/v1/oauth2/token",
        insights_url: str = (
            "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
        ),
        timeout: float = 15,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("eBay Marketplace Insights credentials are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace_id = marketplace_id
        self.token_url = token_url
        self.insights_url = insights_url
        self.timeout = timeout
        self.client = client

    @staticmethod
    def build_query(identity: MarketIdentity) -> str:
        return EbayMarketplaceInsightsProvider.build_queries(identity)[0]

    @staticmethod
    def build_queries(identity: MarketIdentity) -> list[str]:
        season = (
            f"{identity.season_start}-{str(identity.season_end)[-2:]}"
            if identity.season_start and identity.season_end
            else str(identity.season_start or "")
        )
        descriptor = identity.insert_name or identity.set_name
        grade = " ".join(
            value
            for value in (
                identity.grade_company or "",
                str(identity.grade_value) if identity.grade_value is not None else "",
            )
            if value
        )

        def query(*parts: object) -> str:
            return " ".join(
                str(value).strip() for value in parts if str(value or "").strip()
            )

        values = [
            query(
                identity.player_name,
                season,
                identity.brand_name,
                identity.product_name,
                descriptor,
                identity.parallel_name,
                identity.card_number,
                f"/{identity.serial_total}" if identity.serial_total else None,
                grade,
            ),
            query(
                identity.player_name,
                season,
                identity.product_name,
                descriptor,
                identity.card_number,
                grade,
            ),
            query(
                identity.player_name,
                identity.product_name,
                identity.card_number,
                grade,
            ),
            query(identity.player_name, descriptor, identity.card_number, grade),
            query(identity.player_name, identity.card_number, grade),
            query(identity.player_name, identity.product_name, grade),
        ]
        return list(dict.fromkeys(value for value in values if value))

    async def _token(self, client: httpx.AsyncClient) -> str:
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        response = await client.post(
            self.token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
        )
        if response.is_error:
            try:
                error_payload = response.json()
            except ValueError:
                detail = response.text[:300]
            else:
                detail = ": ".join(
                    str(value)
                    for value in (
                        error_payload.get("error"),
                        error_payload.get("error_description"),
                    )
                    if value
                )[:300]
            raise httpx.HTTPStatusError(
                f"eBay OAuth {response.status_code}: {detail or 'unknown error'}",
                request=response.request,
                response=response,
            )
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("eBay OAuth response did not contain access_token")
        return str(token)

    async def fetch_sales(
        self, identity: MarketIdentity, *, since: datetime | None = None
    ) -> ProviderFetchResult:
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            token = await self._token(client)
            common_params: dict[str, object] = {"limit": 200}
            if since:
                start = since.astimezone(timezone.utc).isoformat(timespec="milliseconds")
                common_params["filter"] = (
                    f"lastSoldDate:[{start.replace('+00:00', 'Z')}..]"
                )
            rows_by_id: dict[str, dict] = {}
            queries = self.build_queries(identity)
            for query_text in queries:
                response = await client.get(
                    self.insights_url,
                    params={**common_params, "q": query_text},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                for row in payload.get("itemSales") or payload.get("itemSummaries") or []:
                    external_id = str(
                        row.get("itemId") or row.get("legacyItemId") or ""
                    ).strip()
                    if external_id:
                        rows_by_id.setdefault(
                            external_id,
                            {**row, "matched_query": query_text},
                        )
        finally:
            if owned_client:
                await client.aclose()

        target = CardIdentityResolver().resolve(identity.model_dump(mode="python"))
        classifier = ComparableClassifier()
        fetched_at = datetime.now(timezone.utc)
        evidence: list[EvidenceRecord] = []
        for row in rows_by_id.values():
            title = str(row.get("title") or "").strip()
            occurred_at = _parse_datetime(
                row.get("lastSoldDate") or row.get("itemEndDate") or row.get("soldDate")
            )
            price = _price(row)
            url = str(row.get("itemWebUrl") or row.get("itemHref") or "").strip()
            external_id = str(row.get("itemId") or row.get("legacyItemId") or "").strip()
            if not all((title, occurred_at, price, url, external_id)):
                continue
            hostname = (urlparse(url).hostname or "").casefold()
            if hostname != "ebay.com" and not hostname.endswith(".ebay.com"):
                continue
            if since and occurred_at <= since:
                continue
            grade_company, grade_value = _parse_grade(title)
            classification = classifier.classify(
                target,
                ComparableInput(
                    title=title,
                    grade_company=grade_company,
                    grade_value=grade_value,
                    source_reliability=Decimal("0.95"),
                    source_code=self.code,
                ),
            )
            if classification.tier is ComparableTier.REJECTED:
                continue
            evidence.append(
                EvidenceRecord(
                    provider_code=self.code,
                    external_sale_id=external_id,
                    raw_title=title,
                    sale_price=price[0],
                    currency=price[1],
                    occurred_at=occurred_at,
                    source_url=url,
                    evidence_level="confirmed_transaction",
                    grade_company=grade_company,
                    grade_value=grade_value,
                    match_confidence=classification.similarity,
                    identity_classification={
                        "tier": classification.tier.value,
                        "field_matches": classification.field_matches,
                        "source_reliability": str(classification.source_reliability),
                        "origin_source_code": "ebay",
                    },
                    raw_payload=row,
                    fetched_at=fetched_at,
                )
            )
        return ProviderFetchResult(
            provider_code=self.code,
            evidence=evidence,
            notes=[
                "eBay official Marketplace Insights returned "
                f"{len(evidence)} usable sales across {len(queries)} query levels"
            ],
        )
