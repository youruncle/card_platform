from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

from card_platform.domain.codex_joint_valuation import extract_evidence, joint_valuation

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def fixture():
    item = dict(
        id="item-1",
        player_name="Stitch",
        season_start=2023,
        product_name="Topps Chrome",
        brand_name="Topps",
        card_number="SC-6",
        insert_name="Stitch in Costume",
        parallel_name=None,
        serial_total=None,
        grade_company=None,
        grade_value=None,
        is_autograph=False,
        is_memorabilia=False,
        image_front_url="/uploads/f.jpg",
        image_back_url="/uploads/b.jpg",
    )
    snapshot = dict(
        id="snap-1",
        analysis_version=2,
        created_at=NOW,
        identity_confidence=0.97,
        valuation_confidence=0.3,
        fair_value=50,
        raw_result=dict(
            collection_item_id="item-1",
            reliable_price=False,
            identity=dict(
                character="Stitch / 史迪奇",
                year="2023",
                brand="Topps Chrome Disney 100",
                card_number="SC-6",
                parallel="Base",
                insert="Stitch in Costume",
                autograph=False,
                memorabilia=False,
            ),
            input_images=[
                dict(side="front", url="/uploads/f.jpg", sha256="front"),
                dict(side="back", url="/uploads/b.jpg", sha256="back"),
            ],
        ),
        evidence_summary=[
            dict(
                type="sold_listing",
                url="https://www.ebay.com/itm/123456789012",
                detail="页面明确Sold，2026年7月30日显示9.25美元，商品为2023同标题SC-6未评级。国际邮费11.86美元单列，不计为卡价；未完成原图比对。",
            )
        ],
    )
    reviews = {
        "https://www.ebay.com/itm/123456789012": dict(
            price="9.25",
            currency="USD",
            sold_at="2026-07-30",
            checked_at="2026-09-25",
            sale_confirmed=True,
            actual_price_known=True,
            identity_confirmed=True,
            title="2023 Topps Chrome Disney 100 Stitch In Costume #SC-6",
            identity=dict(
                player_name="Stitch",
                season_start=2023,
                product_name="Topps Chrome",
                card_number="SC-6",
                insert_name="Stitch in Costume",
                parallel_name="Base",
                is_autograph=False,
                is_memorabilia=False,
                serial_total=None,
                grade_company=None,
            ),
            scope="成交状态金额及文字身份已核验；品相未逐图确认",
        )
    }
    fx = dict(base="USD", quote="CNY", rate="6.7", date="2026-09-25", url="https://fx.test/rates")
    return item, snapshot, reviews, fx


def evaluate(item, snapshot, reviews, fx, **kwargs):
    return joint_valuation(
        item,
        [snapshot],
        kwargs.pop("original", None),
        kwargs.pop("market", []),
        "CNY",
        reviews=reviews,
        fx=fx,
        image_hashes={"front": "front", "back": "back"},
        now=NOW,
        **kwargs,
    )


def test_extracts_sale_date_and_price_but_not_shipping():
    _, snapshot, _, _ = fixture()
    e = extract_evidence(snapshot)[0]
    assert e["price"] == Decimal("9.25")
    assert e["sold_at"] == "2026-07-30"
    assert e["kind"] == "reported_sale"
    assert e["verified"] is False


def test_legacy_text_sale_cannot_price_without_current_research_contract():
    args = fixture()
    before = deepcopy(args)
    r = evaluate(*args)
    assert r["status"] == "review_required"
    assert r["fair_value"] is None
    assert args == before


def test_unverified_history_is_extracted_but_not_published():
    item, snapshot, _, fx = fixture()
    r = evaluate(item, snapshot, {}, fx)
    assert r["fair_value"] is None
    assert len(r["recovered_evidence"]) == 1


def test_codex_estimate_never_becomes_a_second_sale():
    args = fixture()
    r = evaluate(*args)
    args[1]["fair_value"] = 100000
    assert evaluate(*args)["fair_value"] == r["fair_value"]


