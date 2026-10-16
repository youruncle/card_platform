#!/usr/bin/env python3
"""Resume the 30-year catalog and publish immutable official fact attachments."""
import argparse
import asyncio
import fcntl
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlencode
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api/src'))
from card_platform.official_checklists import build_manifest, parse_document, product_fields, progress_summary, window_status, parse_mercury_pages, parse_teamless_pages, isolate_conflicting_rows, publisher_progress, parse_upper_deck_epack, parse_upper_deck_html, official_url, PARSER_VERSION as BASE_PARSER_VERSION
from sync_official_checklists import write_json

DATA = ROOT / 'data/official-checklists'
START, END = '1996-10-04', '2026-10-04'
PARSER_VERSION = BASE_PARSER_VERSION + '-history-conflicts-v1-ud-v1'


def save(catalog):
    products = catalog['products']
    summary = progress_summary(products, discovery_complete=False)
    write_json(DATA / 'catalog.json', catalog)
    eligible=[p for p in products if p.get('window_status')!='outside' and p.get('status')!='excluded']
    total=len(eligible) or 1
    phase_progress=dict(
        inventory_percent=None,
        downloaded_percent=round(100*sum((DATA/'raw'/f"{p['key']}.pdf").exists() for p in eligible)/total,1),
        parsed_percent=round(100*sum(p.get('status') in {'parsed','published'} for p in eligible)/total,1),
        published_percent=summary['known_success_percent'])
    phase_progress['known_pipeline_percent']=round(sum(phase_progress[k] for k in ('downloaded_percent','parsed_percent','published_percent'))/3,1)
    write_json(DATA / 'status.json', dict(updated_at=datetime.now(timezone.utc).isoformat(), sport='basketball',
        window=dict(start=START,end=END), engineering_percent=catalog.get('engineering_percent',90),
        summary=summary, publishers=publisher_progress(products), phase_progress=phase_progress, known_products_percent=summary['known_success_percent'],
        source_blockers=catalog['source_blockers'], frozen_inventory=catalog.get('frozen_inventory'), products=products))


def validated_sport_evidence(product, catalog):
    from card_platform.knowledge_card_catalog import text_key
    sources = {p['key']:p for p in catalog['products']}
    evidence = product.get('row_sport_evidence', [])
    for proof in evidence:
        source = sources.get(proof['source_key'])
        if not source or source['source_url'] != proof['source_url'] or not official_url(proof['source_url']):
            raise ValueError('Unverified sport evidence source')
        raw = DATA/'raw'/f"{source['key']}.pdf"
        if sha256(raw.read_bytes()).hexdigest() != proof['source_checksum']:
            raise ValueError('Sport evidence checksum mismatch')
        # Reparse without supplemental evidence: provenance must end at an
        # explicitly classified official row, never a chain of inferred facts.
        rows, _, _ = parse_document(raw)
        if not any(r['source_page']==proof['source_page'] and r['source_line']==proof['source_line']
                   and r['raw_text']==proof['raw_text'] and text_key(r['player'])==text_key(proof['player']) for r in rows):
            raise ValueError('Sport evidence row or player mismatch')
    return evidence


