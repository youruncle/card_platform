import pytest
from card_platform.formal_cardhobby_gate import title_discloses_damage

@pytest.mark.parametrize('text',['边框原始小花点','正面有花点','背面有小花点','表面轻微花点'])
def test_specific_surface_spots_are_disclosed_damage(text):
    assert title_discloses_damage(text)

@pytest.mark.parametrize('text',['无明显花点','没有小花点','无瑕疵','所有商品默认不保卡品 请看图','漂亮的花点平行图案'])
def test_negation_boilerplate_and_printed_pattern_are_not_damage(text):
    assert not title_discloses_damage(text)

@pytest.mark.parametrize('text',['正面无 花点','背面没有明显 花点','正面花点图案','表面花点设计','边框花点纹样'])
def test_surface_location_does_not_turn_pattern_or_negation_into_damage(text):
    assert not title_discloses_damage(text)
