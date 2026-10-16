#!/usr/bin/env python3
"""Write explicit Codex image reviews only; default dry-run, no models or prices."""
from __future__ import annotations
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from uuid import UUID, uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'apps/api/src'),str(ROOT)]
from psycopg.types.json import Jsonb
from card_platform.comparable_image_verifier import (ComparableImageVerifier, VisualCandidate,
    visual_asset_binding, bound_reviewed_visual_decision, normalize_model_visual_decision)
from card_platform.config import Settings
from card_platform.db import Database
from card_platform.domain.independent_finish import IndependentFinishObservation
from card_platform.domain.reviewed_visual import REVIEWED_FINISH_CONTRACT, review_identity_hash
from card_platform.knowledge_runtime import KnowledgeRuntime
from card_platform.valuation_trace import content_hash
from card_platform.valuation_worker import AutomatedValuationWorker
from card_platform.visual_checkpoints import VisualCheckpoints

MANIFEST_CONTRACT='bound-codex-visual-review-batch-v1'


def plain(value):return json.loads(json.dumps(value,default=str,ensure_ascii=False))
def digest(value):return hashlib.sha256(json.dumps(plain(value),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def decision_hash(row):return digest(row) if row is not None else 'absent'
def evidence_hash(row):
    return digest({key:value for key,value in row.items() if key not in {'fetched_at','first_seen_at','last_seen_at','created_at'}})


def save_private(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise ValueError('audit output may not be a symlink')
    with tempfile.NamedTemporaryFile(dir=path.parent,mode='w',delete=False) as stream:
        temporary=Path(stream.name);os.chmod(temporary,0o600)
        json.dump(payload,stream,ensure_ascii=False,indent=2,default=str);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def validate_review(identity,evidence,entry,front,back,candidate_bytes):
    """Validate supplied observations; never synthesize an accepted review or sealed proof."""
    for key,current in (('collection_item_id',identity.get('collection_item_id')),('owner_id',identity.get('owner_id')),
                        ('identity_version_id',identity.get('confirmed_identity_version_id')),('market_evidence_id',evidence.get('id'))):
        if not current or str(entry.get(key))!=str(current):raise ValueError(f'current {key} mismatch')
    if entry.get('reviewed_by')!='codex' or not entry.get('reviewed_at'):raise ValueError('explicit Codex review metadata required')
    reviewed_at=datetime.fromisoformat(entry['reviewed_at'].replace('Z','+00:00'))
    if reviewed_at.tzinfo is None:raise ValueError('review timestamp must be timezone aware')
    if entry.get('evidence_sha256')!=evidence_hash(evidence):raise ValueError('market evidence changed')
    image_url=(evidence.get('raw_payload') or {}).get('image_url')
    if not image_url or (entry.get('source_title'),entry.get('source_url'),entry.get('candidate_image_url')) != (evidence['raw_title'],evidence['source_url'],image_url):
        raise ValueError('source title/url/original image URL mismatch')
    candidate=VisualCandidate(evidence['source_url'],evidence['raw_title'],image_url)
    binding=visual_asset_binding(identity,candidate,front,back,candidate_bytes)
    review=entry.get('visual_review') or {}
    if (review.get('source')!='codex_paired_image_review' or review.get('review_contract')!=REVIEWED_FINISH_CONTRACT
            or review.get('full_identity_sha256')!=review_identity_hash(identity)):
        raise ValueError('Codex review contract or current identity mismatch')
    decision=bound_reviewed_visual_decision(identity,candidate,review,current_binding=binding)
    if decision is None or normalize_model_visual_decision(decision).verdict!='same':
        raise ValueError('specific paired front layout and finish evidence required')
    observation=IndependentFinishObservation.model_validate(review.get('paired_front_observation'))
    if not observation.establishes_same():raise ValueError('specific artwork and fixed finish observations required')
    return plain(review)


def plan_decision(existing,entry,audit):
    review=plain(entry['visual_review'])
    if existing and existing['decision']=='rejected':raise ValueError('existing rejected decision is preserved')
    if existing and existing['decision']=='accepted' and (existing.get('classification') or {}).get('visual_review')==review:
        return {'action':'already_applied','before':plain(existing),'after':plain(existing)}
    if entry.get('expected_decision_sha256')!=decision_hash(existing):raise ValueError('decision preimage changed')
    if existing and existing['decision'] not in {'accepted','needs_review'}:raise ValueError('unsupported previous decision')
    after=plain(existing) if existing else {'id':str(uuid4()),'collection_item_id':entry['collection_item_id'],
        'identity_version_id':entry['identity_version_id'],'market_evidence_id':entry['market_evidence_id'],
        'classification':{},'reason_codes':[]}
    after['decision']='accepted'
    after['classification']={**(after.get('classification') or {}),'visual_review':review,
        'bound_visual_review_audit':{**audit,'review_sha256':digest(review),'reviewed_by':'codex','reviewed_at':entry['reviewed_at']}}
    after['reason_codes']=list(dict.fromkeys([*(after.get('reason_codes') or []),f"codex_bound_visual_review:{audit['audit_id']}"]))
    return {'action':'update' if existing else 'insert','before':plain(existing),'after':after}


class BoundDatabase:
    def __init__(self,connection):self.value=connection
    @asynccontextmanager
    async def connection(self):yield self.value


def local_assets(identity,entry,uploads,review_root):
    contents={};uploads=uploads.resolve();review_root=review_root.resolve()
    for side in ('front','back'):
        url=identity.get(f'image_{side}_url')
        if not url:
            if side=='front':raise ValueError('target front required')
            contents[side]=None;continue
        if not url.startswith('/uploads/'):raise ValueError('target is not local upload')
        path=(uploads/url.removeprefix('/uploads/')).resolve()
        if not path.is_relative_to(uploads) or not path.is_file():raise ValueError('target path invalid')
        content=path.read_bytes()
        if not content or len(content)>ComparableImageVerifier.MAX_IMAGE_BYTES:raise ValueError('target original invalid')
        contents[side]=content
    declared=entry.get('candidate_original') or {};path=Path(declared.get('path') or '')
    if not path.is_absolute():path=review_root/path
    path=path.resolve()
    if not path.is_relative_to(review_root) or not path.is_file():raise ValueError('candidate original escapes review root or missing')
    original=path.read_bytes()
    if not original or len(original)>ComparableImageVerifier.MAX_IMAGE_BYTES or content_hash(original)!=declared.get('sha256'):
        raise ValueError('reviewed original candidate SHA mismatch')
    key=content_hash({key:str(identity.get(key) or '') for key in ('owner_id','collection_item_id')} | {'image_url':entry['candidate_image_url']})
    cached=VisualCheckpoints(uploads).image(key,ComparableImageVerifier.MAX_IMAGE_BYTES)
    if cached is None or cached[0]!=original:raise ValueError('current full original checkpoint missing, expired or changed')
    if 'source_back_identity' in (entry.get('visual_review') or {}):
        validate_source_back_assets(identity,entry,uploads,review_root)
    return contents['front'],contents['back'],original


def validate_source_back_assets(identity,entry,uploads,review_root):
    from card_platform.domain.reviewed_source_back import source_back_observation_matches
    from card_platform.ingestion.cardhobby_public_item import parse_public_item_html, canonical_public_item_url
    import cv2
    import numpy as np

    def original(name,limit):
        declared=entry.get(name) or {};path=Path(declared.get('path') or '')
        if not path.is_absolute():path=review_root/path
        path=path.resolve()
        if not path.is_relative_to(review_root) or not path.is_file() or path.stat().st_size>limit:
            raise ValueError('source back/gallery original missing or outside review root')
        data=path.read_bytes()
        if not data or content_hash(data)!=declared.get('sha256'):
            raise ValueError('source back/gallery original SHA mismatch')
        return data

    observation=entry['visual_review']['source_back_identity']
    back=original('source_back_original',ComparableImageVerifier.MAX_IMAGE_BYTES)
    html=original('source_gallery',10*1024*1024)
    gallery=entry['source_gallery']
    if (not source_back_observation_matches(observation,identity,entry['source_url'],entry['candidate_image_url'],content_hash(back))
            or content_hash(html)!=observation['gallery_html_sha256']
            or canonical_public_item_url(gallery.get('source_url'))!=canonical_public_item_url(entry['source_url'])):
        raise ValueError('source back observation does not match current identity/gallery')
    parsed=parse_public_item_html(html.decode('utf-8'),entry['source_url'])
    metadata=parsed.get('source_metadata') or (parsed.get('sale') or {}).get('source_metadata') or {}
    if ('public_item_id_mismatch' in parsed.get('reasons',[])
            or metadata.get('image_urls')!=observation['gallery_image_urls']):
        raise ValueError('back/front originals do not belong to archived source gallery')
    key=content_hash({key:str(identity.get(key) or '') for key in ('owner_id','collection_item_id')}
                     | {'image_url':observation['image_url']})
    cached=VisualCheckpoints(uploads).image(key,ComparableImageVerifier.MAX_IMAGE_BYTES)
    if cached is None or cached[0]!=back:
        raise ValueError('current source back checkpoint missing, expired or changed')
    if cv2.imdecode(np.frombuffer(back,dtype=np.uint8),cv2.IMREAD_COLOR) is None:
        raise ValueError('source back original is not a readable image')


async def load_current_identity(connection,job_id,uploads,execute):
    if execute:
        await connection.execute("SELECT published_version_id FROM knowledge_workspace WHERE name='Card Platform' FOR SHARE")
    database=BoundDatabase(connection)
    runtime=KnowledgeRuntime(database);await runtime.refresh()
    worker=AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.database=database;worker.knowledge_runtime=runtime
    verifier=ComparableImageVerifier.__new__(ComparableImageVerifier)
    verifier.upload_dirs=(uploads.resolve(),);worker.image_verifier=verifier
    return await worker._load_identity(job_id)


async def prepare_locked(connection,entry,uploads,review_root,execute,audit):
    item_id=UUID(str(entry['collection_item_id']));version_id=UUID(str(entry['identity_version_id']));source_id=UUID(str(entry['market_evidence_id']))
    lock=' FOR UPDATE' if execute else ''
    item=await(await connection.execute('SELECT * FROM collection_item WHERE id=%s'+lock,(item_id,))).fetchone()
    if not item or str(item['owner_id'])!=str(entry['owner_id']):raise ValueError('current owner/item mismatch')
    if item['deleted_at'] is not None or item['holding_status']!='held':raise ValueError('item no longer held')
    if execute:
        # CI row lock blocks new FK-linked jobs/versions; existing rows also locked.
        await connection.execute('SELECT id FROM card_identity_version WHERE collection_item_id=%s ORDER BY id FOR SHARE',(item_id,))
        await connection.execute('SELECT id FROM valuation_job WHERE collection_item_id=%s ORDER BY id FOR UPDATE',(item_id,))
    if await(await connection.execute("SELECT 1 FROM valuation_job WHERE collection_item_id=%s AND status IN ('queued','identifying','searching','calculating') LIMIT 1",(item_id,))).fetchone():
        raise ValueError('active valuation job prevents review write')
    job=await(await connection.execute('SELECT id FROM valuation_job WHERE collection_item_id=%s ORDER BY created_at DESC LIMIT 1',(item_id,))).fetchone()
    if not job:raise ValueError('existing identity-loading job required; writer creates no jobs')
    identity=await load_current_identity(connection,job['id'],uploads,execute)
    source=await(await connection.execute('SELECT * FROM market_evidence WHERE id=%s'+(' FOR SHARE' if execute else ''),(source_id,))).fetchone()
    if not source:raise ValueError('existing market evidence row required')
    existing=await(await connection.execute('SELECT * FROM market_evidence_identity_decision WHERE identity_version_id=%s AND market_evidence_id=%s'+lock,(version_id,source_id))).fetchone()
    if existing and str(existing['collection_item_id'])!=str(item_id):raise ValueError('decision belongs to another item')
    front,back,candidate=local_assets(identity,entry,uploads,review_root)
    validate_review(identity,dict(source),entry,front,back,candidate)
    change=plan_decision(dict(existing) if existing else None,entry,audit)
    return {**change,'collection_item_id':str(item_id),'market_evidence_id':str(source_id),
            'identity_version_id':str(version_id),'source_url':source['source_url'],
            'source_sha256':evidence_hash(dict(source))}


async def apply_batch(connection,manifest,*,uploads,review_root,execute,backup_path,manifest_path):
    if manifest.get('contract')!=MANIFEST_CONTRACT or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',manifest.get('audit_id','')):
        raise ValueError('explicit batch contract and audit_id required')
    entries=manifest.get('items') or []
    if not entries:raise ValueError('no explicit reviewed items')
    keys=[(str(e['identity_version_id']),str(e['market_evidence_id'])) for e in entries]
    if len(set(keys))!=len(keys):raise ValueError('duplicate review target in batch')
    audit={'audit_id':manifest['audit_id'],'manifest_sha256':digest(manifest),'manifest_path':str(manifest_path.resolve())}
    prepared=[]
    current_entry=None
    try:
        async with connection.transaction():
            await connection.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            if not execute:await connection.execute('SET TRANSACTION READ ONLY')
            if execute:await connection.execute("SET LOCAL lock_timeout = '5s'")
            for entry in sorted(entries,key=lambda e:(str(e['collection_item_id']),str(e['market_evidence_id']))):
                current_entry=entry
                prepared.append(await prepare_locked(connection,entry,uploads,review_root,execute,audit))
            # Durable preimage is written before any statement mutates a decision.
            if execute:save_private(backup_path,{'audit':audit,'prepared_at':datetime.now(timezone.utc).isoformat(),'changes':prepared})
            for change in prepared:
                if not execute or change['action']=='already_applied':continue
                current_entry=change
                row=change['after']
                if change['action']=='insert':
                    result=await connection.execute('''INSERT INTO market_evidence_identity_decision
                        (id,collection_item_id,identity_version_id,market_evidence_id,decision,reason_codes,classification)
                        VALUES (%s,%s,%s,%s,'accepted',%s,%s)''',
                        (row['id'],row['collection_item_id'],row['identity_version_id'],row['market_evidence_id'],Jsonb(row['reason_codes']),Jsonb(row['classification'])))
                else:
                    result=await connection.execute('''UPDATE market_evidence_identity_decision SET decision='accepted',reason_codes=%s,classification=%s
                        WHERE id=%s AND collection_item_id=%s AND identity_version_id=%s AND market_evidence_id=%s AND decision<>'rejected' ''',
                        (Jsonb(row['reason_codes']),Jsonb(row['classification']),row['id'],row['collection_item_id'],row['identity_version_id'],row['market_evidence_id']))
                if result.rowcount!=1:raise RuntimeError('decision write lost guarded row; batch rolled back')
    except BaseException as error:
        error.review_items=[{**{key:entry.get(key) for key in
            ('collection_item_id','identity_version_id','market_evidence_id')},
            'status':'blocked' if current_entry and all(str(entry.get(key))==str(current_entry.get(key)) for key in
                ('collection_item_id','identity_version_id','market_evidence_id')) else 'rolled_back'} for entry in entries]
        raise
    return {'mode':'executed' if execute else 'dry_run','audit':audit,'items':[
        {key:value for key,value in change.items() if key not in {'before','after'}} for change in prepared]}


async def run(args):
    manifest=json.loads(args.manifest.read_text());settings=Settings(_env_file=args.env_file,upload_dir=str(args.uploads),valuation_worker_enabled=False)
    database=Database(settings);await database.open()
    backup=args.output.with_name(args.output.stem+'-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
    committed=False
    try:
        async with database.connection() as connection:
            receipt=await apply_batch(connection,manifest,uploads=args.uploads,review_root=args.review_root or args.manifest.parent,
                execute=args.execute,backup_path=backup,manifest_path=args.manifest)
        committed=args.execute
        receipt['backup_path']=str(backup) if args.execute else None
        save_private(args.output,receipt);print(json.dumps(receipt,ensure_ascii=False))
    except BaseException as error:
        save_private(args.output,{'mode':('executed_receipt_failed' if committed else 'rolled_back') if args.execute else 'dry_run_failed','error_type':type(error).__name__,
            'reason':str(error)[:300],'items':getattr(error,'review_items',[]),'backup_path':str(backup) if backup.exists() else None,'manifest_sha256':digest(manifest),'published_value_unchanged':True})
        raise
    finally:await database.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('manifest',type=Path)
    parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--uploads',type=Path,required=True)
    parser.add_argument('--review-root',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execute',action='store_true');asyncio.run(run(parser.parse_args()))
