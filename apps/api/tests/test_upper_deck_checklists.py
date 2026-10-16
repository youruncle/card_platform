from card_platform.official_checklists import official_url, product_fields, parse_document


def test_upper_deck_official_hosts_only():
    assert official_url('https://upperdeck.com/checklist/example/')
    assert official_url('https://www.upperdeckepack.com/api/Checklist/GetChecklist?productId=1')
    assert not official_url('https://upperdeck.com.evil.test/checklist/')


def test_season_rollover_1999():
    assert product_fields('1999-00 Upper Deck Basketball')['season_end'] == 2000


def test_ud_html_filters_sport_preserves_evidence_and_isolates_updates(tmp_path):
    raw = '''<html><table class="csv-table"><tr><th>Set Name</th><th>Card</th><th>Description</th><th>Category</th><th>#'d</th></tr>
    <tr><td>Base Set</td><td>1</td><td>Michael Jordan</td><td>Basketball</td><td></td></tr>
    <tr><td>Base Set</td><td>2</td><td>Wayne Gretzky</td><td>Hockey</td><td></td></tr>
    <tr><td>Base Plaid</td><td>1</td><td>Michael Jordan</td><td>Basketball</td><td>25</td></tr>
    <tr><td>2014 Goodwin Champions Update - Autographs</td><td>A-1</td><td>Michael Jordan</td><td>Basketball</td><td></td></tr>
    <tr><td>Goudey</td><td>G-1</td><td>Michael Jordan</td><td></td><td></td></tr></table></html>'''
    p=tmp_path/'raw.html';p.write_text(raw)
    rows,issues,pages=parse_document(p)
    assert len(rows)==2
    assert rows[0]['player']=='Michael Jordan' and rows[0]['source_line']==2
    assert rows[1]['insert'] is None and rows[1]['parallel'] is None
    assert rows[1]['serial_total']==25
    assert {i['reason'] for i in issues}=={'cross_product_update','sport_unknown'}
    assert pages


def test_ud_brand_aliases_are_source_scoped():
    from card_platform.knowledge_card_catalog import ScopedCardCatalog
    row={'entity_type':'card','id':'ud:1','attributes':{'product':'Skybox Metal Universe Champions','season_start':2023,'season_end':2024,'brand':'Upper Deck','brand_aliases':['Skybox','UD'],'player':'Michael Jordan','card_number':'1','insert':'Jambalaya','source_url':'https://upperdeck.com/checklist/test/'}}
    c=ScopedCardCatalog([row])
    identity=dict(product_name='Upper Deck Skybox Metal Universe Champions',brand_name='Skybox',season_start=2023,season_end=2024,player_name='Michael Jordan',card_number='1')
    assert c.enrich(identity)[0]['insert_name']=='Jambalaya'
    assert not c.lookup(identity | {'brand_name':'Panini'})
    assert not c.lookup(identity | {'product_name':'Fleer Retro'})


def test_epack_checks_product_scope_and_does_not_guess_parallel_print_run():
    from card_platform.official_checklists import parse_upper_deck_epack
    title='2017-18 Turkish Airlines EuroLeague'
    record=dict(PlayerName='Luka Doncic',CardNumber='1',InsertName=title+' Exclusives Parallel',EpackProductName=title,CardTemplateID='example')
    rows,issues,pages=parse_upper_deck_epack({title+' Exclusives Parallel':[record,record|{'EpackProductName':'Other Product'}]},title)
    assert len(rows)==1 and rows[0]['parallel']=='Exclusives'
    assert rows[0]['insert'] is None and rows[0]['serial_total'] is None
    assert len(issues)==1 and issues[0]['reason']=='cross_product_scope'


def test_truncated_html_is_not_importable(tmp_path):
    import pytest
    p=tmp_path/'incomplete.html';p.write_text('<table class="csv-table"><tr><td>Basketball</td></tr>[Truncated]')
    with pytest.raises(ValueError,match='Incomplete'):
        parse_document(p)


def test_publisher_progress_separates_unknown_scope_and_denominators():
    from card_platform.official_checklists import publisher_progress
    products=[dict(publisher='Upper Deck',title='2017 Goodwin Champions',status='published',window_status='unknown',issue_count=2),
              dict(publisher='Upper Deck',title='2018 Goodwin Champions',status='failed',window_status='unknown'),
              dict(publisher='Panini',title='2025-26 Select',status='excluded',window_status='outside')]
    result=publisher_progress(products)
    assert result['Upper Deck']['known_success_percent']==50
    assert result['Upper Deck']['date_unknown']==2
    assert result['Upper Deck']['years']['2017']['published']==1
    assert result['Upper Deck']['full_window_percent'] is None
    assert result['Panini']['eligible_total']==0


def test_source_manifest_preserves_ud_release_and_ownership_evidence():
    from card_platform.official_checklists import build_manifest
    product=dict(key='ud-audit',title='2017 Goodwin Champions',publisher='Upper Deck',source_url='https://upperdeck.com/checklist/test/',checksum='abc',date_scope='e-Pack availability',ownership_evidence={'basis':'official publication'},document_format='upper_deck_epack',retrieval_format='browser_dom')
    metadata=build_manifest(product,[])['sources'][0]['metadata']
    for key in ('date_scope','ownership_evidence','document_format','retrieval_format'):
        assert metadata[key]==product[key]


def test_base_subset_labels_are_not_guessed_as_parallel():
    from card_platform.official_checklists import parse_upper_deck_html
    for section in ('Base Set - Fresh Foundation', 'Base Set - First Look', 'Base Set - Short Prints'):
        html=f'<table class="csv-table"><tr><th>Set Name</th><th>Card</th><th>Description</th><th>Category</th></tr><tr><td>{section}</td><td>1</td><td>Michael Jordan</td><td>Basketball</td></tr></table>'
        rows,issues,_=parse_upper_deck_html(html)
        assert len(rows)==1 and not issues
        assert rows[0]['parallel'] is None
        assert rows[0]['section']==section
