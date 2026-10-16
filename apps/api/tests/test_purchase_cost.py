from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost import (
    OrderCapture,
    allocate_shipping,
    evidence_hash,
    order_errors,
    score_match,
)


def capture(**changes):
    return OrderCapture.model_validate(
        dict(
            external_order_id="900001",
            ordered_at="2025-09-01",
            seller="Fixture shop",
            status="已完成",
            source_url="https://cardhobby.com.cn/Order/detail/900001",
            fetched_at="2025-09-02T00:00:00Z",
            currency="CNY",
            shipping="0.10",
            displayed_total="30.10",
            complete=True,
            lines=[
                dict(
                    item_id="900002",
                    title="2025 Topps NBA Hoops Test Player Gold Bounce House 卡号 123 7/50编",
                    item_url="https://cardhobby.com.cn/market/item/900002",
                    image_urls=[],
                    unit_price="10.00",
                    quantity=3,
                )
            ],
            **changes,
        )
    )


def card():
    return dict(
        player_name="Test Player",
        brand_name="Topps",
        product_name="NBA Hoops",
        season_start=2025,
        insert_name="Bounce House",
        parallel_name="Gold",
        card_number="123",
        serial_number=7,
        serial_total=50,
        notes="https://cardhobby.com.cn/market/item/900002",
        is_autograph=False,
        is_memorabilia=False,
    )


def test_order_validation_and_exact_allocation():
    order = capture()
    assert order_errors(order) == []
    units = allocate_shipping(order)
    assert [u["shipping"] for u in units] == [Decimal(".04"), Decimal(".03"), Decimal(".03")]
    assert sum(u["shipping"] for u in units) == order.shipping
    assert all(u["unit_price"] == Decimal(10) for u in units)


@pytest.mark.parametrize("quantity", [1, 2, 3, 7, 29, 500])
@pytest.mark.parametrize("cents", [0, 1, 2, 17, 100, 10001])
def test_shipping_conservation(quantity, cents):
    value = capture().model_dump()
    value["lines"][0]["quantity"] = quantity
    value["shipping"] = Decimal(cents) / 100
    units = allocate_shipping(OrderCapture.model_validate(value))
    assert sum(u["shipping"] for u in units) == Decimal(cents) / 100
    assert max(u["shipping"] for u in units) - min(u["shipping"] for u in units) <= Decimal(".01")


def test_allocation_order_and_hash_independent_of_input_line_order():
    value = capture().model_dump()
    second = {
        **value["lines"][0],
        "item_id": "900003",
        "item_url": "https://cardhobby.com.cn/market/item/900003",
        "quantity": 1,
    }
    value["lines"].append(second)
    first = OrderCapture.model_validate(value)
    value["lines"].reverse()
    second = OrderCapture.model_validate(value)
    assert allocate_shipping(first) == allocate_shipping(second)
    assert evidence_hash(first) == evidence_hash(second)


@pytest.mark.parametrize(
    "field,value",
    [
        ("shipping", None),
        ("displayed_total", "100"),
        ("discount", "1"),
        ("refund", "1"),
        ("extra_fee", "1"),
        ("complete", False),
        ("status", "待支付"),
        ("status", "已关闭"),
        ("status", "退款中"),
    ],
)
def test_unconfirmed_money_or_status_blocks(field, value):
    values = capture().model_dump()
    values[field] = value
    assert order_errors(OrderCapture.model_validate(values))


def test_zero_shipping_is_confirmed_not_missing():
    values = capture().model_dump()
    values.update(shipping="0", displayed_total="30", paid_total="30")
    assert not order_errors(OrderCapture.model_validate(values))


@pytest.mark.parametrize(
    "field,value",
    [
        ("shipping", "0.001"),
        ("currency", "USD"),
        ("source_url", "https://cardhobby.com.cn/Order/MyOrders?token=secret"),
        ("fetched_at", "2025-09-01T00:00:00"),
    ],
)
def test_invalid_precision_currency_and_credentials(field, value):
    values = capture().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        OrderCapture.model_validate(values)


