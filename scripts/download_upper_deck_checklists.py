"""Resume official Upper Deck HTML checklists; only basketball facts are parsed."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
from html import unescape
import json, re, sys, time
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api/src'))
from card_platform.official_checklists import parse_upper_deck_html
DATA=ROOT/'data/official-checklists'; CACHE=DATA/'upper-deck'
SEEDS=[f'{y}-goodwin-champions-checklist' for y in (2016,2017,2018,2019,2020,2021,2022,2024,2025,2026)] + [
 '2021-metal-universe-champions-checklist',
 '2022-23-metal-universe-champions-checklist',
 '2023-24-metal-universe-champions-checklist',
 '2024-25-skybox-metal-universe-champions-checklist',
 '2026-metal-universe-se','2026-upper-deck-nscc-checklist','2025-allure-se-checklist',
 '2025-nscc-checklist','2024-25-gr8-moments-checklist',
 'world-of-sports-volume-1-checklist','upper-deck-world-of-sports-volume-ii-checklist']
# Exact links verified in the official Skybox Metal Universe Champions directory.
# Keep the original candidate key so recovery cannot create duplicate sources.
CANONICAL_SLUGS={
    '2022-23-metal-universe-champions-checklist':'2022-metal-universe-champions',
    '2024-25-skybox-metal-universe-champions-checklist':'2024-skybox-metal-universe-champions-checklist',
}

def write(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(path)

def validate_html(text):
    if '[Truncated]' in text or text.count('<table') != text.count('</table>') or text.count('<tr') != text.count('</tr>'):
        raise ValueError('Incomplete official HTML table archive')
    if 'class="csv-table"' not in text or not re.search(r'<h1\b',text):
        raise ValueError('Missing official title or structured checklist')


def load_checklist(client,url,target,*,pause):
    if target.exists():
        cached=target.read_text()
        try:
            validate_html(cached)
            return cached
        except ValueError:
            digest=sha256(target.read_bytes()).hexdigest()[:12]
            target.rename(target.with_name(target.name+'.invalid-'+digest))
    for attempt in range(3):
        pause()
        try:
            response=client.get(url);response.raise_for_status()
            if response.url.host not in {'upperdeck.com','www.upperdeck.com'}:
                raise ValueError('Non-official redirect')
            validate_html(response.text)
            temporary=target.with_suffix('.download')
            temporary.write_text(response.text);temporary.replace(target)
            return response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401,404} or attempt==2:raise
        except httpx.TransportError:
            if attempt==2:raise


def exclusion_reason(html):
    # A license can cover only part of a multi-sport product (2021 Metal
    # Universe carries NHL taxonomy but includes basketball). This exclusion
    # is scoped to the verified GR8 product, never to every NHL-tagged page.
    titles=re.findall(r'<h1\b[^>]*>(.*?)</h1>',html,re.S)
    if not any('GR8 Moments' in re.sub('<[^>]+>','',title) for title in titles):
        return None
    if parse_upper_deck_html(html)[0]:
        return None
    for tag in re.findall(r'<(?:article|div)\b[^>]*>',html):
        classes=re.search(r'class="([^"]*)"',tag)
        if not classes:
            continue
        if tag.startswith('<article') or 'data-elementor-type="single-post"' in tag:
            if 'checklist-license-nhl' in classes[1].split():
                return 'official_nhl_license'
    return None

def run():
    CACHE.mkdir(parents=True,exist_ok=True)
    path=DATA/'upper-deck-history.json'
    existing={p['key']:p for p in json.loads(path.read_text())} if path.exists() else {}
    for old, canonical in CANONICAL_SLUGS.items():
        old_key='upper-deck-'+sha256(('https://upperdeck.com/checklist/'+old+'/').encode()).hexdigest()[:16]
        canonical_key='upper-deck-'+sha256(('https://upperdeck.com/checklist/'+canonical+'/').encode()).hexdigest()[:16]
        if canonical_key in existing and old_key not in existing:
            existing[old_key]=existing.pop(canonical_key)
            existing[old_key]['key']=old_key
            existing[old_key]['alternate_discovery_keys']=[canonical_key]
        if old_key in existing and canonical_key in existing:
            existing[old_key].setdefault('alternate_discovery_keys',[])
            if canonical_key not in existing[old_key]['alternate_discovery_keys']:
                existing[old_key]['alternate_discovery_keys'].append(canonical_key)
            del existing[canonical_key]
    queue=list(SEEDS);visited=set();last=0
    with httpx.Client(timeout=30,follow_redirects=True) as client:
        while queue:
            slug=queue.pop(0)
            slug=next((old for old,canonical in CANONICAL_SLUGS.items() if canonical==slug),slug)
            if slug in visited:continue
            visited.add(slug);url='https://upperdeck.com/checklist/'+slug+'/'
            key='upper-deck-'+sha256(url.encode()).hexdigest()[:16]
            p=existing.get(key,dict(key=key,title=slug.replace('-',' '),publisher='Upper Deck',source_url=url,directory_url='https://upperdeck.com/checklists/',release_date=None,date_basis='unknown_no_explicit_release_date',window_status='unknown',status='pending'))
            if slug in CANONICAL_SLUGS:
                p.setdefault('original_source_url',url)
                url='https://upperdeck.com/checklist/'+CANONICAL_SLUGS[slug]+'/'
                p.update(source_url=url,directory_url='https://upperdeck.com/checklist-brand/skybox-metal-universe-champions/')
            existing[key]=p;target=CACHE/(slug+'.html')
            try:
                cached_mtime = target.stat().st_mtime_ns if target.exists() else None
                def pause():
                    nonlocal last
                    time.sleep(max(0,10-(time.monotonic()-last)));last=time.monotonic()
                h=load_checklist(client,url,target,pause=pause)
                if target.stat().st_mtime_ns != cached_mtime:
                    p['retrieval_format']='official_http_response'
                titles=re.findall(r'<h1\b[^>]*>(.*?)</h1>',h,re.S)
                if not titles:raise ValueError('Missing official title')
                title=unescape(re.sub('<[^>]+>','',titles[-1])).strip()
                title=re.sub(r'\s+Checklist$','',title)
                p['title']=title
                p['ownership_evidence']=dict(url=url,basis='Upper Deck official checklist publication',brands=re.findall(r'checklist-brand-([\w-]+)',h))
                rows,issues,pages=parse_upper_deck_html(h)
                p.update(row_count=len(rows),issue_count=len(issues),checksum=sha256(h.encode()).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat())
                (DATA/'raw'/f'{key}.pdf').write_bytes(target.read_bytes())
                excluded=exclusion_reason(h)
                if not excluded:
                    p.pop('exclusion_reason',None);p.pop('scope_evidence',None)
                if excluded:
                    p.update(status='excluded',exclusion_reason=excluded,scope_evidence=dict(url=url,basis='official checklist-license-nhl taxonomy'))
                    p.pop('error',None)
                elif not re.search(r'\b(?:19|20)\d{2}\b',title):
                    p.update(status='failed',error='Product season unknown; retained without guessing from page date')
                elif not rows:p.update(status='failed',error='No explicitly identified basketball rows; sport scope isolated')
                else:p.update(status='downloaded');p.pop('error',None)
                for linked in re.findall(r'href="https://upperdeck.com/checklist/([^"/?]+)/"',h):
                    if any(x in linked for x in ['goodwin','universe-champions','world-of-sports','nscc','allure-se','metal-universe-se']):queue.append(linked)
            except Exception as exc:p.update(status='failed',error=str(exc))
            write(path,list(existing.values()));print(len(visited),p['title'],p['status'],p.get('row_count',0),flush=True)
    write(CACHE/'discovery.json',dict(frozen_at=datetime.now(timezone.utc).isoformat(),known_sources=len(existing),directory_complete=False,full_30_year_denominator=None,blocked_entries=['legacy Collectors-Zone HTTP403','WordPress API HTTP403','checklists pagination HTTP403','sitemap HTTP403']))

if __name__=='__main__':run()
