from card_platform.ingestion.cardhobby import CardHobbyProvider, _matching_player_aliases


def test_mixed_script_ocr_name_gets_query_only_chinese_aliases():
    identity={'player_name':'TRAЕ YOUNG','product_name':'Select','insert_name':'Fanatical','card_number':'FAN-12','season_start':2024}
    original=dict(identity)
    queries=' '.join(q for _,q in CardHobbyProvider.build_query_plan(identity))
    assert '特雷' in queries or '吹杨' in queries
    assert identity==original
    assert '特雷 杨' not in _matching_player_aliases(identity)


def test_ascii_canonical_player_has_reviewed_chinese_aliases():
    queries=' '.join(q for _,q in CardHobbyProvider.build_query_plan({'player_name':'Trae Young','product_name':'Select'}))
    assert '特雷' in queries or '吹杨' in queries


def test_shared_surname_is_not_an_entity_correction():
    queries=' '.join(q for _,q in CardHobbyProvider.build_query_plan({'player_name':'Thaddeus Young','product_name':'Select'}))
    assert '吹杨' not in queries and '特雷' not in queries