def test_reject_extra_fields_duplicate_lines_and_bad_item_url():
    values = capture().model_dump()
    values["cookie"] = "private"
    with pytest.raises(ValidationError):
        OrderCapture.model_validate(values)
    del values["cookie"]
    values["lines"] *= 2
    with pytest.raises(ValidationError):
        OrderCapture.model_validate(values)
    values = capture().model_dump()
    values["lines"][0]["item_url"] = "https://example.com/900002"
    with pytest.raises(ValidationError):
        OrderCapture.model_validate(values)


def test_unique_high_quality_score():
    score = score_match(card(), capture().lines[0].model_dump())
    assert not score["conflicts"], score
    assert not score["missing"], score
    assert score["automatic"], score
    letter_card = {**card(), "card_number": "AB-12"}
    letter_line = capture().lines[0].model_dump()
    letter_line["title"] = letter_line["title"].replace("卡号 123", "卡号 AB-12")
    assert not score_match(letter_card, letter_line)["conflicts"]


@pytest.mark.parametrize(
    "old,new,conflict",
    [
        ("2025", "2024", "年份"),
        ("Topps", "Panini", "品牌"),
        ("7/50", "8/50", "分子"),
        ("7/50", "7/99", "总数"),
        ("Gold", "Red", "颜色"),
        ("卡号 123", "卡号 124", "卡号"),
        ("Bounce House", "Dreamland", "Insert"),
    ],
)
def test_hard_conflicts(old, new, conflict):
    line = capture().lines[0].model_dump()
    line["title"] = line["title"].replace(old, new)
    score = score_match(card(), line)
    assert any(conflict in value for value in score["conflicts"]), score
    assert not score["automatic"]


@pytest.mark.parametrize("suffix", [" 10张打包", " LOT2", " 签字", " 球衣物料"])
def test_bundles_and_attributes_conflict(suffix):
    line = capture().lines[0].model_dump()
    line["title"] += suffix
    assert score_match(card(), line)["conflicts"]


def test_full_variant_identity_does_not_require_physical_copy_anchor():
    stored = card()
    stored["notes"] = ""
    stored["serial_number"] = None
    result = score_match(stored, capture().lines[0].model_dump())
    assert result["automatic"], result


def base_card(**changes):
    return {
        **card(),
        "notes": "",
        "insert_name": None,
        "parallel_name": None,
        "card_number": None,
        "serial_number": None,
        "serial_total": None,
        **changes,
    }


def title_line(title):
    return {**capture().lines[0].model_dump(), "title": title, "quantity": 1}


@pytest.mark.parametrize("parallel", [None, "Base", "普卡"])
@pytest.mark.parametrize("number", [None, "123"])
def test_complete_unnumbered_base_identity_auto_matches_without_card_number(parallel, number):
    result = score_match(
        base_card(parallel_name=parallel, card_number=number),
        title_line("2025 Topps NBA Hoops Test Player Base"),
    )
    assert result["automatic"], result
    assert result["missing"] == []


@pytest.mark.parametrize("omitted", ["2025", "Topps", "NBA Hoops", "Test Player"])
def test_one_result_does_not_make_incomplete_core_identity_automatic(omitted):
    result = score_match(
        base_card(), title_line("2025 Topps NBA Hoops Test Player".replace(omitted, ""))
    )
    assert not result["automatic"], result
    assert result["missing"]


@pytest.mark.parametrize("title", [
    "2025 Topps NBA Hoops Test Player Gold",
    "2025 Topps NBA Hoops Test Player Silver Refractor",
    "2025 Topps NBA Hoops Test Player 金折",
    "2025 Topps NBA Hoops Test Player Dreamland",
    "2025 Topps NBA Hoops Test Player Unknown Insert 特卡",
    "2025 Topps NBA Hoops Test Player /50",
])
def test_matching_base_fields_do_not_guess_unknown_variant(title):
    result = score_match(base_card(), title_line(title))
    assert not result["automatic"], result


@pytest.mark.parametrize("variant", ["Gold Wave", "Gold Laser", "金波纹折", "金碎冰折"])
def test_same_color_different_parallel_technology_never_auto_matches(variant):
    result = score_match(
        base_card(parallel_name="Gold"),
        title_line(f"2025 Topps NBA Hoops Test Player {variant}"),
    )
    assert not result["automatic"], result
    assert result["conflicts"], result


