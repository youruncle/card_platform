"""Non-person recall and visible-field roles from the two reported cards."""
from decimal import Decimal
import asyncio
from io import BytesIO

import pytest

from card_platform.image_service import sanitize_identity_field_roles
from card_platform.purchase_cost_search import build_purchase_cost_search_plan
from card_platform.schemas import CardRecognitionResult
from card_platform.config import Settings
from card_platform.image_service import CardVisionService
from starlette.datastructures import UploadFile, Headers


@pytest.mark.parametrize('name', ['无法识别球员', '未识别球员', '未知球员'])
def test_person_placeholder_is_never_an_order_search(name):
    result = build_purchase_cost_search_plan({'player_name': name})
    assert result['keywords'] == []
    assert result['status'] == 'skipped'


def test_non_person_card_uses_printed_work_title_and_product_for_discovery():
    result = build_purchase_cost_search_plan(dict(player_name='无法识别球员',
        brand_name='Topps', product_name='Star Wars Galaxy', insert_name='Falcon Rising',
        image_front_url='/uploads/front.jpg', serial_number=38, serial_total=50))
    assert result['status'] == 'ready'
    assert result['keywords'] == ['Falcon Rising', 'Star Wars Galaxy']
    assert result['keyword_sources'] == ['non_player_title', 'product']
    assert result['fallback_keywords'] == []


@pytest.mark.parametrize('updates', [
    {'image_front_url':None}, {'product_name':'Topps'},
])
def test_missing_person_search_stays_bounded_by_specific_product_and_image(updates):
    card = dict(player_name='无法识别球员',product_name='Star Wars Galaxy',
                insert_name='Falcon Rising',image_front_url='/uploads/front.jpg')
    plan = build_purchase_cost_search_plan({**card, **updates})
    assert plan['status'] == 'skipped'
    assert not plan['keywords']


@pytest.mark.parametrize('position', ['UPPER-RIGHT', 'upper right', 'top-left', '右上角'])
def test_region_description_is_not_a_printed_catalog_number(position):
    original = CardRecognitionResult(player_name=None,brand_name='Topps',
        product_name='Star Wars Galaxy',card_number=position,confidence=Decimal('.83'))
    result = sanitize_identity_field_roles(original)
    assert result.card_number is None
    assert result.recognition_status == 'research_required'
    assert 'card_number_is_location_not_catalog_identifier' in result.recognition_quality_issues


@pytest.mark.parametrize('number', ['93', 'NS-20', 'GR-5'])
def test_real_printed_catalog_numbers_remain_intact(number):
    original = CardRecognitionResult(card_number=number,confidence=Decimal('.98'))
    assert sanitize_identity_field_roles(original).card_number == number


@pytest.mark.parametrize('evidence', [
    '卡背传记：in the 2024/25 season at Illinois',
    'Back biography: he led Illinois in the 2024/25 season.',
    '卡背大学赛季 2024/25，不是产品发行行',
])
def test_college_biography_season_is_not_a_card_release(evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(player_name='Kasparas Jakucionis',
        brand_name='Topps',product_name='Hoops',season_start=2024,season_end=2025,
        card_number='NS-20',confidence=Decimal('.98'),
        field_evidence={'season_start':[evidence],'season_end':[evidence]}))
    assert result.season_start is None and result.season_end is None
    assert result.recognition_status == 'research_required'


def test_explicit_release_line_wins_over_a_separate_biography_year():
    result = sanitize_identity_field_roles(CardRecognitionResult(season_start=2025,season_end=2026,
        confidence=Decimal('.98'),field_evidence={key:[
            '产品行 2025-26 Topps Basketball','传记 2024/25 college season',
        ] for key in ('season_start','season_end')}))
    assert (result.season_start,result.season_end) == (2025,2026)