def prepare():
    catalog = json.loads((DATA/'catalog.json').read_text())
    if (DATA/'topps-history.json').exists():
        known = {p['key'] for p in catalog['products']}
        catalog['products'].extend(p for p in json.loads((DATA/'topps-history.json').read_text()) if p['key'] not in known)
    for supplemental in ('upper-deck-history.json','upper-deck-epack-history.json','upper-deck-scope-evidence.json'):
        path = DATA/supplemental
        if path.exists():
            existing = {p['key']:p for p in catalog['products']}
            for p in json.loads(path.read_text()):
                if p['key'] not in existing:
                    catalog['products'].append(p)
                elif existing[p['key']].get('status') != 'published':
                    existing[p['key']].update(p)
                    if p.get('status') != 'excluded':
                        existing[p['key']].pop('exclusion_reason',None)
                        existing[p['key']].pop('scope_evidence',None)
    report = json.loads((DATA/'panini/progress.json').read_text())
    existing = {p['key']:p for p in catalog['products']}
    for item in report['products']:
        key = f"panini-api-{item['archive_year']}-{item['id']}"
        if key not in existing:
            season = re.search(r'\((\d{2})-(\d{2})\)', item['name'])
            year = f"20{season[1]}-{season[2]}" if season else item['archive_year']
            name = re.sub(r'\s*\(\d{2}-\d{2}\)', '',item['name'])
            p = dict(key=key,title=f'{year} Panini {name} Basketball',publisher='Panini',
                directory_url='https://www.paniniamerica.net/checklist.html',
                source_url='https://support.paniniamerica.net/get-product-checklist?'+urlencode(dict(sport='Basketball',year=item['archive_year'],brand=item['brand'],program=item['name'])),
                release_date=item.get('release_date'),date_basis='official_directory_api_release_date',status='pending',api_program_id=item['id'])
            catalog['products'].append(p); existing[key]=p
        p = existing[key]
        if p.get('status') != 'published':
            season = re.search(r'\((\d{2})-(\d{2})\)', item['name'])
            year = f"20{season[1]}-{season[2]}" if season else item['archive_year']
            name = re.sub(r'\s*\(\d{2}-\d{2}\)', '',item['name'])
            title = f'{year} Panini {name} Basketball'
            if p['title'] != title:
                p.pop('artifact',None)
                p['title'] = title
        p['api_brand'] = item['brand']
        p['window_status'] = window_status(p.get('release_date'),START,END)
        if p['window_status']=='outside':
            p['status']='excluded'; continue
        if item.get('status')=='downloaded':
            target=DATA/'raw'/f'{key}.pdf'
            if not target.exists(): shutil.copy2(DATA/'panini'/item['file'],target)
        elif p.get('status')!='published':
            p['status']=item.get('status','pending')
            if item.get('error'): p['error']=item['error']
    catalog['source_blockers']=[
        'Panini公开目录2009–2026共18个年份已扫描；1996-10-04至2008档案未提供，不能宣称30年穷尽。',
        'Topps当前官方目录未提供1996–2019篮球历史清单档案；发行日期未知单列，赛季不作为发行日期。',
        'Upper Deck现代官网主要为2015年起档案，e-Pack另有篮球目录；1996–2014完整官方目录尚不可获取，未用第三方替代。',
        '下载、解析及发布成功率仅针对已发现候选；隔离原文保留，不计为成功卡片事实。']
    catalog['panini_discovery']=dict(complete=report['directory_complete'],archive_years=len(report['archive_years']),products=len(report['products']))
    catalog['window']=dict(start=START,end=END)
    catalog.setdefault('engineering_percent',90)
    save(catalog)
    for index,p in enumerate(catalog['products']):
        p['window_status']=window_status(p.get('release_date'),START,END)
        if p['window_status']=='outside' or p.get('status') in {'published','excluded'}: continue
        raw=DATA/'raw'/f"{p['key']}.pdf"
        if not raw.exists(): continue
        try:
            checksum=sha256(raw.read_bytes()).hexdigest()
            if p.get('publisher') == 'Upper Deck':
                p.setdefault('document_format', 'upper_deck_html')
                p.setdefault('retrieval_format', 'official_http_response')
                p.setdefault('ownership_evidence', dict(url=p['source_url'], basis='Upper Deck official checklist publication'))
            sport_evidence = validated_sport_evidence(p,catalog) if p.get('row_sport_evidence') else []
            evidence_checksum = sha256(json.dumps(sport_evidence,sort_keys=True).encode()).hexdigest()
            if p.get('artifact') and p.get('checksum')==checksum and p.get('parser_version')==PARSER_VERSION and p.get('evidence_checksum')==evidence_checksum:
                p['status']='parsed'; p.pop('error',None); continue
            p['checksum']=checksum
            p.setdefault('retrieved_at',datetime.now(timezone.utc).isoformat())
            if p.get('publisher') == 'Upper Deck' and not product_fields(p['title'])['season_start']:
                raise ValueError('Product season unknown; no inference from archive date')
            if p.get('document_format') == 'upper_deck_epack':
                rows,issues,pages=parse_upper_deck_epack(json.loads(raw.read_text()),p['official_product_title'])
            elif p.get('row_sport_evidence'):
                rows,issues,pages=parse_upper_deck_html(raw.read_text(),row_sport_evidence=sport_evidence)
            else:
                rows,issues,pages=parse_document(raw)
            if p['title'] in {'2021-22 Topps Bowman University Basketball','2021 Topps Finest Basketball'}:
                rows,issues=parse_teamless_pages(pages)
            if p['title']=='2023-24 Topps Mercury Victor Wembanyama Basketball':
                rows,issues=parse_mercury_pages(pages)
            rows,conflicts=isolate_conflicting_rows(rows)
            issues.extend(conflicts)
            write_json(DATA/'parsed'/f"{p['key']}.json",dict(rows=rows,issues=issues,pages=pages,checksum=checksum,parser_version=PARSER_VERSION))
            p.update(row_count=len(rows),issue_count=len(issues),parser_version=PARSER_VERSION,evidence_checksum=evidence_checksum)
            if not rows: raise ValueError('No structured rows; raw retained and isolated')
            manifest=build_manifest(p,rows)
            cards=[dict(id='official:'+p['key']+':'+e['key'],entity_type='card',canonical_name=e['canonical_name'],attributes=e['attributes']) for e in manifest['entities'] if e['entity_type']=='card']
            blob=gzip.compress(json.dumps(cards,ensure_ascii=False,separators=(',',':')).encode(),mtime=0)
            digest=sha256(blob).hexdigest()
            path=DATA/'artifacts'/f'{digest}.json.gz';path.parent.mkdir(exist_ok=True)
            if not path.exists(): path.write_bytes(blob)
            p['artifact']=dict(source_key=p['key'],path=path.name,checksum=digest,card_count=len(cards),**product_fields(p['title']))
            # Product source/evidence stays in existing knowledge tables. Cards are
            # immutable version attachments to avoid copying millions of rows per version.
            manifest['entities']=[e for e in manifest['entities'] if e['entity_type']=='product']
            manifest['entities'][0]['attributes']['official_artifact']=p['artifact']
            write_json(DATA/'manifests'/f"{p['key']}.json",manifest)
            p.update(status='parsed');p.pop('error',None)
        except Exception as exc: p.update(status='failed',error=str(exc))
        save(catalog)
        print(f"Parse {index+1}/{len(catalog['products'])} {p['title']}: {p['status']}",flush=True)
    save(catalog)
    return catalog


