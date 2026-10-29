import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url


IDENTITY_VERSION = 2


class Freshness(StrEnum):
    FRESH = "fresh"
    CACHED = "cached"
    STALE = "stale"
    SOURCE_LIMITED = "source_limited"
    INSUFFICIENT = "insufficient"


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.strip().casefold()) or None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


class MarketIdentity(BaseModel):
    player_name: str
    public_source_urls: list[str] = Field(default_factory=list, max_length=2)

    @field_validator('public_source_urls')
    @classmethod
    def public_urls(cls, values):
        urls = []
        for value in values:
            url = canonical_public_item_url(value)
            if not url:
                raise ValueError('only public CardHobby item discovery URLs are allowed')
            if url not in urls:
                urls.append(url)
        return urls

    player_name_aliases: list[str] = Field(default_factory=list)
    season_start: int | None = None
    season_end: int | None = None
    brand_name: str | None = None
    product_name: str | None = None
    set_name: str | None = None
    card_number: str | None = None
    insert_name: str | None = None
    parallel_name: str | None = None
    print_run: int | None = None
    serial_total: int | None = None
    is_redemption: bool = False
    redemption_status: str = "unknown"
    is_rookie: bool | None = False
    is_autograph: bool | None = False
    is_memorabilia: bool | None = False
    is_patch: bool | None = False
    grade_company: str | None = None
    grade_value: Decimal | None = None

    def normalized(self) -> dict[str, Any]:
        return {
            key: _normalize(value)
            for key, value in self.model_dump(mode="python").items()
            if value is not None and (key != "public_source_urls" or value)
        }

    @property
    def fingerprint(self) -> str:
        core_identity = self.normalized()
        core_identity.pop('public_source_urls', None)
        payload = json.dumps(
            {"version": IDENTITY_VERSION, **core_identity},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"v{IDENTITY_VERSION}:{hashlib.sha256(payload.encode()).hexdigest()}"


class EvidenceRecord(BaseModel):
    provider_code: str
    external_sale_id: str
    raw_title: str
    sale_price: Decimal = Field(gt=0)
    shipping_price: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(min_length=3, max_length=3)
    occurred_at: datetime
    source_url: str
    evidence_level: str
    grade_company: str | None = None
    grade_value: Decimal | None = None
    match_confidence: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    identity_classification: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_version: int = Field(default=1, gt=0)
    fetched_at: datetime


class ProviderFetchResult(BaseModel):
    provider_code: str
    public_image_sources: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    incremental_cursor: str | None = None
    query_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    web_research: dict[str, Any] = Field(default_factory=dict)
    source_error: str | None = None


class MarketDataResult(BaseModel):
    identity_fingerprint: str
    freshness: Freshness
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    data_as_of: datetime | None = None
    next_refresh_at: datetime | None = None
    refresh_job_id: str | None = None
    notes: list[str] = Field(default_factory=list)
    provider_diagnostics: list[dict[str, Any]] = Field(default_factory=list)

    def refresh_is_due(self, *, now: datetime | None = None) -> bool:
        """Whether another provider refresh may be queued now.

        ``freshness=insufficient`` describes evidence quantity, not refresh
        scheduling.  Keeping the backoff deadline separate lets valuation use
        one or two retained sales without immediately starting another refresh.
        """
        if self.next_refresh_at is None:
            return True
        deadline = self.next_refresh_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return deadline <= (now or datetime.now(timezone.utc))