def test_catalog_enrichment_cannot_restore_a_biography_year(monkeypatch):
    class Provider:
        async def recognize(self, content, content_type):
            return CardRecognitionResult(player_name='Kasparas Jakucionis',
                brand_name='Topps',season_start=2025,season_end=2026,confidence=Decimal('.98'))

    service = CardVisionService(Settings(),provider=Provider())

    async def recover(result, images):
        return result

    async def enrich(result):
        return result.model_copy(update=dict(season_start=2024,season_end=2025,
            field_evidence={key:['联网候选：Back bio states 2024/25 season']
                            for key in ('season_start','season_end')}))

    monkeypatch.setattr(service,'_recover_serial',recover)
    monkeypatch.setattr(service.search,'enrich',enrich)
    upload = UploadFile(file=BytesIO(b'\xff\xd8\xfffixture'),filename='front.jpg',
                        headers=Headers({'content-type':'image/jpeg'}))
    result = asyncio.run(service.recognize(upload))
    assert result.season_start is None and result.season_end is None


@pytest.mark.parametrize('verified', [True,False])
def test_verified_catalog_release_can_replace_a_biography_year(verified):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        player_name='Kasparas Jakucionis',season_start=2025,season_end=2026,
        catalog_verified_fields={'season_start':2025,'season_end':2026} if verified else {},
        card_number='NS-20',confidence=Decimal('.98'),
        field_evidence={key:['Back bio: 2024/25 season',
            ('目录核验：' if verified else '联网候选：')+'官方 checklist 对应发行赛季 2025-26']
            for key in ('season_start','season_end')},
        web_candidates=[dict(title='2025-26 Topps Basketball New School',
            identity=dict(player_name='Kasparas Jakucionis',card_number='NS-20',season_start=2025,season_end=2026),
            matching_fields=['player_name','card_number','season_start','season_end'],
            source_urls=['https://www.topps.com/pages/checklists'],match_confidence='.96')]))
    assert (result.season_start,result.season_end) == ((2025,2026) if verified else (None,None))


def test_roster_discovery_covers_each_subject_within_three_queries():
    plan = build_purchase_cost_search_plan(dict(player_name='Curry / James / Durant',
        product_name='Topps Now',image_front_url='/uploads/front.jpg'))
    assert plan['keywords'] == ['Stephen Curry','LeBron James','Kevin Durant']
    assert plan['keyword_sources'] == ['roster_subject']*3
    assert plan['fallback_keywords'] == ['Topps Now']


def test_roster_query_reserves_public_number_priority_and_respects_budget():
    plan=build_purchase_cost_search_plan(dict(player_name='Curry / James / Durant',
        notes='商品编号：123456',product_name='Topps Now',image_front_url='/uploads/front.jpg'),max_keywords=2)
    assert plan['keywords'] == ['123456','Stephen Curry']


def test_missing_sports_name_can_recall_specific_product_with_front_image():
    plan=build_purchase_cost_search_plan(dict(player_name='无法识别球员',product_name='NBA Hoops',
        image_front_url='/uploads/front.jpg'))
    assert plan['keywords'] == ['NBA Hoops']
    assert plan['keyword_sources'] == ['product']
    assert plan['fallback_keywords'] == []


@pytest.mark.parametrize('product', ['Topps','Panini','NBA','Basketball','未知','无法识别产品','unknown','x'*121])
def test_missing_name_does_not_recall_generic_or_invalid_product(product):
    plan=build_purchase_cost_search_plan(dict(player_name=None,product_name=product,image_front_url='/uploads/front.jpg'))
    assert plan['status']=='skipped' and not plan['keywords']


@pytest.mark.parametrize('product',['未知产品','未识别产品','未知品牌产品','Sports Cards','NBA Trading Cards','Topps NBA','Basketball Cards','体育球星卡',
    'Upper Deck','Upper Deck Cards','Fleer','Skybox','Leaf','品牌产品','Upper Deck Basketball'])
def test_missing_subject_rejects_combined_generic_product_labels(product):
    plan=build_purchase_cost_search_plan({'player_name':None,'product_name':product,'image_front_url':'/uploads/front.jpg'})
    assert plan['status']=='skipped' and plan['keywords']==[]


def test_actual_topps_basketball_flagship_product_still_supports_discovery():
    plan=build_purchase_cost_search_plan({'player_name':None,'product_name':'Topps Basketball','image_front_url':'/uploads/front.jpg'})
    assert plan['keywords']==['Topps Basketball']
