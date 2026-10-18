"""Cost matching must not load the unrelated 160k-card checklist catalogue."""
import asyncio

from card_platform.purchase_cost_knowledge import load_purchase_cost_knowledge
from card_platform.purchase_cost import score_match
from card_platform.knowledge import KnowledgeResolver


def test_projected_knowledge_preserves_published_identity_aliases_and_query_vocabulary():
    snapshot = {'version': 9, 'entities': [
        {'id':'p','entity_type':'player','canonical_name':'Example Player','aliases':['示例球员'],
         'attributes':{'query_aliases':['示例']}},
        {'id':'b','entity_type':'brand','canonical_name':'Topps','aliases':['拓普斯']},
        {'id':'s','entity_type':'product','canonical_name':'Chrome','aliases':['铬']},
        {'id':'c','entity_type':'card','canonical_name':'irrelevant checklist card','aliases':[]},
    ]}
    class Cursor:
        async def fetchone(self):
            return {'snapshot': {**snapshot,'entities':[r for r in snapshot['entities'] if r['entity_type']!='card']}}
    class Conn:
        async def execute(self,sql,params):
            # A DB-side projection is necessary: a Python-only filter still
            # transfers/deserializes the whole 190MB published snapshot.
            assert 'jsonb_array_elements' in sql
            assert params == (False, [])
            assert 'SELECT kv.snapshot FROM' not in sql
            return Cursor()
    projected=asyncio.run(load_purchase_cost_knowledge(Conn(), dict(player_name='Example Player')))
    original=KnowledgeResolver(snapshot)
    card=dict(player_name='Example Player',brand_name='Topps',product_name='Chrome',season_start=2025)
    line=dict(title='2025 拓普斯 铬 示例球员 Base')
    assert score_match(card,line,projected)==score_match(card,line,original)
    assert projected.query_aliases_for('player','Example Player')==('示例',)
    assert projected.version==9
    assert 'c' not in projected.entities


def test_official_checklist_enrichment_is_part_of_cost_identity_not_optional():
    from card_platform.knowledge_card_catalog import ScopedCardCatalog
    entities = [dict(id='official',entity_type='card',canonical_name='Official card',attributes=dict(
        source_url='https://example.com/checklist',brand='Topps',product='Chrome',
        season_start=2025,season_end=2025,card_number='A-1',player='Example Player',insert='Official Insert'))]
    card=dict(player_name='Example Player',brand_name='Topps',product_name='Chrome',
              season_start=2025,card_number='A-1')
    assert ScopedCardCatalog(entities).enrich(card)[0]['insert_name']=='Official Insert'
    # A vocabulary-only optimization would silently remove this required fact.
    assert 'insert_name' not in ScopedCardCatalog([]).enrich(card)[0]
