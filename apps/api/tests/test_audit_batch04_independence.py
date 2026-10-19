"""Batch-04 semantic regressions and controls; no private files or model fabrication.

These tests exercise real domain/worker functions. Structural fixtures are not
claimed to be observations from a live model or an admitted production sale.
Redemption regressions were observed failing before the targeted parser fix.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from card_platform.domain.redemption import redemption_conflict, redemption_identity
from card_platform.domain.sale_independence import full_serial, unique_physical_rows
from card_platform.valuation_worker import AutomatedValuationWorker, deduplicate_market_rows


def row(key, title):
    return dict(id=key, source_code="cardhobby", external_id=f"item:{key}",
                source_url=f"https://cardhobby.com.cn/Market/Item/{key}",
                listing_title=title, occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                source_reliability=Decimal(".7"), raw_payload={})


@pytest.mark.parametrize("title", [
    "2026 Topps Chrome Update Jalen Green Havoc Marks 交换板 只发板不发码",
    "2025-26 Topps Inception Khaman Maluach RJ-KM 交换板 发卡不发码",
    "2025-26 Topps Inception Khaman Maluach RJ-KM 交换板 只发卡不发码",
    "2026 Topps Chrome Update Jalen Green Havoc Marks 交换板 只发板",
    "Topps Inception RJ-KM Redemption code not included",
    "Topps Inception RJ-KM Redemption no code",
    "Topps Inception RJ-KM Redemption without redemption code",
])
def test_voucher_with_code_withheld_cannot_price_the_redemption_right(title):
    identity = dict(is_redemption=True, redemption_status="unknown")

    assert redemption_conflict(identity, title) is not None


def test_intact_coating_does_not_establish_unknown_backend_redemption_status():
    # Paraphrased visible evidence from object 498; contains no tracking/code text.
    assert redemption_identity(dict(is_redemption=True,
        notes="背面灰色涂层未刮，后台兑换状态未知")) == (True, "unknown")


@pytest.mark.parametrize("text", ["not redeemed", "未兑换", "未兑奖", "未刮，未兑换"])
def test_explicit_unredeemed_state_claim_is_preserved(text):
    assert redemption_identity(dict(is_redemption=True, notes=text)) == (True, "unredeemed")


def test_fulfilled_card_and_explicitly_unusable_voucher_are_rejected():
    identity = dict(is_redemption=True, redemption_status="unknown")

    assert redemption_conflict(identity, "Topps Inception RJ-KM signed card") == "redemption_form_conflict"
    assert redemption_conflict(identity, "Topps Inception RJ-KM 已兑换 空板") == "redemption_unusable_or_expired"


def test_same_31_of_49_signature_listing_is_one_other_copy():
    title = "2014-15 Panini Donruss Kyle Anderson Elite 蓝平行 签字 异形 31/49编"
    selected, excluded = unique_physical_rows([row("1", title), row("2", title)],
        identity=dict(serial_number=None, serial_total=49))

    assert len(selected) == 1
    assert excluded[0]["reason_code"] == "same_physical_card_serial"


def test_full_13_of_50_owned_history_does_not_remove_a_different_numerator():
    selected, excluded = unique_physical_rows([
        row("1", "2025-26 Topps Hoops Kon Knueppel Bounce House 金折 13/50编"),
        row("2", "2025-26 Topps Hoops Kon Knueppel Bounce House 金折 20/50编"),
    ], identity=dict(serial_number=13, serial_total=50))

    assert [r["id"] for r in selected] == ["2"]
    assert excluded[0]["reason_code"] == "owned_copy_history"


@pytest.mark.parametrize("title", [
    "2023-24 Panini Hoops Premium Stock Amen Thompson 变形虫折 199编",
    "2024 Topps Midnight Gradey Dick 新秀 折射 99编",
    "2025 Topps Bowman Chrome Shelton Henderson 蓝棋盘格折 签字 150编",
])
def test_total_only_actual_title_never_invents_a_full_physical_serial(title):
    assert full_serial(title) is None


def test_equal_serial_other_parallel_is_rejected_before_physical_copy_clustering():
    # Actual audit semantics: object 398 is smooth purple, candidate is purple wave.
    identity = dict(player_name="Cameron Johnson", product_name="Chrome Update",
        brand_name="Topps", season_start=2025, season_end=2026, card_number="69",
        parallel_name="Purple Refractor", serial_number=12, serial_total=75)
    candidate = row("141582318", "2026 Topps Chrome Update Cameron Johnson TCU 掘金 "
                    "卡梅隆 约翰逊 12/75编 紫波纹折 base 折射 限量 默认微瑕")
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    worker.image_verifier = None
    visual, facts = worker._current_visual_evidence(identity, candidate)
    classification = worker._classify_pricing_source(identity, candidate, visual, facts)

    assert classification.hard_conflicts
    # The production worker only sends admitted rows to independence clustering.
    selected, excluded = unique_physical_rows(
        [candidate] if not classification.hard_conflicts else [], identity)
    assert selected == [] and excluded == []


def test_same_marketplace_url_record_is_counted_once_across_external_prefixes():
    first = row("1", "2025 Chrome #153 Base")
    duplicate = {**first, "id": "another-held-card", "external_id": "cardhobby:item:1"}
    selected, excluded = deduplicate_market_rows([first, duplicate])

    assert len(selected) == len(excluded) == 1
    assert excluded[0]["reason"] == "same underlying marketplace sale"


def test_shared_seller_terms_and_background_hash_do_not_link_different_cards():
    rows = [row("1", "2025 Chrome #153"), row("2", "2025 Chrome #153")]
    for r in rows:
        r["raw_payload"] = dict(gallery_image_hashes=["a" * 64], image_role="terms",
                                background_sha256="b" * 64)
    selected, excluded = unique_physical_rows(rows)

    assert len(selected) == 2 and excluded == []


def test_different_public_psa_certificates_are_not_linked_by_company_and_grade():
    # Structural control: neither a common PSA9 label nor card design is a copy key.
    rows = [row("1", "2020-21 Panini Mosaic Luka Doncic Stained Glass PSA9"),
            row("2", "2020-21 Panini Mosaic Luka Doncic Stained Glass PSA9")]
    rows[0]["grade_cert_number"], rows[1]["grade_cert_number"] = "146848398", "157046676"
    for r in rows:
        r.update(grade_company="PSA", grade_value=Decimal("9"))
    selected, excluded = unique_physical_rows(rows)

    assert len(selected) == 2 and excluded == []


@pytest.mark.parametrize(("company", "grade"), [("PGC", "9"), ("BCTC", "10"),
                                                     ("BCTC", "9.5"), ("RFE", "9")])
def test_local_grader_is_not_interchangeable_with_psa_or_another_company(company, grade):
    identity = dict(grade_company=company, grade_value=Decimal(grade))
    def comp(grader):
        return SimpleNamespace(source_code="cardhobby", matching_fields=[],
                               grade_company=grader, grade_value=Decimal(grade))

    assert AutomatedValuationWorker._comparable_grade_matches(identity, comp(company))
    assert not AutomatedValuationWorker._comparable_grade_matches(identity, comp("PSA"))
    assert not AutomatedValuationWorker._comparable_grade_matches(identity, comp("PSS"))


@pytest.mark.parametrize('title', [
    'Topps Inception RJ-KM 未兑换 交换板 不是只发板，兑换码随卡附带',
    'Topps Inception RJ-KM 未兑换 交换板 并非只发板不发码，兑换码随卡附带',
    'Topps Inception RJ-KM Unredeemed Redemption not without redemption code',
    'Topps Inception RJ-KM Unredeemed Redemption no code visible',
    'Topps Inception RJ-KM Unredeemed Redemption no redemption code shown in photos',
])
def test_negated_withholding_and_hidden_code_do_not_assert_excluded_rights(title):
    assert redemption_conflict(dict(is_redemption=True, redemption_status='unredeemed'), title) is None
