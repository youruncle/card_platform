"""Legacy captures remain usable without accepting changed auction facts."""
from copy import deepcopy

import pytest

from card_platform.public_source_catalog import observe_public_sale, validated_sale


def capture():
    return observe_public_sale({
        "ID": 142402352,
        "Title": "2024-25 Panini Prizm Stephen Curry #23 base",
        "EffectiveDate": "2026-10-10 12:00:00",
        "Status": -2, "ByWay": 2, "OutCount": 2, "LowestPrice": "100.00",
        "TitImg": "https://images.cardhobby.com/test.jpg",
    })


@pytest.mark.parametrize("missing", [
    ("seller_id", "consignment_code"),
    ("seller_id", "consignment_code", "source_metadata", "source_gallery"),
])
def test_new_optional_empty_fields_do_not_invalidate_immutable_legacy_capture(missing):
    observation = capture()
    for key in missing:
        observation["sale"].pop(key)
    before = deepcopy(observation)
    sale, _ = validated_sale(observation)
    assert str(sale.sale_price) == "100.00"
    assert sale.item_id == "142402352"
    assert observation == before  # Never backfill or rewrite the raw evidence.


@pytest.mark.parametrize("key,value", [
    ("sale_price", "999"), ("shipping_price", "10"),
    ("occurred_at", "2026-10-09T12:00:00+08:00"),
    ("title", "different card"), ("item_id", "999"),
    ("grade_company", "PSA"), ("seller_id", "different-seller"),
    ("source_metadata", {"unverified": True}), ("unknown_field", None),
])
def test_legacy_compatibility_still_rejects_any_changed_fact_or_nondefault_metadata(key, value):
    observation = capture()
    observation["sale"].pop("consignment_code")
    observation["sale"][key] = value
    with pytest.raises(ValueError, match="public_sale_facts_changed"):
        validated_sale(observation)


@pytest.mark.parametrize("key", ["sale_price", "shipping_price", "title", "occurred_at", "bid_count"])
def test_missing_original_fact_is_not_filled_from_a_new_parse(key):
    observation = capture()
    observation["sale"].pop(key)
    with pytest.raises(ValueError, match="public_sale_facts_changed"):
        validated_sale(observation)


def test_raw_hash_is_checked_before_legacy_schema_compatibility():
    observation = capture()
    observation["sale"].pop("seller_id")
    observation["raw_row"]["LowestPrice"] = "999"
    with pytest.raises(ValueError, match="invalid_public_observation"):
        validated_sale(observation)
