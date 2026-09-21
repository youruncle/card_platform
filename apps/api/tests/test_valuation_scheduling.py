import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.market_data.models import Freshness, MarketDataResult
from card_platform.market_data.protection import ProtectedProvider, ProviderConcurrencyBusy
from card_platform.market_data.repository import MarketDataRepository
from card_platform.market_data.service import MarketDataService
from card_platform.valuation_worker import AutomatedValuationWorker, ComparableSearchResult


class Context:
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *_): return None


class Connection:
    def __init__(self): self.execute = AsyncMock()
    def transaction(self): return Context(self)


class Database:
    def __init__(self): self.c = Connection()
    def connection(self): return Context(self.c)


@pytest.mark.asyncio
@pytest.mark.parametrize('due', [False,True])
async def test_wrong_version_history_queues_once_but_finished_empty_ttl_stops_loop(due):
    result=MarketDataResult(identity_fingerprint='same-physical-card',freshness=Freshness.CACHED,
        next_refresh_at=None if due else datetime.now(timezone.utc)+timedelta(days=2))
    repository=SimpleNamespace(cached=AsyncMock(return_value=result),enqueue_refresh=AsyncMock(return_value=(uuid4(),True)))
    service=MarketDataService(Database(),[],repository)
    queued=await service.refresh_after_rejected_cache('same-physical-card')
    assert bool(queued)==due
    assert repository.enqueue_refresh.await_count==int(due)
    assert 'FOR UPDATE' in service.database.c.execute.await_args.args[0]


@pytest.mark.asyncio
async def test_local_capacity_is_distinct_from_source_failure():
    provider = ProtectedProvider(Database(), SimpleNamespace(code='cardhobby'))
    provider._begin = AsyncMock(side_effect=ProviderConcurrencyBusy('local capacity'))
    with pytest.raises(ProviderConcurrencyBusy, match='local capacity busy'):
        await provider._reserve_request(timeout_seconds=0)


@pytest.mark.asyncio
async def test_busy_refresh_yields_without_publishing_insufficient_or_consuming_attempt():
    repository = SimpleNamespace(defer_refresh_for_capacity=AsyncMock(), fail_refresh=AsyncMock(), finish_refresh=AsyncMock(), store_evidence=AsyncMock())
    provider = SimpleNamespace(provider=SimpleNamespace(code='cardhobby'), fetch_sales=AsyncMock(side_effect=ProviderConcurrencyBusy('busy')))
    service = MarketDataService(Database(), [provider], repository)
    job = {'id':uuid4(), 'normalized_identity':{'player_name':'Example Player'}, 'data_as_of':None}
    await service.process_claimed_refresh(job)
    repository.defer_refresh_for_capacity.assert_awaited_once_with(service.database.c, job['id'])
    repository.fail_refresh.assert_not_awaited()
    repository.finish_refresh.assert_not_awaited()
    repository.store_evidence.assert_not_awaited()


@pytest.mark.asyncio
async def test_capacity_deferral_preserves_fifo_age_and_has_nonzero_backoff():
    c = Connection()
    await MarketDataRepository().defer_refresh_for_capacity(c, uuid4())
    sql = c.execute.await_args.args[0]
    assert "interval '5 seconds'" in sql
    assert 'GREATEST(attempt_count - 1, 0)' in sql
    assert 'created_at =' not in sql
    assert 'provider_state' not in sql
    assert "WHERE id = %s AND status = 'running'" in sql


@pytest.mark.asyncio
async def test_cancellation_releases_lease_without_source_failure():
    provider = ProtectedProvider(Database(), SimpleNamespace(code='cardhobby', fetch_sales=AsyncMock(side_effect=asyncio.CancelledError)))
    provider._reserve_request = AsyncMock(return_value=0)
    provider._release_cancelled = AsyncMock()
    provider._failure = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await provider.fetch_sales(SimpleNamespace())
    provider._release_cancelled.assert_awaited_once()
    provider._failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_valuation_is_not_starved_by_nonempty_refresh_queue():
    worker = object.__new__(AutomatedValuationWorker)
    worker._stop = asyncio.Event()
    worker._claim = AsyncMock(return_value=uuid4())
    worker._process = AsyncMock(side_effect=lambda *_: worker._stop.set())
    worker.market_data = SimpleNamespace(process_one_refresh=AsyncMock(return_value=True))
    await worker.run()
    worker._process.assert_awaited_once()
    worker.market_data.process_one_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_runs_when_no_valuation_is_ready():
    worker = object.__new__(AutomatedValuationWorker)
    worker._stop = asyncio.Event()
    worker._claim = AsyncMock(return_value=None)
    worker._process = AsyncMock()
    async def refresh():
        worker._stop.set()
        return True
    worker.market_data = SimpleNamespace(process_one_refresh=AsyncMock(side_effect=refresh))
    await worker.run()
    worker.market_data.process_one_refresh.assert_awaited_once()
    worker._process.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('only', [True, False])
