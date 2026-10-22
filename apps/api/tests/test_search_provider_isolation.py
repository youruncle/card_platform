"""The independently configured Bailian search key must never reach DeepSeek."""

from card_platform.config import Settings

ALI = "https://ws-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def test_explicit_bailian_search_credentials_are_usable_without_vision_fallback():
    s = Settings(
        vision_api_key="vision-fixture", search_api_key="bailian-fixture", search_base_url=ALI
    )
    assert s.resolved_search_api_key == "bailian-fixture"
    assert s.resolved_search_base_url == ALI
    assert s.card_search_model == s.valuation_search_model == "qwen-plus"


def test_missing_search_key_does_not_borrow_the_visual_key():
    s = Settings(vision_api_key="vision-fixture")
    assert s.resolved_search_api_key is None


def test_unpaired_historical_search_key_never_uses_a_default_deepseek_endpoint():
    s = Settings(vision_api_key="vision-fixture", search_api_key="old-bailian-fixture")
    assert s.resolved_search_api_key is None


def test_explicit_deepseek_endpoint_cannot_receive_a_search_credential():
    s = Settings(
        vision_api_key="vision-fixture",
        search_api_key="bailian-fixture",
        search_base_url="https://api.deepseek.com",
    )
    assert s.resolved_search_api_key is None


def test_a_visual_key_copied_into_bailian_config_is_not_a_search_key():
    s = Settings(
        vision_api_key="vision-fixture", search_api_key="vision-fixture", search_base_url=ALI
    )
    assert s.resolved_search_api_key is None