def test_identity_mismatch_prevents_using_sale():
    item, snapshot, reviews, fx = fixture()
    snapshot["raw_result"]["identity"]["card_number"] = "SC-7"
    r = evaluate(item, snapshot, reviews, fx)
    assert r["status"] == "review_required"
    assert r["fair_value"] is None


def test_changed_image_bytes_invalidate_historical_binding():
    item, snapshot, reviews, fx = fixture()
    snapshot["raw_result"]["input_images"][0]["sha256"] = "old"
    assert evaluate(item, snapshot, reviews, fx)["fair_value"] is None


def test_duplicate_sale_across_history_counts_once():
    item, snapshot, reviews, fx = fixture()
    r = joint_valuation(
        item,
        [snapshot, deepcopy(snapshot)],
        None,
        [],
        "CNY",
        reviews=reviews,
        fx=fx,
        image_hashes={"front": "front", "back": "back"},
        now=NOW,
    )
    assert len(r["accepted_evidence"]) == 0


def test_unknown_best_offer_is_never_a_sale_amount():
    _, snapshot, _, _ = fixture()
    snapshot["evidence_summary"][0].update(
        type="sold_best_offer_unknown",
        detail="2026-09-02售出；40美元，Best offer accepted，实际成交价未知",
    )
    e = extract_evidence(snapshot)[0]
    assert e["kind"] == "unknown_sale_price"


def test_listing_negative_statement_is_not_sold():
    _, snapshot, _, _ = fixture()
    snapshot["evidence_summary"][0].update(
        type="挂牌，非成交", detail="2026-09-02挂牌9美元，不作为成交"
    )
    assert extract_evidence(snapshot)[0]["kind"] == "asking_price"


def test_codex_parallel_rejects_wrong_system_sale():
    item, snapshot, _, fx = fixture()
    snapshot["raw_result"]["identity"]["parallel"] = "Negative Refractor"
    item["parallel_name"] = None
    original = dict(
        id="v1",
        fair_value=1,
        low_value=1,
        high_value=1,
        currency="CNY",
        method="ai_estimate",
        evidence={"used_comparables": [{"market_comp_id": "comp-1"}]},
    )
    market = [
        dict(
            id="comp-1",
            listing_title="2023 Topps Chrome Stitch in Costume SC-6 Base",
            sale_price="1",
            currency="CNY",
            occurred_at="2026-08-01",
            source_url="https://market.test/1",
            raw_payload={},
        )
    ]
    r = evaluate(item, snapshot, {}, fx, original=original, market=market)
    assert r["rejected_evidence"]
    assert r["status"] == "review_required"


def test_manual_estimate_remains_authoritative():
    r = evaluate(
        *fixture(),
        original=dict(
            id="manual", method="manual", fair_value=80, low_value=70, high_value=90, currency="CNY"
        ),
    )
    assert r["fair_value"] == 80
    assert r["status"] == "original_only"


def test_reviewed_source_must_match_insert_as_well_as_card_number():
    item, snapshot, reviews, fx = fixture()
    item["insert_name"] = "Other Insert"
    snapshot["raw_result"]["identity"]["insert"] = "Other Insert"
    r = evaluate(item, snapshot, reviews, fx)
    assert r["fair_value"] is None
    assert r["rejected_evidence"]


def test_unparsed_system_title_does_not_justify_changing_price():
    item, snapshot, reviews, fx = fixture()
    original = dict(
        id="v1",
        method="ai_estimate",
        fair_value=80,
        low_value=60,
        high_value=100,
        currency="CNY",
        evidence={"used_comparables": [{"market_comp_id": "comp-1"}]},
    )
    market = [
        dict(
            id="comp-1",
            listing_title="",
            sale_price="80",
            currency="CNY",
            occurred_at="2026-08-01",
            source_url="https://market.test/1",
        )
    ]
    r = evaluate(item, snapshot, {}, fx, original=original, market=market)
    assert r["fair_value"] == 80
    assert r["status"] == "review_required"