async def test_sparse_fresh_cardhobby_cache_is_not_searched_twice(only):
    worker = object.__new__(AutomatedValuationWorker)
    cached = MarketDataResult(identity_fingerprint='test', freshness=Freshness.INSUFFICIENT, next_refresh_at=datetime.now(timezone.utc)+timedelta(hours=1), provider_diagnostics=[{'provider':'cardhobby','query_diagnostics':[{'query':'Example Player 2005','stage':'player_release_recovery','raw_count':4,'accepted_count':0,'outcome':'identity_gate_rejected'}]}])
    worker.market_data = SimpleNamespace(get=AsyncMock(return_value=cached), MIN_EVIDENCE=3)
    worker.knowledge_runtime = None
    worker.settings = SimpleNamespace(valuation_cardhobby_only=only, valuation_search_timeout_seconds=1)
    worker.client = object()
    worker.search_index = SimpleNamespace(enabled=False)
    worker._search_external_legacy = AsyncMock(return_value=ComparableSearchResult())
    worker._verify_comparable_images = AsyncMock(side_effect=lambda _, result: result)
    worker._review_comparable_evidence = AsyncMock(side_effect=lambda _, result: result)
    identity={'player_name':'Example Player','season_start':2005,'product_name':'Example Set','collection_item_id':uuid4(),'card_definition_id':uuid4()}
    result=await worker._search(identity)
    if only:
        worker._search_external_legacy.assert_not_awaited()
        assert '召回4/通过0' in result.search_queries[0]
    else:
        worker._search_external_legacy.assert_awaited_once_with(identity, include_cardhobby=False)


@pytest.mark.asyncio
async def test_waiting_valuation_only_defers_for_its_actual_active_refresh():
    worker = object.__new__(AutomatedValuationWorker)
    worker.database = Database()
    worker.database.c.execute.return_value = SimpleNamespace(rowcount=1)
    assert await worker._defer_until_market_refresh(uuid4(), str(uuid4()))
    sql = worker.database.c.execute.await_args.args[0]
    assert "refresh.status IN ('queued', 'running')" in sql
    assert 'binding.collection_item_id = job.collection_item_id' in sql
    assert 'GREATEST(job.attempt_count - 1, 0)' in sql
    assert 'INSERT INTO' not in sql
    worker.database.c.execute.return_value = SimpleNamespace(rowcount=0)
    assert not await worker._defer_until_market_refresh(uuid4(), str(uuid4()))


@pytest.mark.asyncio
@pytest.mark.parametrize('still_active', [True, False])
async def test_pending_empty_search_waits_but_completed_empty_search_finishes(monkeypatch, still_active):
    monkeypatch.setattr('card_platform.valuation_worker.valuation_identity_specificity', lambda _: (True, []))
    worker = object.__new__(AutomatedValuationWorker)
    worker.database = Database()
    worker._load_identity = AsyncMock(return_value={})
    worker._set_status = AsyncMock()
    worker._search = AsyncMock(side_effect=[ComparableSearchResult(pending_refresh_job_id=str(uuid4())), ComparableSearchResult()])
    worker._defer_until_market_refresh = AsyncMock(return_value=still_active)
    worker._store_comparables = AsyncMock()
    worker._calculate = AsyncMock()
    worker._finish = AsyncMock()
    worker.market_data = SimpleNamespace(repository=SimpleNamespace(enqueue_revaluation_after_concurrent_refresh=AsyncMock()))
    await worker._process(uuid4())
    if still_active:
        worker._calculate.assert_not_awaited()
        assert worker._search.await_count == 1
    else:
        worker._calculate.assert_awaited_once()
        assert worker._search.await_count == 2
    worker._finish.assert_not_awaited()
