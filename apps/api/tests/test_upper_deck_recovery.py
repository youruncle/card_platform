import importlib.util
from pathlib import Path
import httpx
import pytest

spec=importlib.util.spec_from_file_location('ud_downloader',Path(__file__).resolve().parents[3]/'scripts/download_upper_deck_checklists.py')
ud=importlib.util.module_from_spec(spec);spec.loader.exec_module(ud)
HTML='<h1>2022 Goodwin Champions Checklist</h1><table class="csv-table"><tr><th>Set Name</th><th>Card</th><th>Description</th><th>Category</th></tr><tr><td>Base Set</td><td>1</td><td>Michael Jordan</td><td>Basketball</td></tr></table>'
URL='https://upperdeck.com/checklist/2022-goodwin-champions-checklist/'


def test_bad_cache_is_preserved_and_replaced_with_complete_response(tmp_path):
    target=tmp_path/'page.html';target.write_text('<table class="csv-table">[Truncated]')
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=HTML))) as client:
        result=ud.load_checklist(client,URL,target,pause=lambda:None)
    assert result==HTML and target.read_text()==HTML
    assert '[Truncated]' in next(tmp_path.glob('page.html.invalid-*')).read_text()


def test_incomplete_download_never_becomes_a_reusable_cache(tmp_path):
    target=tmp_path/'page.html'
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,text='<h1>Title</h1><table class="csv-table">'))) as client:
        with pytest.raises(ValueError,match='Incomplete'):
            ud.load_checklist(client,URL,target,pause=lambda:None)
    assert not target.exists()


def test_transient_403_recovers_without_publishing_error_page(tmp_path):
    responses=iter([httpx.Response(403,text='Forbidden'),httpx.Response(200,text=HTML)])
    with httpx.Client(transport=httpx.MockTransport(lambda request:next(responses))) as client:
        result=ud.load_checklist(client,URL,tmp_path/'page.html',pause=lambda:None)
    assert result==HTML


def test_nhl_license_is_excluded_but_multi_sport_is_not():
    assert ud.exclusion_reason('<article class="checklist-category-multi-sport checklist-license-nhl"><h1>2024-25 GR8 Moments Checklist</h1>')=='official_nhl_license'
    assert ud.exclusion_reason('<article class="checklist-category-multi-sport">') is None


def test_sync_preserves_official_non_basketball_exclusion(tmp_path,monkeypatch):
    import sys,json
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
    import sync_official_history as history
    monkeypatch.setattr(history,'DATA',tmp_path)
    product=dict(key='nhl',title='2024-25 GR8 Moments',publisher='Upper Deck',status='excluded',exclusion_reason='official_nhl_license',release_date=None)
    (tmp_path/'catalog.json').write_text(json.dumps({'products':[product]}))
    (tmp_path/'panini').mkdir();(tmp_path/'panini/progress.json').write_text(json.dumps({'products':[],'directory_complete':True,'archive_years':[]}))
    (tmp_path/'raw').mkdir();(tmp_path/'raw/nhl.pdf').write_text('Not a basketball source')
    assert history.prepare()['products'][0]['status']=='excluded'


def test_missing_sport_column_uses_only_exact_row_official_evidence():
    from card_platform.official_checklists import parse_upper_deck_html,build_manifest
    html='<table class="csv-table"><tr><th>Set Name</th><th>Card</th><th>Description</th></tr><tr><td>VIP</td><td>PC-2</td><td>Michael Jordan</td></tr><tr><td>Other</td><td>PC-2</td><td>Michael Jordan</td></tr><tr><td>VIP</td><td>PC-1</td><td>Wayne Gretzky</td></tr></table>'
    proof=dict(section='VIP',card_number='PC-2',player='Michael Jordan',sport='basketball',source_url=URL,source_checksum='a'*64,source_locator='table 1 line 2',raw_text='Base Set\t1\tMichael Jordan\tBasketball')
    rows,issues,_=parse_upper_deck_html(html,row_sport_evidence=[proof])
    assert len(rows)==1 and len(issues)==2
    assert rows[0]['sport_evidence']==proof and rows[0]['raw_text']=='VIP\tPC-2\tMichael Jordan'
    product=dict(key='nscc',title='2025 NSCC',publisher='Upper Deck',source_url='https://upperdeck.com/checklist/2025-nscc-checklist/',checksum='b'*64)
    cards=[e for e in build_manifest(product,rows)['entities'] if e['entity_type']=='card']
    assert cards[0]['attributes']['sport_evidence']==proof
    rows,_,_=parse_upper_deck_html(html,row_sport_evidence=[proof|{'source_url':'https://unofficial.example/checklist'}])
    assert rows==[]