def test_published_player_brand_product_insert_and_parallel_aliases_auto_match():
    entities = [
        ("player", "Test Player", ["测试球员"]),
        ("brand", "Topps", ["拓普斯"]),
        ("product", "NBA Hoops", ["测试产品"]),
        ("insert", "Bounce House", ["跳跳屋"]),
        ("parallel", "Gold", ["金折"]),
    ]
    knowledge = KnowledgeResolver({"entities": [
        {"id": str(i), "entity_type": kind, "canonical_name": name, "aliases": aliases}
        for i, (kind, name, aliases) in enumerate(entities)
    ]})
    result = score_match(
        base_card(insert_name="Bounce House", parallel_name="Gold"),
        title_line("2025 拓普斯 测试产品 测试球员 跳跳屋 金折"), knowledge,
    )
    assert result["automatic"], result


def test_insert_legacy_alias_matches_without_published_snapshot():
    result = score_match(
        base_card(insert_name="Bounce House"),
        title_line("2025 Topps NBA Hoops Test Player 跳跳屋"),
    )
    assert result["automatic"], result


def test_published_parent_alias_does_not_prove_child_product():
    knowledge = KnowledgeResolver({"entities": [{
        "id": "child", "entity_type": "product", "canonical_name": "Chronicles Hoops",
        "aliases": ["Chronicles", "编年史"],
    }]})
    result = score_match(
        base_card(brand_name="Panini", product_name="Chronicles Hoops"),
        title_line("2025 Panini 编年史 Test Player"), knowledge,
    )
    assert not result["automatic"], result
    assert "产品" in result["missing"]


def test_explicit_hash_card_number_conflict_is_not_optional():
    result = score_match(
        base_card(card_number="123"), title_line("2025 Topps NBA Hoops Test Player #124")
    )
    assert not result["automatic"], result
    assert "卡号冲突" in result["conflicts"]


@pytest.mark.parametrize("title_serial", ["/50", "50编", "7/50"])
def test_matching_serial_total_does_not_require_title_numerator(title_serial):
    result = score_match(
        base_card(serial_total=50, serial_number=7, parallel_name="Gold"),
        title_line(f"2025 Topps NBA Hoops Test Player Gold {title_serial}"),
    )
    assert result["automatic"], result


@pytest.mark.parametrize("title_serial", ["/99", "8/50"])
def test_serial_conflict_still_blocks_with_otherwise_full_identity(title_serial):
    result = score_match(
        base_card(serial_total=50, serial_number=7, parallel_name="Gold"),
        title_line(f"2025 Topps NBA Hoops Test Player Gold {title_serial}"),
    )
    assert result["conflicts"], result
    assert not result["automatic"]


@pytest.mark.parametrize("url", [
    "https://cardhobby.com.cn/market/item/9000021",
    "https://cardhobby.com.cn/market/item/900002/other",
    "https://cardhobby.com.cn/market/item/900002?other=1",
    "https://cardhobby.com.cn.evil.test/market/item/900002",
])
def test_source_link_must_match_exact_item_path(url):
    result = score_match(base_card(notes=url), title_line("Test Player"))
    assert not result["automatic"], result


def test_canonical_source_link_can_supply_omitted_title_identity():
    result = score_match(
        base_card(notes="来源：https://www.cardhobby.com.cn/market/item/900002/。"),
        title_line("Test Player"),
    )
    assert result["automatic"], result


def test_exact_source_link_never_overrides_explicit_identity_conflict():
    result = score_match(card(), title_line("2024 Topps NBA Hoops Test Player"))
    assert not result["automatic"], result
    assert "年份冲突" in result["conflicts"]


def test_color_in_player_name_is_not_inferred_as_parallel():
    result = score_match(
        base_card(player_name="Jalen Green"),
        title_line("2025 Topps NBA Hoops Jalen Green"),
    )
    assert result["automatic"], result


def test_latin_player_name_does_not_match_inside_longer_word():
    result = score_match(base_card(player_name="Test Player"), title_line("2025 Topps NBA Hoops Test Players"))
    assert not result["automatic"], result


def test_known_patch_does_not_auto_match_generic_jersey_title():
    result = score_match(
        base_card(is_patch=True, is_memorabilia=True),
        title_line("2025 Topps NBA Hoops Test Player Jersey"),
    )
    assert not result["automatic"], result
    assert "Patch 切割" in result["missing"]