async def publish(catalog, deploy_root):
    from card_platform.config import Settings
    from card_platform.db import Database
    from card_platform.knowledge import normalize_knowledge_text
    from card_platform.knowledge_repository import KnowledgeRepository
    from card_platform.knowledge_batch import apply_manifest
    from card_platform.knowledge_manifest import validate_manifest
    db=Database(Settings());await db.open()
    repo=KnowledgeRepository()
    applied=[]
    try:
        async with db.connection() as c:
            lock=await c.execute("SELECT pg_try_advisory_xact_lock(hashtext('card-platform-knowledge-publish')) AS acquired")
            if not (await lock.fetchone())['acquired']: raise RuntimeError('Another knowledge publication/sync holds the database lock')
            w=await repo.workspace(c)
            q=await c.execute('SELECT snapshot FROM knowledge_version WHERE id=%s',(w['published_version_id'],))
            previous=(await q.fetchone())['snapshot']
            refs={r['source_key']:r for r in previous.get('official_artifacts',[])}
            q=await c.execute("SELECT user_id FROM knowledge_member WHERE workspace_id=%s AND role='admin' ORDER BY user_id LIMIT 1",(w['id'],))
            actor=(await q.fetchone())['user_id']
            for p in catalog['products']:
                if p.get('status') not in {'parsed','published'} or not p.get('artifact'): continue
                ref=p['artifact'];local=DATA/'artifacts'/ref['path']
                if sha256(local.read_bytes()).hexdigest()!=ref['checksum']: raise ValueError('Artifact corrupted: '+p['key'])
                if deploy_root:
                    target=deploy_root/'data/official-checklists/artifacts'/ref['path'];target.parent.mkdir(parents=True,exist_ok=True)
                    if not target.exists(): shutil.copy2(local,target)
                    if sha256(target.read_bytes()).hexdigest()!=ref['checksum']: raise ValueError('Deployment checksum mismatch')
                if refs.get(p['key'])==ref:
                    p.update(status='published',knowledge_version=w['published_version']);continue
                manifest=json.loads((DATA/'manifests'/f"{p['key']}.json").read_text())
                names=[normalize_knowledge_text(e['canonical_name']) for e in manifest['entities']]
                conflict=await c.execute("SELECT canonical_name FROM knowledge_entity WHERE workspace_id=%s AND entity_type='product' AND normalized_name=ANY(%s) AND (review_status <> 'approved' OR source->>'source_key' IS DISTINCT FROM %s)", (w['id'],names,p['key']))
                collisions=await conflict.fetchall()
                if collisions:
                    # Keep existing products/drafts untouched; this source's scoped
                    # cards remain independently published in the attachment.
                    manifest['entities']=[]
                manifest['sources'][0]['metadata']['official_artifact']=ref
                report=validate_manifest(manifest)
                if not report.publishable: p.update(status='failed',error=str(report.conflicts));continue
                async with c.transaction():
                    batch=await apply_manifest(c,w['id'],actor,manifest,report,dry_run=False)
                    if batch['status']=='failed': raise ValueError(str(batch['conflict_report']))
                refs[p['key']]=ref;applied.append(p['key'])
            if applied:
                w=await repo.workspace(c)
                version=await repo.publish(c,w,actor,w['revision'],'Automatic 30-year official basketball history; immutable scoped fact attachments; unknown dates isolated.',None,official_source_keys=applied,official_artifacts=list(refs.values()))
                number=version['version_number']
            else: number=w['published_version']
        for p in catalog['products']:
            if p['key'] in applied: p.update(status='published',knowledge_version=number)
        save(catalog)
        print(json.dumps(dict(version=number,new_sources=len(applied),summary=progress_summary(catalog['products'],discovery_complete=False))))
    finally: await db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--deploy-root',type=Path)
    args=parser.parse_args()
    with (DATA/'catalog.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        catalog=prepare()
        if args.apply: asyncio.run(publish(catalog,args.deploy_root))
