"""Build the reviewed NBA alias manifest from pinned factual source extracts.

Build dependency only: opencc-python-reimplemented. No network or database writes.
PYTHONPATH=apps/api/src:/tmp/card-alias-build-deps .venv/bin/python scripts/build_nba_player_aliases.py
"""
import gzip
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from opencc import OpenCC

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/knowledge_sources/nba_aliases_20260923.json.gz'
OUTPUT = ROOT / 'apps/api/src/card_platform/data/nba_player_aliases_v1.json'


def key(value):
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]', '', value.casefold())


def build(source):
    cc = OpenCC('t2s')
    players = {}
    names = defaultdict(set)
    for nba_id, last, first, name, active in source['nba_api_players']:
        pid = f'nba-{nba_id}'
        players[pid] = dict(name=name, nba_id=str(nba_id), active=active, identity={name}, query=set(), preferred=[], evidence=[], source='nba-api')
        names[key(name)].add(pid)
    # Exact full-name joins only. Ambiguous names cannot be joined by surname or fuzzy similarity.
    for row in source['wikidata_labels']:
        pid = 'nba-' + row['nba']['value']
        if pid in players and row['label']['xml:lang'] == 'en':
            names[key(row['label']['value'])].add(pid)
    for row in source['wikidata_aliases']:
        pid = 'nba-' + row['nba']['value']
        if pid in players and row['label']['xml:lang'] == 'en' and len(row['label']['value'].split()) > 1:
            names[key(row['label']['value'])].add(pid)
    reviewed = json.loads((ROOT / 'data/knowledge_sources/nba_aliases_reviewed.json').read_text())
    for row in reviewed:
        pid = 'nba-' + row['nba_id']
        p = players[pid]
        p['identity'].update(row['aliases'])
        p['query'].update(row.get('query_aliases', []))
        p['preferred'].extend(row.get('query_aliases', []))
        p['evidence'].append(dict(source='reviewed', url=row['url'], values=row['aliases']))
        for value in row['aliases']:
            names[key(value)].add(pid)
    unjoined = []
    for row in source['sina']:
        name = (row['first_name'] + ' ' + row['last_name']).strip()
        ids = names.get(key(name), set())
        if len(ids) > 1:
            unjoined.append(row)
            continue
        if not ids:
            # Only current roster additions qualify; past spelling mismatches remain review candidates.
            if not row['source_url'].endswith('season=2026'):
                unjoined.append(row)
                continue
            pid = 'sina-' + row['pid']
            players.setdefault(pid, dict(name=name, nba_id=None, active=True, identity={name}, query=set(), preferred=[], evidence=[], source='sina'))
            names[key(name)].add(pid)
        else:
            pid = next(iter(ids))
        p = players[pid]
        p['identity'].add(name)
        parts = [v.strip() for v in (row['first_name_cn'], row['last_name_cn']) if v.strip()]
        full = '·'.join(parts)
        if re.search('[\u4e00-\u9fff]', full):
            p['identity'].update((full, ''.join(parts), ' '.join(parts)))
            if len(parts) == 2 and len(parts[-1]) >= 2:
                p['query'].add(parts[-1])
                if parts[-1] not in p['preferred']:
                    p['preferred'].append(parts[-1])
            p['evidence'].append(dict(source='sina', url=row['source_url'], value=full))
    for row in source['wikidata_labels']:
        pid = 'nba-' + row['nba']['value']
        if pid not in players:
            continue
        value = row['label']['value']
        lang = row['label']['xml:lang']
        p = players[pid]
        if lang == 'en':
            # English display labels may differ from card names; retain full names only.
            if len(value.split()) > 1:
                p['identity'].add(value)
        elif re.search('[\u4e00-\u9fff]', value):
            value = re.sub(r'\s*[（(].*?[）)]', '', value).strip()
            p['identity'].update((value, cc.convert(value)))
            for v in (value, cc.convert(value)):
                if '·' in v and len(v.split('·')[-1]) >= 2:
                    p['query'].add(v.split('·')[-1])
            p['evidence'].append(dict(source='wikidata', url=row['p']['value'].replace('http:', 'https:'), value=value, language=lang))
    for row in source['wikidata_aliases']:
        pid = 'nba-' + row['nba']['value']
        value = row['label']['value']
        if pid not in players or len(value) > 60:
            continue
        # Only name-shaped translations enter identity vocabulary. Unreviewed nicknames stay out.
        if re.search('[\u4e00-\u9fff]', value) and '·' in value and not re.search('[()（）,，]', value):
            players[pid]['identity'].update((value, cc.convert(value)))
            players[pid]['evidence'].append(dict(source='wikidata', url=row['p']['value'].replace('http:', 'https:'), value=value, language=row['label']['xml:lang']))
    for name, aliases in source['legacy_aliases'].items():
        ids = names.get(key(name), set())
        if len(ids) == 1:
            p = players[next(iter(ids))]
            excluded = source['legacy_exclusions'].get(name, [])
            for alias in aliases:
                (p['query'] if alias in excluded else p['identity']).add(alias)
            p['evidence'].append(dict(source='legacy', value=name))
    # Add only mechanical spelling variants, preserving Jr/Sr/II/III.
    for p in players.values():
        for value in tuple(p['identity']):
            p['identity'].add(re.sub(r"['’.]", '', value))
            p['identity'].add(''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c)))
            if re.search('[\u4e00-\u9fff]', value):
                p['identity'].add(re.sub(r'[·・‧\s-]', '', value))
        p['identity'].discard('')
        p['query'].discard('')
    owners = defaultdict(set)
    for pid, p in players.items():
        for value in p['identity']:
            owners[key(value)].add(pid)
    # Identity consumers match Chinese substrings. A father's full name can be
    # contained in a son's name (加里佩顿 / 加里佩顿二世); exact collisions alone
    # are insufficient. Demote these overlapping forms to discovery vocabulary.
    full_names = [(k, set(ids)) for k, ids in owners.items() if re.search('[\u4e00-\u9fff]', k)]
    for full, ids in full_names:
        for start in range(len(full)):
            for end in range(start + 2, len(full) + 1):
                part = full[start:end]
                if part != full and part in owners and owners[part] - ids:
                    owners[part].update(ids)
    for pid, p in players.items():
        for value in p['query']:
            owners[key(value)].add(pid)
    collisions = {k: sorted(v) for k, v in owners.items() if len(v) > 1}
    entities = []
    for pid, p in sorted(players.items()):
        ambiguous = {v for v in p['identity'] if key(v) in collisions}
        p['identity'] -= ambiguous
        p['query'] |= ambiguous
        canonical = p['name']
        if key(canonical) in collisions:
            canonical += f' [{pid}]'
        # Always use NBA directory spelling as canonical, including suffixes.
        aliases = sorted(v for v in p['identity'] if v != canonical)
        queries = sorted(p['query'] - p['identity'])
        entities.append(dict(key=pid, entity_type='player', canonical_name=canonical,
            source_key=p['source'], review_status='approved', confidence=0.95,
            attributes=dict(nba_id=p['nba_id'], active_at_source=p['active'], league='NBA',
                query_aliases=queries, preferred_query_aliases=p['preferred'], alias_evidence=p['evidence'],
                chinese_coverage='sourced' if any(re.search('[\u4e00-\u9fff]', v) for v in (*aliases, *queries)) else 'missing_source'),
            aliases=[dict(value=v, language='zh' if re.search('[\u4e00-\u9fff]', v) else 'en', alias_type='translation' if re.search('[\u4e00-\u9fff]', v) else 'synonym', confidence=0.95) for v in aliases]))
    definitions=[('nba-api','NBA historical player directory (nba_api snapshot)','https://github.com/swar/nba_api/blob/master/src/nba_api/stats/library/data.py','nba_api'),('sina','Sina NBA bilingual team rosters','https://slamdunk.sports.sina.com.cn/roster','Sina Sports'),('wikidata','NBA ID P3647 labels and name variants','https://www.wikidata.org/wiki/Property:P3647','Wikidata (CC0)')]
    manifest=dict(batch_key='nba-player-aliases-v1',manifest_version=1,catalog_enabled=True,
        sources=[dict(key=k,title=t,url=u,publisher=p,source_type='other',retrieved_at=source['retrieved_at']) for k,t,u,p in definitions],
        entities=entities,required_regressions=['Josh Hart','约什哈特','Devin Vassell'],relations=[],coverage=[])
    report=dict(nba_directory=len(source['nba_api_players']),entities=len(entities),
        chinese_covered=sum(e['attributes']['chinese_coverage']=='sourced' for e in entities),
        chinese_identity_covered=sum(any(re.search('[\u4e00-\u9fff]', a['value']) for a in e['aliases']) for e in entities),
        missing_chinese=[dict(name=e['canonical_name'],nba_id=e['attributes']['nba_id']) for e in entities if e['attributes']['chinese_coverage']=='missing_source'],
        collisions=collisions,unjoined_sina=unjoined,
        identity_aliases=sum(len(e['aliases']) for e in entities),query_aliases=sum(len(e['attributes']['query_aliases']) for e in entities))
    return manifest, report


if __name__ == '__main__':
    with gzip.open(SOURCE,'rt') as stream:
        manifest, report = build(json.load(stream))
    OUTPUT.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'data/knowledge_sources/nba_aliases_coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print({k:v for k,v in report.items() if not isinstance(v,(list,dict))})