@pytest.mark.parametrize("marker", ["Patch", "切割", "RPA"])
def test_known_patch_matches_explicit_patch_evidence(marker):
    result = score_match(
        base_card(is_patch=True, is_memorabilia=True, is_autograph=marker == "RPA"),
        title_line(f"2025 Topps NBA Hoops Test Player {marker}"),
    )
    assert result["automatic"], result


@pytest.mark.parametrize("anchor", ["link", "image"])
def test_exact_source_anchor_can_supply_omitted_patch_description(anchor):
    line = title_line("2025 Topps NBA Hoops Test Player Jersey")
    stored = base_card(is_patch=True, is_memorabilia=True)
    if anchor == "link":
        stored["notes"] = line["item_url"]
    else:
        stored["image_front_url"] = "https://images.cardhobby.com/fixture-patch.jpg"
        line["image_urls"] = [stored["image_front_url"]]
    result = score_match(stored, line)
    assert result["automatic"], result


@pytest.mark.parametrize("marker", ["No Patch Jersey", "非 Patch 球衣"])
def test_exact_source_cannot_override_explicit_nonpatch_description(marker):
    line = title_line(f"2025 Topps NBA Hoops Test Player {marker}")
    result = score_match(base_card(is_patch=True, is_memorabilia=True, notes=line["item_url"]), line)
    assert not result["automatic"], result
    assert "Patch 切割属性冲突" in result["conflicts"]


def test_plain_memorabilia_does_not_auto_match_explicit_patch():
    result = score_match(
        base_card(is_patch=False, is_memorabilia=True),
        title_line("2025 Topps NBA Hoops Test Player Patch"),
    )
    assert not result["automatic"], result
    assert "Patch 切割属性冲突" in result["conflicts"]


def test_season_fraction_is_not_serial_and_explicit_end_conflicts():
    stored = base_card(season_start=2025, season_end=2026)
    assert score_match(stored, title_line("2025/26 Topps NBA Hoops Test Player"))["automatic"]
    result = score_match(stored, title_line("2025/27 Topps NBA Hoops Test Player"))
    assert not result["automatic"], result
    assert result["conflicts"]


def test_capture_time_not_part_of_hash():
    order = capture()
    later = order.model_copy(update={"fetched_at": datetime.now(timezone.utc)})
    assert evidence_hash(order) == evidence_hash(later)


def test_public_commodity_code_and_internal_item_url_are_distinct_identifiers():
    value = capture().model_dump()
    value["lines"][0]["item_id"] = "250901123456789"
    parsed = OrderCapture.model_validate(value)
    assert parsed.lines[0].item_id == "250901123456789"
    assert parsed.lines[0].item_url.endswith("/900002")
    assert score_match(card(), parsed.lines[0].model_dump())["automatic"]


def test_comic_issue_number_is_not_autograph_card_checklist_number():
    target = dict(player_name='Joshua Cassara',season_start=2024,brand_name='Topps',
        product_name='Marvel Chrome',insert_name='Chrome Autographs',parallel_name=None,
        card_number='CA-JC',serial_number=6,serial_total=10,is_autograph=True,
        is_memorabilia=False,notes='https://cardhobby.com.cn/market/item/900002')
    line = capture().lines[0].model_dump()
    line['title'] = '2024 Topps Chrome Marvel Joshua Cassara /10编 画师签字 2018年《Falcon #4》漫画封面'
    match = score_match(target,line)
    assert '卡号冲突' not in match['conflicts']
    assert match['automatic']
    line['title'] += ' 卡号 CA-XX'
    assert '卡号冲突' in score_match(target,line)['conflicts']


def test_large_material_window_alone_is_not_a_rookie_patch_autograph_insert():
    target = dict(player_name='Derik Queen', season_start=2026, brand_name='Topps',
        product_name='Motif', insert_name='Rookie Relics', card_number='MRR-DQ',
        serial_total=75, is_autograph=False, is_memorabilia=True,
        notes='https://cardhobby.com.cn/market/item/900002')
    line = capture().lines[0].model_dump()
    line['title'] = '2026 Topps Motif Derik Queen RC 新秀 大窗球衣 实物 /75编'
    assert score_match(target,line)['automatic']
    line['title'] += ' Rookie Patch Autographs'
    assert 'Insert 冲突' in score_match(target,line)['conflicts']


