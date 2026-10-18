import asyncio
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from card_platform.repository import PortfolioRepository
from card_platform.schemas import CardIdentityInput, CollectionItemCreate

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,
                               reason='requires migrated isolated test PostgreSQL')


@pytest.mark.parametrize('fallback_age', ['2 days', '0 days'])
def test_historical_codex_price_does_not_turn_latest_insufficient_job_into_success(fallback_age):
    asyncio.run(check_state(fallback_age))


async def check_state(fallback_age):
    async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as conn:
        try:
            owner = await (await conn.execute(
                "INSERT INTO app_user(email,display_name) VALUES(%s,'audit test') RETURNING id",
                (f'{uuid4()}@example.com',))).fetchone()
            repo = PortfolioRepository()
            item = await repo.create_item(conn, CollectionItemCreate(owner_id=owner['id'], card=CardIdentityInput(
                player_name='Audit Synthetic Player', brand_name='Test', product_name='Test', season_start=2025)))
            await conn.execute('DELETE FROM valuation_job WHERE collection_item_id=%s', (item,))
            await conn.execute("""INSERT INTO valuation_snapshot(collection_item_id,method,currency,
                low_value,fair_value,high_value,confidence,evidence,model_version,valued_at)
                VALUES(%s,'ai_estimate','CNY',34,39,62,.35,
                '{"pricing_basis":"codex_independent_fallback","source_analyzed_at":"2026-01-01"}',
                'codex-independent-fallback-v1',now()-%s::interval)""", (item, fallback_age))
            await conn.execute("""INSERT INTO valuation_job(collection_item_id,status,created_at,error_message,result_summary)
                VALUES(%s,'insufficient_evidence',now()-interval '1 day','AccessDenied.Unpurchased',
                       '{"reason":"model access denied"}')""", (item,))
            result = (await repo.list_items(conn, owner['id'], item_ids=[item]))[0]
            assert result.valuation_status == 'insufficient_evidence'
            assert result.valuation_error == 'AccessDenied.Unpurchased'
            assert result.valuation_result_summary['reason'] == 'model access denied'
            assert result.valuation_result_summary['historical_reference']['pricing_basis'] == 'codex_independent_fallback'
            assert result.valuation_result_summary['historical_reference']['current_job_verified'] is False
        finally:
            await conn.rollback()


def test_qualified_research_fallback_uses_current_database_identity(tmp_path):
    asyncio.run(check_research_fallback(tmp_path))


async def check_research_fallback(tmp_path):
    import hashlib
    from datetime import datetime, timezone, timedelta
    from psycopg.types.json import Jsonb
    from test_codex_sales_research import research
    from card_platform.codex_valuation_fallback import promote_codex_valuation
    packet=research()
    now=datetime.now(timezone.utc)
    for search in packet['searches']:
        search['executed_at']=now.isoformat()
    for sale in packet['sales']:
        sale.update(checked_at=now.isoformat(), sold_at=(now-timedelta(days=1)).isoformat())
    hashes={}
    for side in ('front','back'):
        content=side.encode();(tmp_path/f'{side}.jpg').write_bytes(content)
        hashes[side]=hashlib.sha256(content).hexdigest()
        packet[f'{side}_sha256']=hashes[side]
    async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as conn:
        try:
            owner=(await (await conn.execute("INSERT INTO app_user(email,display_name) VALUES(%s,'research test') RETURNING id",
                        (f'{uuid4()}@example.com',))).fetchone())['id']
            item=await PortfolioRepository().create_item(conn, CollectionItemCreate(owner_id=owner,
                card=CardIdentityInput(**packet['identity'])))
            packet['collection_item_id']=str(item)
            await conn.execute('DELETE FROM valuation_job WHERE collection_item_id=%s',(item,))
            await conn.execute("UPDATE collection_item SET image_front_url='/uploads/front.jpg',image_back_url='/uploads/back.jpg' WHERE id=%s",(item,))
            job=(await (await conn.execute("INSERT INTO card_analysis_job(owner_id,collection_item_id,status) VALUES(%s,%s,'succeeded') RETURNING id",(owner,item))).fetchone())['id']
            raw=dict(collection_item_id=str(item),market_research=packet,research_captures_verified=True,
                front_sha256=hashes['front'],back_sha256=hashes['back'])
            await conn.execute("""INSERT INTO card_analysis_snapshot(job_id,collection_item_id,analysis_version,source,model_name,
                identity_summary,identity_confidence,currency,fair_value_low,fair_value,fair_value_high,valuation_confidence,raw_result,status)
                VALUES(%s,%s,1,'codex_multimodal','test','qualified synthetic',.95,'CNY',34,39,62,.35,%s,'succeeded')""",(job,item,Jsonb(raw)))
            result=await promote_codex_valuation(conn,item,tmp_path,dry_run=True)
            assert result['status']=='eligible',result
        finally:
            await conn.rollback()
