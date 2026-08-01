import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


IDENTITY_VERSION = 1


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
    is_rookie: bool = False
    is_autograph: bool = False
    is_patch: bool = False
    grade_company: str | None = None
    grade_value: Decimal | None = None

    def normalized(self) -> dict[str, Any]:
        return {
            key: _normalize(value)
            for key, value in self.model_dump(mode="python").items()
            if value is not None
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {"version": IDENTITY_VERSION, **self.normalized()},
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
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    incremental_cursor: str | None = None


class MarketDataResult(BaseModel):
    identity_fingerprint: str
    freshness: Freshness
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    data_as_of: datetime | None = None
    refresh_job_id: str | None = None
    notes: list[str] = Field(default_factory=list)
