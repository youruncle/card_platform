"""Local full-v26 replay against recorded actually-deployed 35-response contract.

Reads immutable source evidence, writes only this task's derived cache/receipts.
No database, production connection, listening server, or new network requests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import resource
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
OTHER=Path('/Users/song/Documents/Codex/2026-10-28/task')
DEPLOYED=OTHER/'real-validation/atlas-api-deployment-20261030-r2'
OUTPUT=ROOT/'specs/032-atlas-management/verification'
sys.path.insert(0,str(ROOT/'apps/api/src'))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from card_platform.knowledge import snapshot_checksum
from card_platform.knowledge_browser import KnowledgeBrowser
from card_platform.knowledge_browser_api import router,published_browser
from card_platform.knowledge_atlas_api import _summary

p=argparse.ArgumentParser();p.add_argument('--run',choices=('cold','warm'),required=True);args=p.parse_args()
started=time.perf_counter()
baseline=json.loads((DEPLOYED/'post-deployment/current-baseline.json').read_text())
assert snapshot_checksum(baseline['snapshot'])=='82bdd53f184af540ffe6251fadaef4e2752a34cb69ff1e5b497ec305aef06e81'
expected=json.loads((DEPLOYED/'runtime-contract-result.json').read_text())['responses']
cache=ROOT/'.runtime/full-aligned-20261030-final/cache';cache.mkdir(parents=True,exist_ok=True)
cache_existed=bool(list(cache.glob('*.sqlite3')))
if args.run=='cold' and cache_existed:raise RuntimeError('Cold run requires its own initially empty cache; do not relabel a warm replay')
print(json.dumps({'stage':'full_index_start','run':args.run,'cache_existed':cache_existed}),flush=True)
index=KnowledgeBrowser(baseline['snapshot'],version_id=baseline['published']['version_id'],artifact_root=OTHER/'real-validation/input/artifacts',data_root=Path('/Users/song/Documents/Codex/2026-07-19/w/card_platform/data'),cache_root=cache)
index_seconds=time.perf_counter()-started
print(json.dumps({'stage':'full_index_ready','seconds':index_seconds}),flush=True)
app=FastAPI();app.include_router(router,prefix='/v1/knowledge')
async def frozen():return index
app.dependency_overrides[published_browser]=frozen
app.state.knowledge_browser_paths={'data_root':Path('/Users/song/Documents/Codex/2026-07-19/w/card_platform/data')}

def compatible(old,new,path=''):
    if isinstance(old,dict):
        if not isinstance(new,dict):return [path+': type']
        return [error for key,value in old.items() for error in ([path+'/'+key+': missing'] if key not in new else compatible(value,new[key],path+'/'+key))]
    if isinstance(old,list):
        if not isinstance(new,list) or len(old)!=len(new):return [path+': list length/type']
        return [error for i,(a,b) in enumerate(zip(old,new)) for error in compatible(a,b,path+'/'+str(i))]
    return [] if type(old)==type(new) and old==new else [path+': value/type']

calls=[];differences=[];responses={}
with TestClient(app) as client:
    for key,old in expected.items():
        path,param=key.split('?',1);params=json.loads(param)
        t=time.perf_counter();r=client.get('/v1/knowledge/browse'+path,params=params)
        new={'status':r.status_code}
        if r.headers.get('content-type','').startswith('application/json'):new['json']=r.json()
        else:new.update(sha256=hashlib.sha256(r.content).hexdigest(),bytes=len(r.content),content_type=r.headers.get('content-type'))
        errors=compatible(old,new)
        differences.extend({'request':key,'difference':error} for error in errors)
        calls.append({'key':key,'status':r.status_code,'seconds':time.perf_counter()-t,'differences':len(errors)})
        responses[key]=new
        print(json.dumps({'stage':'contract','done':len(calls),'status':r.status_code,'differences':len(errors)}),flush=True)
management=[]
for name,fn in [
    ('summary',lambda:_summary(index)),
    ('confirmed',lambda:index.cards(identity_state='confirmed',limit=10)),
    ('pending',lambda:index.cards(identity_state='pending',limit=10)),
    ('conflict',lambda:index.cards(identity_state='conflict',limit=10)),
    ('both_images',lambda:index.cards(image_state='both',limit=10)),
    ('single_image',lambda:index.cards(image_state='single',limit=10)),
    ('no_images',lambda:index.cards(image_state='none',limit=10)),
    ('exact_player_card',lambda:index.cards(product_id='2a09f03b-10da-403d-b48a-52dd6bd310ce',player='Hansen Yang',card_number='14',limit=100)),
]:
    print(json.dumps({'stage':'management_start','name':name}),flush=True)
    t=time.perf_counter();value=fn();management.append({'name':name,'seconds':time.perf_counter()-t,'total':value.get('total'),'summary':value if name=='summary' else None})
summary=index.summary()
result={'run':args.run,'platform':platform.platform(),'cache_existed_before':cache_existed,'snapshot_checksum':snapshot_checksum(baseline['snapshot']),'version_id':baseline['published']['version_id'],'workspace_revision':baseline['workspace_revision'],'index_seconds':index_seconds,'total_seconds':time.perf_counter()-started,'max_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'cache_bytes':sum(f.stat().st_size for f in cache.glob('*.sqlite3')),'summary':summary,'calls':calls,'contract_cases':len(calls),'differences':differences,'management':management,'production_writes':0,'network_requests':0}
(OUTPUT/f'full-aligned-{args.run}-metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
(OUTPUT/f'full-aligned-{args.run}-responses.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in {'summary','calls','management'}},indent=2),flush=True)
if differences:raise SystemExit('Recorded deployed contract mismatch')
assert summary['summary']=={'manufacturers':3,'products':565,'card_facts':2169016,'images':831,'paired_cards':14},summary['summary']
assert not summary['issues'],summary['issues']