def test_supplemental_sport_evidence_checks_real_source_and_identity(tmp_path,monkeypatch):
    import sync_official_history as history
    from hashlib import sha256
    monkeypatch.setattr(history,'DATA',tmp_path)
    (tmp_path/'raw').mkdir();(tmp_path/'raw/goodwin.pdf').write_text(HTML)
    proof=dict(section='VIP',card_number='PC-2',player='Michael Jordan',sport='basketball',source_key='goodwin',source_url=URL,source_checksum=sha256(HTML.encode()).hexdigest(),source_locator='table 1 line 2',source_page=1,source_line=2,raw_text='Base Set\t1\tMichael Jordan\tBasketball')
    catalog={'products':[dict(key='goodwin',source_url=URL)]}
    product={'row_sport_evidence':[proof]}
    assert history.validated_sport_evidence(product,catalog)==[proof]
    with pytest.raises(ValueError):history.validated_sport_evidence({'row_sport_evidence':[proof|{'player':'Wayne Gretzky'}]},catalog)
    (tmp_path/'raw/goodwin.pdf').write_text('tampered')
    with pytest.raises(ValueError):history.validated_sport_evidence(product,catalog)


def test_nhl_taxonomy_on_real_elementor_post_wrapper_is_excluded():
    html='<article class="blog-article"><div data-elementor-type="single-post" data-elementor-id="325" class="elementor checklist checklist-license-nhl checklist-category-multi-sport"><h1>2024-25 GR8 Moments Checklist</h1></div></article>'
    assert ud.exclusion_reason(html)=='official_nhl_license'
    assert ud.exclusion_reason('<article class="blog-article"><div class="related-post checklist-license-nhl"></div></article>') is None


@pytest.mark.parametrize("include_legacy", [False, True])
def test_verified_canonical_link_recovers_existing_candidate_key(tmp_path,monkeypatch,include_legacy):
    import json
    from hashlib import sha256
    slug='2022-23-metal-universe-champions-checklist'
    old='https://upperdeck.com/checklist/'+slug+'/'
    canonical='https://upperdeck.com/checklist/2022-metal-universe-champions/'
    monkeypatch.setattr(ud,'DATA',tmp_path);monkeypatch.setattr(ud,'CACHE',tmp_path/'upper-deck');monkeypatch.setattr(ud,'SEEDS',[slug,'2022-metal-universe-champions']);monkeypatch.setattr(ud.time,'sleep',lambda _:None)
    (tmp_path/'raw').mkdir()
    client_type=httpx.Client
    transport=httpx.MockTransport(lambda request:httpx.Response(200,text=HTML.replace('2022 Goodwin Champions','2022-23 Metal Universe Champions')) if str(request.url)==canonical else httpx.Response(404))
    monkeypatch.setattr(ud.httpx,'Client',lambda **kwargs:client_type(transport=transport))
    (tmp_path/'upper-deck-history.json').write_text(json.dumps([dict(key='upper-deck-'+sha256(url.encode()).hexdigest()[:16],title='old candidate',publisher='Upper Deck',source_url=canonical,status='pending') for url in ((old,canonical) if include_legacy else (canonical,))]))
    ud.run()
    products=json.loads((tmp_path/'upper-deck-history.json').read_text())
    assert len(products)==1
    product=products[0]
    assert product['status']=='downloaded'
    assert product['key']=='upper-deck-'+sha256(old.encode()).hexdigest()[:16]
    assert product['source_url']==canonical and product['original_source_url']==old


def test_nhl_license_does_not_exclude_multi_sport_basketball():
    html='<div data-elementor-type="single-post" class="checklist-license-nhl">'+HTML+'</div>'
    assert ud.exclusion_reason(html) is None
