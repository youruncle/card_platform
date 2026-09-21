from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.market_data.cardhobby_provider import CardHobbyMarketDataProvider
from card_platform.valuation_worker import AutomatedValuationWorker
from test_cardhobby_market_data_provider import FakeCardHobbyClient


@pytest.mark.asyncio
async def test_direct_retrieval_persists_original_source_before_returning_comparables():
    @asynccontextmanager
    async def transaction():
        yield

    connection = SimpleNamespace(transaction=transaction)

    @asynccontextmanager
    async def connect():
        yield connection

    repository = SimpleNamespace(
        ensure_identity=AsyncMock(return_value="fingerprint"),
        store_evidence=AsyncMock(),
    )
    worker = object.__new__(AutomatedValuationWorker)
    worker.database = SimpleNamespace(connection=connect)
    worker.market_data = SimpleNamespace(repository=repository, MIN_EVIDENCE=3)
    worker.cardhobby = FakeCardHobbyClient()
    worker.settings = SimpleNamespace(valuation_verify_sources=False, valuation_cardhobby_only=True)
    identity = {"collection_item_id": uuid4(), "player_name": "Luka Doncic"}

    result = await worker._search_external_legacy(identity)

    assert len(result.comparables) == 1
    repository.store_evidence.assert_awaited_once()
    stored = repository.store_evidence.await_args.args[2][0]
    assert stored.raw_payload["bid_count"] == 3
    assert stored.raw_payload["sold_status_evidence"] == "Status=-2"
    assert stored.evidence_level == "ended_auction"
    assert stored.external_sale_id == "item:123456"


@pytest.mark.asyncio
async def test_direct_and_refresh_normalizers_preserve_identical_sale_facts():
    result = await FakeCardHobbyClient().search({})
    normalized = CardHobbyMarketDataProvider.normalize_result(result)
    evidence = normalized.evidence[0]
    assert evidence.raw_payload == result.sales[0].model_dump(mode="json")
    assert evidence.sale_price == result.sales[0].sale_price
    assert evidence.shipping_price == result.sales[0].shipping_price