def test_image_bound_review_can_correct_ocr_without_mutating_collection():
    item, snapshot, reviews, fx = fixture()
    item["insert_name"] = "Wrong OCR"
    recovery = {
        "front:back": dict(
            fields={"insert_name": "Stitch in Costume"},
            reason="复核同一正反面原图，确认主题",
            checked_at="2026-09-25",
        )
    }
    before = deepcopy(item)
    r = evaluate(item, snapshot, reviews, fx, identity_reviews=recovery)
    assert r["status"] == "review_required"
    assert r["fair_value"] is None
    assert r["identity_corrections"]["insert_name"]["before"] == "Wrong OCR"
    assert item == before


def test_distinct_child_product_never_uses_parent_sale():
    item, snapshot, reviews, fx = fixture()
    item["product_name"] = "Topps Chrome Sapphire"
    snapshot["raw_result"]["identity"]["brand"] = "Topps Chrome Sapphire"
    r = evaluate(item, snapshot, reviews, fx)
    assert r["fair_value"] is None
    assert r["rejected_evidence"]


def test_explicit_identity_conflict_and_manual_override_are_preserved():
    item, snapshot, reviews, fx = fixture()
    snapshot["raw_result"]["identity_conflict"] = True
    assert evaluate(item, snapshot, reviews, fx)["status"] == "review_required"
    original = dict(id="manual", method="manual_override", fair_value=80, currency="CNY")
    assert evaluate(*fixture(), original=original)["fair_value"] == 80


def test_unconfirmed_source_identity_is_reference_only():
    item, snapshot, reviews, fx = fixture()
    for source in reviews.values():
        source["identity_confirmed"] = False
    assert evaluate(item, snapshot, reviews, fx)["fair_value"] is None


def test_missing_original_sample_cannot_be_jointly_validated():
    item, snapshot, _, fx = fixture()
    original = dict(
        id="v1",
        method="model",
        fair_value=80,
        currency="CNY",
        evidence={"used_comparables": [{"market_comp_id": "missing"}]},
    )
    r = evaluate(item, snapshot, {}, fx, original=original)
    assert r["status"] == "review_required"
    assert any(e.get("id") == "missing" for e in r["rejected_evidence"])


def test_human_confirmed_fields_cannot_be_filled_from_codex():
    item, snapshot, reviews, fx = fixture()
    item["_human_confirmed"] = True
    item["_confirmed_identity"] = {"serial_total": None}
    snapshot["raw_result"]["identity"]["serial_total"] = 25
    r = evaluate(item, snapshot, reviews, fx)
    assert r["status"] == "review_required"


def test_outdated_confirmed_identity_cannot_override_current_manual_fields():
    item, snapshot, reviews, fx = fixture()
    item["serial_total"] = 25
    item["_human_confirmed"] = True
    item["_confirmed_identity"] = {"serial_total": None}
    r = evaluate(item, snapshot, reviews, fx)
    assert r["status"] == "review_required"
    assert r["joint_identity"]["serial_total"] == 25


def test_tracked_ebay_urls_are_one_sale():
    item, snapshot, reviews, fx = fixture()
    duplicate = deepcopy(snapshot["evidence_summary"][0])
    duplicate["url"] = "https://www.ebay.com/itm/a-card-title/123456789012?hash=item123&var=0"
    snapshot["evidence_summary"].append(duplicate)
    result = evaluate(item, snapshot, reviews, fx)
    assert result["recovered_evidence"][0]["url"] == result["recovered_evidence"][1]["url"]
    assert len(result["accepted_evidence"]) == 0


