"""LOCAL ONLY QA harness. Session override and test DB never enter product code."""
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4,UUID
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import FastAPI
from card_platform.auth import current_user
from card_platform.knowledge_api import router

ROOT=Path(__file__).resolve().parents[3]
TASK=Path('/Users/song/Documents/Codex/2026-10-28/task')
DSN='postgresql://atlas_test@/postgres?host=/tmp/card-atlas-pg-socket&port=55569'
schema='atlas_qa_'+uuid4().hex
actor=uuid4()
baseline=json.loads((TASK/'real-validation/cross-product-02/current-baseline.json').read_text())
source=baseline['snapshot']
product=deepcopy(next(e for e in source['entities'] if e.get('entity_type')=='product' and e.get('attributes',{}).get('official_artifact',{}).get('source_key')=='panini-api-2025-1533'))
snapshot={'version':source['version'],'entities':[product],'relations':[],'official_artifacts':[product['attributes']['official_artifact']]}
version=UUID(baseline['published']['version_id'])
with psycopg.connect(DSN,autocommit=True) as c:
    c.execute(f'CREATE SCHEMA {schema}');c.execute(f'SET search_path TO {schema},public')
    c.execute('CREATE TABLE app_user(id uuid PRIMARY KEY)')
    c.execute((ROOT/'db/migrations/0015_card_knowledge_base.sql').read_text())
    c.execute('CREATE TABLE knowledge_source(workspace_id uuid,source_key text,source_type text)')
    c.execute('CREATE TABLE market_comp(id uuid PRIMARY KEY,listing_title text)')
    c.execute('CREATE TABLE card_definition(id uuid PRIMARY KEY,insert_name text,parallel_name text,card_number text)')
    c.execute('CREATE TABLE collection_item(id uuid PRIMARY KEY,card_definition_id uuid)')
    c.execute('INSERT INTO app_user VALUES(%s)',(actor,))
    workspace=c.execute('SELECT id FROM knowledge_workspace').fetchone()[0]
    c.execute("INSERT INTO knowledge_member VALUES(%s,%s,'admin',now())",(workspace,actor))
    c.execute('INSERT INTO knowledge_version(id,workspace_id,version_number,snapshot,checksum,published_by) VALUES(%s,%s,%s,%s,%s,%s)',(version,workspace,snapshot['version'],Jsonb(snapshot),'historical-local-qa',actor))
    c.execute('UPDATE knowledge_workspace SET published_version_id=%s',(version,))
    c.execute("INSERT INTO knowledge_entity(id,workspace_id,entity_type,canonical_name,normalized_name,attributes,review_status,created_by) VALUES(%s,%s,'product',%s,%s,%s,'approved',%s)",(UUID(product['id']),workspace,product['canonical_name'],product['canonical_name'].lower(),Jsonb(product['attributes']),actor))

@asynccontextmanager
async def connection():
    async with await psycopg.AsyncConnection.connect(DSN,row_factory=dict_row) as c:
        await c.execute(f'SET search_path TO {schema},public')
        yield c

@asynccontextmanager
async def lifespan(app):
    yield
    with psycopg.connect(DSN,autocommit=True) as c:c.execute(f'DROP SCHEMA {schema} CASCADE')

app=FastAPI(lifespan=lifespan);app.include_router(router)
app.state.database=SimpleNamespace(connection=connection)
app.state.knowledge_browser_paths={'data_root':Path('/Users/song/Documents/Codex/2026-07-19/w/card_platform/data'),'artifact_root':TASK/'real-validation/input/artifacts','cache_root':ROOT/'.runtime/atlas-qa/cache'}
app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=actor,role='admin')
@ app.get('/qa/info')
async def info():return {'schema':schema,'actor':str(actor),'product_id':product['id'],'snapshot':'historical v26, one product; no production connection'}
