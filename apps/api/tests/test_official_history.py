import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('downloader', ROOT / 'scripts/download_panini_checklists.py')
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


def test_resume_reselects_old_exclusions_without_losing_downloads():
    rows = [dict(release_date='2010-01-01', status='excluded'),
            dict(release_date='2026-01-01', status='downloaded', checksum='abc'),
            dict(release_date=None, status='failed'),
            dict(release_date='2006-10-03', status='downloaded')]
    selected = downloader.select_window(rows, '2006-10-04', '2026-10-04')
    assert len(selected) == 3
    assert rows[0]['status'] == 'pending'
    assert rows[1]['checksum'] == 'abc'
    assert rows[1]['status'] == 'downloaded'
    assert rows[3]['status'] == 'excluded'


def test_progress_separates_attempts_from_success():
    from card_platform.official_checklists import progress_summary
    result = progress_summary([{'status':'published','window_status':'in_window','issue_count':2},
                               {'status':'failed','window_status':'unknown'},
                               {'status':'excluded','window_status':'outside'}], discovery_complete=False)
    assert result['eligible_total'] == 2
    assert result['known_success_percent'] == 50
    assert result['quarantined_rows'] == 2
    assert result['success_percent'] is None


def test_catalog_loads_published_artifact_only_for_matching_scope(tmp_path):
    import gzip, json, hashlib
    from card_platform.knowledge_card_catalog import ScopedCardCatalog
    entity = {'id':'history-1','entity_type':'card','attributes':{'product':'Prizm','brand':'Panini','season_start':2012,'season_end':2013,'player':'Kobe Bryant','card_number':'1','insert':'Finalists','source_url':'https://support.paniniamerica.net/test','source_locator':'page 1 line 2'}}
    raw = gzip.compress(json.dumps([entity]).encode())
    (tmp_path/'a.json.gz').write_bytes(raw)
    refs = [{'product':'Prizm','season_start':2012,'season_end':2013,'path':'a.json.gz','checksum':hashlib.sha256(raw).hexdigest()}]
    catalog = ScopedCardCatalog((), artifacts=refs, artifact_root=tmp_path)
    identity = dict(product_name='Prizm',brand_name='Panini',season_start=2012,season_end=2013,player_name='Kobe Bryant',card_number='1')
    assert catalog.enrich(identity)[0]['insert_name'] == 'Finalists'
    assert not catalog.lookup(identity | {'season_start':2014})
    (tmp_path/'a.json.gz').write_bytes(b'corrupt')
    fresh = ScopedCardCatalog((), artifacts=refs, artifact_root=tmp_path)
    assert not fresh.lookup(identity)
    assert fresh.artifact_errors


def test_conflicting_official_rows_are_quarantined_as_a_group():
    from card_platform.official_checklists import isolate_conflicting_rows
    base=dict(section='Base',card_number='1',player='Test Player',team='A',insert=None,parallel=None,serial_total=None,source_page=1,source_line=2,raw_text='original')
    accepted, issues = isolate_conflicting_rows([base,base | {'team':'B','source_line':3},base | {'card_number':'2','source_line':4}])
    assert [r['card_number'] for r in accepted] == ['2']
    assert len(issues)==2
    assert all(i['reason']=='conflicting_card_identity' for i in issues)


def test_bad_artifact_is_atomic_and_recoverable(tmp_path):
    import gzip, json, hashlib
    from card_platform.knowledge_card_catalog import ScopedCardCatalog
    entity={'id':'x','entity_type':'card','attributes':{'product':'Prizm','season_start':2012,'season_end':2013,'player':'Kobe','card_number':'1','insert':'Wrong','source_url':'https://support.paniniamerica.net/test'}}
    good=gzip.compress(json.dumps([entity]).encode())
    bad=gzip.compress(json.dumps([entity,{}]).encode())
    path=tmp_path/'a.gz';path.write_bytes(bad)
    ref=dict(product='Prizm',season_start=2012,season_end=2013,path='a.gz',checksum=hashlib.sha256(bad).hexdigest())
    catalog=ScopedCardCatalog((),artifacts=[ref],artifact_root=tmp_path)
    identity=dict(product_name='Prizm',season_start=2012,player_name='Kobe',card_number='1')
    assert not catalog.lookup(identity)
    path.write_bytes(good);ref['checksum']=hashlib.sha256(good).hexdigest()
    assert len(catalog.lookup(identity))==1


def test_explicit_teamless_layout_preserves_base_and_locator():
    from card_platform.official_checklists import parse_teamless_pages
    rows,issues = parse_teamless_pages(['BASE\nBOWMAN CHROME PROSPECTS\n  1 Mikey Williams\nINSERT\nHEAT CHECK\n HC-1 Chet Holmgren'])
    assert not issues
    assert rows[0]['insert'] is None and rows[0]['team'] is None
    assert rows[1]['insert']=='Heat Check' and rows[1]['source_line']==6


def test_mercury_layout_does_not_treat_parallel_marketing_as_cards():
    from card_platform.official_checklists import parse_mercury_pages
    rows,issues=parse_mercury_pages(['Base Cards\n1      Victor Wembanyama RC\nBase Card Parallels (20 Subjects)\n3 per box\n/99 Refractor\nInsert Cards Base /99 Refractor\nVW-1    Victor Wembanyama RC       Wingspan Insert'])
    assert len(rows)==2
    assert rows[0]['insert'] is None
    assert rows[0]['player']=='Victor Wembanyama'
    assert rows[1]['insert']=='Wingspan'
    assert all(r['team'] is None for r in rows)


def test_artifact_cache_is_bounded(tmp_path):
    import gzip,json,hashlib
    from card_platform.knowledge_card_catalog import ScopedCardCatalog
    refs=[]
    for year in range(2010,2020):
        e=dict(id=str(year),entity_type='card',attributes=dict(product='Test',season_start=year,season_end=year,player='Player',card_number='1',source_url='https://support.paniniamerica.net/test'))
        raw=gzip.compress(json.dumps([e]).encode());name=f'{year}.gz';(tmp_path/name).write_bytes(raw)
        refs.append(dict(product='Test',season_start=year,season_end=year,path=name,checksum=hashlib.sha256(raw).hexdigest()))
    catalog=ScopedCardCatalog((),artifacts=refs,artifact_root=tmp_path)
    for year in range(2010,2020):
        assert catalog.lookup(dict(product_name='Test',season_start=year,season_end=year,player_name='Player',card_number='1'))
    assert len(catalog.artifact_indexes)<=8
    assert catalog.lookup(dict(product_name='Test',season_start=2010,player_name='Player',card_number='1'))


def test_legacy_sync_does_not_reimport_published_attachments(tmp_path, monkeypatch):
    import asyncio,json
    from types import SimpleNamespace
    import httpx
    spec=importlib.util.spec_from_file_location('legacy_sync',ROOT/'scripts/sync_official_checklists.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    path=tmp_path/'catalog.json'
    path.write_text(json.dumps(dict(products=[dict(key='x',title='2012 Panini Prizm Basketball',publisher='Panini',source_url='https://support.paniniamerica.net/test',release_date='2012-11-01',status='published',artifact={'path':'verified.gz'})])))
    async def refuse(*args,**kwargs): raise AssertionError('Must not redownload published attachment')
    monkeypatch.setattr(httpx.AsyncClient,'get',refuse)
    asyncio.run(module.run(SimpleNamespace(catalog=str(path),start='2006-10-04',end='2026-10-04',discover=False,apply=False)))
    assert json.loads(path.read_text())['products'][0]['status']=='published'