def sports_fixture():
    item, snapshot, reviews, fx = fixture()
    identity = dict(
        player_name="Stephen Curry",
        season_start=2025,
        product_name="Topps Chrome",
        card_number="30",
        parallel_name="Base",
        is_autograph=False,
        is_memorabilia=False,
    )
    item.update(identity, insert_name=None)
    snapshot["raw_result"]["identity"] = identity
    for review in reviews.values():
        review.update(identity=identity, price=60, currency="CNY", sold_at="2026-09-25")
    original = dict(
        id="v1",
        method="model",
        fair_value=999,
        currency="CNY",
        evidence={"used_comparables": [{"market_comp_id": "c1"}]},
    )
    market = [
        dict(
            id="c1",
            listing_title="2025 Topps Chrome Stephen Curry #30 Base",
            sale_price=40,
            currency="CNY",
            occurred_at="2026-09-25",
            source_url="https://market.test/one",
            raw_payload={},
        )
    ]
    return item, snapshot, reviews, fx, original, market


def test_two_legacy_systems_cannot_replace_current_research_contract():
    item, snapshot, reviews, fx, original, market = sports_fixture()
    result = evaluate(item, snapshot, reviews, fx, original=original, market=market)
    assert result["status"] == "review_required"
    assert result["fair_value"] == 999
    assert result["accepted_evidence"] == []


def test_system_best_offer_unknown_price_is_not_accepted():
    item, snapshot, _, fx, original, market = sports_fixture()
    market[0]["raw_payload"] = {"sale_status": "best_offer_accepted", "actual_price_known": False}
    result = evaluate(item, snapshot, {}, fx, original=original, market=market)
    assert result["status"] == "review_required"
    assert result["fair_value"] == 999
    assert result["accepted_evidence"] == []
    assert "实际成交金额" in result["rejected_evidence"][0]["reasons"][0]


def test_cannot_validate_only_a_subset_of_original_used_sales():
    item, snapshot, _, fx, original, market = sports_fixture()
    original["evidence"]["used_comparables"].append({"market_comp_id": "missing"})
    result = evaluate(item, snapshot, {}, fx, original=original, market=market)
    assert result["status"] == "review_required"
    assert len(result["accepted_evidence"]) == 0
    assert result["rejected_evidence"][0]["id"] == "missing"


def test_market_comp_recall_ids_do_not_promote_unpublished_candidates():
    item, snapshot, _, fx, original, market = sports_fixture()
    original["evidence"] = {"market_comp_ids": ["c1"]}
    result = evaluate(item, snapshot, {}, fx, original=original, market=market)
    assert result["status"] != "joint_validated"
    assert result["accepted_evidence"] == []


def test_verified_sale_requires_fresh_fx_and_source_check():
    item, snapshot, reviews, fx = fixture()
    fx["date"] = "2020-01-01"
    assert evaluate(item, snapshot, reviews, fx)["fair_value"] is None
    item, snapshot, reviews, fx = fixture()
    for r in reviews.values():
        r["checked_at"] = "2020-01-01"
    assert evaluate(item, snapshot, reviews, fx)["fair_value"] is None


def test_same_sale_with_conflicting_reviewed_amount_is_not_jointly_validated():
    item, snapshot, reviews, fx, original, market = sports_fixture()
    market[0]["source_url"] = snapshot["evidence_summary"][0]["url"]
    result = evaluate(item, snapshot, reviews, fx, original=original, market=market)
    assert result["status"] == "review_required"
    assert result["fair_value"] == 999
    assert any("当前" in reason for e in result["rejected_evidence"] for reason in e["reasons"])


def test_system_duplicate_url_with_conflicting_prices_requires_review():
    item, snapshot, _, fx, original, market = sports_fixture()
    duplicate = dict(market[0], id="c2", sale_price=80)
    market.append(duplicate)
    original["evidence"]["used_comparables"].append({"market_comp_id": "c2"})
    result = evaluate(item, snapshot, {}, fx, original=original, market=market)
    assert result["status"] == "review_required"
    assert result["fair_value"] == 999