@pytest.mark.parametrize('seller_suffix', [
    '卡品如图 #js91#', '收藏必备#1聂-EEXE', '收藏必备 #3 杰-EEXE',
    '卡品如图 JH #B8-15 7.23', '瑕疵如图1 L甲#820',
])
def test_seller_stock_labels_are_not_checklist_numbers(seller_suffix):
    target = card()
    line = capture().lines[0].model_dump()
    line['title'] = '2025 Topps NBA Hoops Test Player Gold Bounce House 7/50编 ' + seller_suffix
    assert score_match(target, line)['automatic']
    line['title'] = '2025 Topps NBA Hoops Test Player Gold Bounce House 卡号 999 7/50编 ' + seller_suffix
    assert '卡号冲突' in score_match(target,line)['conflicts']


@pytest.mark.parametrize('number', ['#999', '#CA-JC', '卡号 #JS91#', 'No. 999', '卡号 #1聂-EEXE'])
def test_actual_checklist_labels_remain_hard_conflicts(number):
    line = capture().lines[0].model_dump()
    line['title'] = '2025 Topps NBA Hoops Test Player Gold Bounce House 7/50编 卡品如图 ' + number
    assert '卡号冲突' in score_match(card(),line)['conflicts']


def test_century_boundary_season_end_is_2000_not_1900():
    target={**card(), 'season_start':1999, 'season_end':2000}
    line=capture().lines[0].model_dump()
    line['title']=line['title'].replace('2025','1999-00')
    assert score_match(target,line)['automatic']
    line['title']=line['title'].replace('1999-00','1999-01')
    assert '赛季结束年份冲突' in score_match(target,line)['conflicts']


def test_signature_class_box_name_is_not_an_autographed_card():
    target=dict(player_name='Nolan Traore',brand_name='Topps',season_start=2025,
        product_name='Topps Signature Class',insert_name='Odyssey',is_autograph=False,
        notes='https://cardhobby.com.cn/market/item/900002')
    line=capture().lines[0].model_dump()
    line['title']='2025-26 Topps Signature Class Nolan Traore 签字盒 Odyssey 特卡'
    assert score_match(target,line)['automatic']
    line['title'] += ' 卡签'
    assert '签字属性冲突' in score_match(target,line)['conflicts']


def test_parallel_of_base_set_is_not_plain_base_conflict():
    target={**card(),'parallel_name':'Gold Wave'}
    line=capture().lines[0].model_dump()
    line['title']='2025 Topps NBA Hoops Test Player Gold Wave Bounce House 7/50编 base 折射'
    assert score_match(target,line)['automatic']
    line['title']='2025 Topps NBA Hoops Test Player Bounce House 普卡 正base'
    assert '平行与 Base 冲突' in score_match(target,line)['conflicts']


def test_consignor_tag_does_not_add_a_conflicting_insert():
    target=dict(player_name='Giannis Antetokounmpo',brand_name='Panini',season_start=2021,
        product_name='Donruss Elite',insert_name='Spellbound',card_number='10',
        notes='https://cardhobby.com.cn/market/item/900002')
    line=capture().lines[0].model_dump()
    line['title']='2021-22 Panini Donruss Elite Giannis Antetokounmpo N字母 拼写特卡 微瑕如图/大头/【D】'
    assert score_match(target,line)['automatic']
    line['title']='2021-22 Panini Donruss Elite Giannis Antetokounmpo Jam Masters 大头特卡 微瑕如图/卖家/【D】'
    assert 'Insert 冲突' in score_match(target,line)['conflicts']


def test_prepared_recall_keeps_all_existing_recall_paths():
    from card_platform.purchase_cost import prepare_purchase_recall
    target={**card(),'notes':''}
    line=capture().lines[0].model_dump()
    recall=prepare_purchase_recall(target, KnowledgeResolver())
    for title in ['2025 Test Player Gold','2025 Other Player','2025 Test Players']:
        candidate={**line,'title':title}
        assert recall(candidate)==score_match(target,candidate)['recalled']
    unrelated={**line,'title':'2025 Other Player'}
    assert prepare_purchase_recall(card(),KnowledgeResolver())(unrelated)
    image_target={**target,'image_front_url':'https://images.cardhobby.com/source.jpg'}
    assert prepare_purchase_recall(image_target,KnowledgeResolver())({**unrelated,'image_urls':[image_target['image_front_url']]})
    roster={**target,'player_name':'LeBron James, Stephen Curry'}
    assert prepare_purchase_recall(roster,KnowledgeResolver())({**line,'title':'2025 Stephen Curry'})


