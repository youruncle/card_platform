"""Read-only real-source replay and protected-table fingerprints for UD rollout."""
import asyncio
import gzip
import json
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api/src'))
from card_platform.config import Settings
from card_platform.db import Database
from card_platform.knowledge import KnowledgeResolver
from card_platform.valuation_worker import AutomatedValuationWorker

async def main():
    data=ROOT/'data/official-checklists'
    catalog=json.loads((data/'catalog.json').read_text())
    products=[p for p in catalog['products'] if p['publisher']=='Upper Deck' and p['status'] in {'parsed','published'}]
    resolver=KnowledgeResolver({'version_number':0,'entities':[],'official_artifacts':[p['artifact'] for p in products]})
    worker=object.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime=SimpleNamespace(resolver=resolver)
    total=negatives=0; examples=[]
    for p in products:
        assert sha256((data/'raw'/f"{p['key']}.pdf").read_bytes()).hexdigest()==p['checksum']
        blob=(data/'artifacts'/p['artifact']['path']).read_bytes()
        assert sha256(blob).hexdigest()==p['artifact']['checksum']
        cards=json.loads(gzip.decompress(blob));assert len(cards)==p['artifact']['card_count']
        sample=None
        for card in cards:
            a=card['attributes'];assert a['sport']=='basketball' and a['source_checksum']==p['checksum']
            identity=dict(product_name=a['product'],brand_name='UD',season_start=a['season_start'],season_end=a['season_end'],card_number=a['card_number'],player_name=a['player'])
            assert card['id'] in {x['id'] for x in resolver.card_catalog.lookup(identity)}
            after,evidence=resolver.card_catalog.enrich(identity)
            assert 'serial_total' not in after and 'parallel_name' not in after
            if evidence and sample is None: sample=(identity,after,evidence)
            total+=1
        for override in ({'product_name':'__wrong_product__'},{'player_name':'__wrong_player__'},{'card_number':'__wrong_number__'},{'season_start':1900,'season_end':1901},{'brand_name':'Panini'}):
            assert not resolver.card_catalog.lookup(identity|override);negatives+=1
        if sample:
            identity,after,evidence=sample
            actual=worker.resolve_identity_row(identity)
            assert actual['knowledge_identity_evidence']==evidence
            assert actual['insert_name']==after['insert_name']
            queries=[str(x) for x in resolver.build_query_plan(after)]
            assert queries
            examples.append(dict(source_key=p['key'],identity=identity,insert_name=after['insert_name'],evidence=evidence,query_plan=queries))
    assert not resolver.card_catalog.artifact_errors
    db=Database(Settings());await db.open()
    try:
        async with db.connection() as c1, db.connection() as c2:
            r=await c1.execute("SELECT pg_try_advisory_xact_lock(hashtext('card-platform-knowledge-publish')) AS acquired")
            assert (await r.fetchone())['acquired']
            r=await c2.execute("SELECT pg_try_advisory_xact_lock(hashtext('card-platform-knowledge-publish')) AS acquired")
            assert not (await r.fetchone())['acquired']
            protected={}
            for table in ('collection_item','card_definition','card_identity_version','valuation_snapshot'):
                result=await c1.execute('SELECT to_jsonb(t)::text AS row FROM '+table+' t ORDER BY id')
                rows=await result.fetchall();protected[table]=dict(count=len(rows),checksum=sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest())
        result=dict(sources=len(products),facts_checked=total,negative_probes=negatives,worker_examples=examples,artifact_errors=resolver.card_catalog.artifact_errors,database_lock='cross_connection_passed',database_writes=0,protected_tables=protected)
        name=sys.argv[1] if len(sys.argv)>1 else 'upper-deck-verification.json'
        (data/name).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:len(v) if k=='worker_examples' else v for k,v in result.items()}))
    finally:await db.close()

if __name__=='__main__':asyncio.run(main())
