from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate
from card_platform.market_data.models import EvidenceRecord, Freshness, MarketDataResult
from card_platform.valuation_worker import AutomatedValuationWorker


@pytest.mark.asyncio
@pytest.mark.parametrize("product,insert,number", [
    ("Topps Chrome Update", "Clutch Gene", "CG-10"),
    ("Donruss", "All Heart", "3"),
])
async def test_twelve_weak_image_candidates_cannot_starve_explicit_insert_proxy(product, insert, number):
    now=datetime.now(timezone.utc)
    identity=dict(player_name="Example Player",season_start=2025,season_end=2026,
                  product_name=product,insert_name=insert,card_number=number,
                  serial_total=150,collection_item_id=uuid4(),card_definition_id=uuid4())
    def evidence(index,title, *, force_unsafe=False):
        sale=CardHobbySale(item_id=str(100000+index),title=title,
            source_url=f"https://cardhobby.com.cn/Market/Item/{100000+index}",
            occurred_at=now,sale_price=Decimal("3"),currency="CNY",bid_count=2,
            sale_type="auction",evidence_level="ended_auction",sold_status_evidence="Status=-2 with bids",
            image_url=f"https://img.cardhobby.com/{index}.jpg",match_confidence=Decimal(".9"))
        score,conflicts,tier=assess_cardhobby_candidate(identity,sale)
        sale.match_confidence=score
        sale.conflicts=conflicts
        sale.matching_fields.append(f"cardhobby_tier:{tier}")
        return EvidenceRecord(provider_code="cardhobby",external_sale_id=sale.item_id,
            raw_title=title,sale_price=sale.sale_price,currency="CNY",occurred_at=now,
            source_url=sale.source_url,evidence_level="ended_auction",fetched_at=now,
            match_confidence=Decimal(".99") if force_unsafe else score,
            identity_classification={"conflicts":conflicts,"matching_fields":sale.matching_fields},
            raw_payload=sale.model_dump(mode="json"))
    weak=[evidence(i,f"2025-26 {product} Example Player 150编 蓝折") for i in range(12)]
    strong=evidence(20,f"2025-26 {product} Example Player {insert} 特卡")
    unsafe=evidence(30,f"2025-26 {product} Different Person {insert} #{number} 150编",force_unsafe=True)
    assert all(row.match_confidence>strong.match_confidence for row in weak)
    cached=MarketDataResult(identity_fingerprint="fixture",freshness=Freshness.FRESH,
        evidence=[*weak,strong,unsafe],next_refresh_at=now+timedelta(days=1))
    worker=object.__new__(AutomatedValuationWorker)
    worker.market_data=SimpleNamespace(get=AsyncMock(return_value=cached),MIN_EVIDENCE=3)
    worker.knowledge_runtime=None
    worker.settings=SimpleNamespace(valuation_cardhobby_only=True)
    worker._verify_comparable_images=AsyncMock(side_effect=lambda _,result:result)
    worker._review_comparable_evidence=AsyncMock(side_effect=lambda _,result:result)
    result=await worker._search(identity)
    assert len(result.comparables)==12
    assert result.comparables[0].source_url==strong.source_url
    assert "cache_title_anchor:insert" in result.comparables[0].matching_fields
    assert unsafe.source_url not in [row.source_url for row in result.comparables]
    assert any(row["source_url"]==unsafe.source_url for row in result.rejected_candidates)