def test_single_release_year_vs_cross_year_listing_needs_visual_review():
    target={**card(),'season_start':2026,'season_end':None}
    line=capture().lines[0].model_dump();line['title']=line['title'].replace('2025','2025-26')
    result=score_match(target,line)
    assert '年份冲突' not in result['conflicts']
    assert result['soft_differences'] and not result['automatic']
    line['title']=line['title'].replace('2025-26','2024-25')
    assert '年份冲突' in score_match(target,line)['conflicts']


@pytest.mark.parametrize('tail,conflict', [('7/25/【D】', '限编总数冲突'),
                                         ('7/50编 /卡号 999/【D】', '卡号冲突')])
def test_consignor_cleanup_cannot_erase_identity_evidence(tail, conflict):
    target = {**card(), 'notes': 'https://cardhobby.com.cn/market/item/900002'}
    line = capture().lines[0].model_dump()
    line['title'] = '2025 Topps NBA Hoops Test Player Gold Bounce House ' + tail
    result = score_match(target, line)
    assert not result['automatic']
    assert conflict in result['conflicts']


def test_printed_tre_johnson_iii_name_matches_reviewed_marketplace_name():
    target = dict(player_name='Tre Johnson III', brand_name='Topps', season_start=2025,
                  product_name='Topps Chrome Updates Basketball', insert_name='Shadow Etch',
                  card_number='SE-13')
    line = capture().lines[0].model_dump()
    line['title'] = '2025-26 Topps Chrome Update Tre Johnson Shadow Etch RC 卡号 SE-13'
    result = score_match(target, line)
    assert result['automatic'], result
    line['title'] = line['title'].replace('Tre Johnson', 'Keldon Johnson')
    assert not score_match(target, line)['automatic']


@pytest.mark.parametrize('dash', ['‐', '‑', '–', '—'])
def test_unicode_season_dash_keeps_end_year_evidence(dash):
    target = {**card(), 'season_start': 2004, 'season_end': None}
    line = capture().lines[0].model_dump()
    line['title'] = line['title'].replace('2025', '2003' + dash + '04')
    result = score_match(target,line)
    assert not result['conflicts']
    assert result['soft_differences'] and not result['automatic']


def test_die_cut_captains_is_not_a_memorabilia_or_patch_card():
    target=dict(player_name='Jamal Murray',brand_name='Topps',season_start=2025,
                product_name='Topps Chrome Update',insert_name='Captains',is_memorabilia=False,
                notes='https://cardhobby.com.cn/market/item/900002')
    line=capture().lines[0].model_dump()
    line['title']='2025-26 Topps Chrome Update Jamal Murray Captains 异形切割特卡'
    assert score_match(target,line)['automatic']
    line['title'] += ' 球衣物料切割'
    assert '球衣属性冲突' in score_match(target,line)['conflicts']


def test_generic_retro_description_does_not_assert_retrofractor_insert():
    target=dict(player_name='Michael Jordan',brand_name='Skybox',season_start=2021,
                product_name='Metal Universe Champions',insert_name='1997-98 Retro',
                notes='https://cardhobby.com.cn/market/item/900002')
    line=capture().lines[0].model_dump()
    line['title']='2021 Skybox Metal Universe Michael Jordan 复古特卡'
    assert score_match(target,line)['automatic']
    line['title'] += ' Retrofractor Variations'
    assert 'Insert 冲突' in score_match(target,line)['conflicts']


@pytest.mark.parametrize('name', ['勒布朗 詹姆斯', '勒布朗詹姆斯'])
def test_full_lebron_chinese_name_is_identity_evidence_not_query_only(name):
    target={**card(),'player_name':'LeBron James','notes':None}
    line=capture().lines[0].model_dump()
    line['title']=line['title'].replace('Test Player',name)
    assert score_match(target,line)['automatic']
    line['title']=line['title'].replace(name,'詹姆斯')
    assert not score_match(target,line)['automatic']
