"""Exercise the exact projection SQL with a small public synthetic snapshot."""
import asyncio
import os

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost_knowledge import PURCHASE_KNOWLEDGE_SQL

pytestmark=pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,reason='requires isolated PostgreSQL')


def test_sql_preserves_scoped_checklist_artifacts_metadata_and_unicode_numbers():
    async def run():
        snapshot=dict(version=7,entities=[
            dict(id='p',entity_type='player',canonical_name='Example Player',aliases=['示例']),
            dict(id='official',entity_type='card',canonical_name='Official',attributes=dict(
                source_url='https://example.com/checklist',brand='Topps',product='Chrome',
                season_start=2025,season_end=2025,card_number='Ａ-1',player='Example Player',insert='Official Insert')),
            dict(id='casefold',entity_type='card',canonical_name='Unicode casefold',attributes=dict(
                source_url='https://example.com/checklist',brand='Topps',product='Chrome',
                season_start=2025,season_end=2025,card_number='ß-1',player='Example Player',insert='Casefold Insert')),
            dict(id='unrelated',entity_type='card',canonical_name='Other',attributes=dict(card_number='999')),
            dict(id='metadata',entity_type='card',canonical_name='Metadata',attributes=dict(catalog_mappings=[])),
        ],official_artifacts=[],relations=[])
        card=dict(player_name='Example Player',brand_name='Topps',product_name='Chrome',season_start=2025,card_number='A-1')
        sql=PURCHASE_KNOWLEDGE_SQL.split('    FROM knowledge_workspace')[0]+' FROM (SELECT %s::jsonb AS snapshot) kv'
        async with await AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],row_factory=dict_row) as conn:
            await conn.execute('SET TRANSACTION READ ONLY')
            result=await(await conn.execute(sql,(False,['a1'],Jsonb(snapshot)))).fetchone()
            projected=result['snapshot']
            assert {r['id'] for r in projected['entities']}=={'p','official','metadata','casefold'}
            assert projected['official_artifacts']==[] and projected['relations']==[]
            assert KnowledgeResolver(projected).normalize_identity(card)==KnowledgeResolver(snapshot).normalize_identity(card)
            assert KnowledgeResolver(projected).normalize_identity(card)[0]['insert_name']=='Official Insert'
            folded=await(await conn.execute(sql,(False,['ss1'],Jsonb(snapshot)))).fetchone()
            folded_card={**card,'card_number':'SS1'}
            assert KnowledgeResolver(folded['snapshot']).normalize_identity(folded_card)==KnowledgeResolver(snapshot).normalize_identity(folded_card)
            assert KnowledgeResolver(folded['snapshot']).normalize_identity(folded_card)[0]['insert_name']=='Casefold Insert'
            fallback=await(await conn.execute(sql,(True,[],Jsonb(snapshot)))).fetchone()
            assert fallback['snapshot']['entities']==snapshot['entities']
    asyncio.run(run())
