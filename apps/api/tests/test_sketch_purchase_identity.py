"""Art-card regressions: set logo != subject; artist signature != subject auto."""

import pytest

from card_platform.purchase_cost import score_match
from card_platform.domain.product_identity import assess_product_match, ProductMatchStatus
from card_platform.recognition_benchmark_predictor import merge_side_predictions
from card_platform.schemas import CardRecognitionResult
from card_platform.vision_routing import recognition_quality_issues


def sketch(**changes):
    return dict(player_name="Sentry", brand_name="Upper Deck", product_name="Marvel Spider-Man",
                season_start=2022, insert_name="Artist Sketch Card", serial_number=1,
                serial_total=1, is_autograph=False, is_memorabilia=False, **changes)


def listing(suffix="画师亲笔签字卡签", subject="Sentry"):
    return dict(title=f"2022 Upper Deck Marvel (MCU) Spider-Man 手绘卡 {subject} 1/1编 {suffix}",
                item_id="123456", item_url="https://cardhobby.com.cn/market/item/98765", image_urls=[])


@pytest.mark.parametrize("subject", ["Sentry", "Gamora", "Doctor Strange"])
@pytest.mark.parametrize("signature", ["画师亲笔签字卡签", "画家签名", "artist signed", "signed by the artist"])
def test_reviewed_art_source_matches_without_false_autograph_conflict(subject, signature):
    target = sketch(notes="已核对实物 https://cardhobby.com.cn/market/item/98765")
    target["player_name"] = subject
    match = score_match(target, listing(signature, subject))
    assert match["automatic"], match
    assert match["conflicts"] == []
    assert match["missing"] == []
    assert "画师署名与主体签字属性分开核对" in match["reasons"]


def test_same_subject_and_one_of_one_is_not_same_artwork():
    match = score_match(sketch(), listing())
    assert match["recalled"] and not match["automatic"]
    assert match["conflicts"] == []
    assert match["missing"] == ["手绘原作需核对实物并关联来源商品"]


@pytest.mark.parametrize("suffix", ["画师签名 球员签字", "演员亲笔签名", "autograph", "画师签名 reprint", "画师签名 复制品"])
def test_source_link_does_not_override_real_conflict(suffix):
    match = score_match(sketch(notes="https://cardhobby.com.cn/market/item/98765"), listing(suffix))
    assert not match["automatic"] and match["conflicts"]


def test_wrong_source_link_does_not_prove_physical_artwork():
    match = score_match(sketch(notes="https://cardhobby.com.cn/market/item/123456"), listing())
    assert not match["automatic"]  # public number is not the internal URL id


def test_artist_signature_exception_is_not_applied_to_ordinary_cards():
    target = sketch(notes="https://cardhobby.com.cn/market/item/98765")
    target["insert_name"] = None
    assert "签字属性冲突" in score_match(target, listing())["conflicts"]


def test_series_alias_does_not_match_another_marvel_series():
    assert assess_product_match("Marvel (MCU) Spider-Man 手绘卡", "Marvel Spider-Man") == ProductMatchStatus.MATCH
    assert assess_product_match("Marvel Masterpieces Sentry 手绘卡", "Marvel Spider-Man") != ProductMatchStatus.MATCH
    assert assess_product_match("Marvel (MCU) Spider-Man", "Marvel Metal Universe Spider-Man") != ProductMatchStatus.MATCH


@pytest.mark.parametrize("front_subject", ["Sentry", "Gamora", None])
def test_generic_art_back_does_not_overwrite_front_subject(front_subject):
    front = CardRecognitionResult(player_name=front_subject, insert_name="Artist Sketch Card", confidence="0.9", image_role="front")
    back = CardRecognitionResult(player_name="SPIDER-MAN", product_name="Marvel Spider-Man",
                                 season_start=2022, card_number="017", insert_name="Artist Sketch Card",
                                 image_role="back", confidence="0.99", field_evidence={"player_name":["卡背 Logo"]})
    assert merge_side_predictions(front, back).player_name == front_subject
    assert merge_side_predictions(None, back).player_name is None


def test_logo_subject_quality_gate_requires_review_not_high_confidence_acceptance():
    value = CardRecognitionResult(player_name="SPIDER-MAN", product_name="Marvel Spider-Man",
                                  insert_name="Artist Sketch Card", confidence="0.99")
    assert "sketch_subject_may_be_series_logo" in recognition_quality_issues(value)
    assert "sketch_subject_may_be_series_logo" not in recognition_quality_issues(value.model_copy(update={"player_name":"Sentry"}))


def test_ladypool_reviewed_sketch_order_uses_film_title_and_artist_qinqian():
    target = dict(player_name='Ladypool', brand_name='Topps', season_start=2025,
                  product_name='Deadpool Chrome', insert_name='Original Sketch Card',
                  is_autograph=False, is_memorabilia=False,
                  notes='逐图核对 https://cardhobby.com.cn/market/item/134695940')
    line = dict(title='2025 Topps Chrome Marvel 漫威影视 死侍与金刚狼 Ladypool 女死侍 Sketch 手绘卡 1/1编 画师亲签 签字 卡签 Jean Sinclair',
                item_id='260910172040691', item_url='https://cardhobby.com.cn/market/item/134695940', image_urls=[])
    match = score_match(target, line)
    assert match['automatic'], match
    assert not match['conflicts']
    assert not score_match({**target, 'notes': ''}, line)['automatic']
    assert not score_match(target, {**line, 'title': line['title'] + ' 演员亲笔签名'})['automatic']


def test_deadpool_film_title_does_not_match_other_chrome_sets():
    assert assess_product_match('2025 Topps Chrome Marvel Spider-Man 手绘卡', 'Deadpool Chrome') != ProductMatchStatus.MATCH
    assert assess_product_match('2025 死侍与金刚狼 普通电影卡', 'Deadpool Chrome') != ProductMatchStatus.MATCH
