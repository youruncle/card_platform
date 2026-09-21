from copy import deepcopy

import pytest

from card_platform.purchase_cost import score_match
from card_platform.purchase_cost_visual import purchase_visual_evidence, same_reviewed_identity


def fixture():
    card = dict(
        player_name="Example Player",
        brand_name="Topps",
        product_name="Flagship",
        season_start=2025,
        season_end=2026,
        card_number="157",
        parallel_name="Black and White",
        is_autograph=False,
        is_memorabilia=False,
        image_front_url="/uploads/front.jpg",
        image_back_url="/uploads/back.jpg",
    )
    line = dict(
        title="2025 Topps Flagship Example Player 黑白折 底片折 折射",
        item_id="123456789",
        item_url="https://cardhobby.com.cn/market/item/123",
        image_urls=["https://images.cardhobby.com/art.jpg"],
    )
    row = dict(
        id="decision",
        identity_version_id="version",
        decision="accepted",
        source_url="https://cardhobby.com.cn/Market/Item/123",
        review=dict(
            source="codex_paired_image_review",
            verdict="same",
            confidence=".95",
            target_front_url=card["image_front_url"],
            target_back_url=card["image_back_url"],
            candidate_image_url=line["image_urls"][0],
            candidate_source_url="https://cardhobby.com.cn/Market/Item/123",
            reasons=["paired-image same fixed printing; official name remains provisional"],
        ),
    )
    return card, line, row


def test_current_paired_review_allows_descriptive_parallel_not_market_price():
    card, line, row = fixture()
    evidence = purchase_visual_evidence(card, line, [row])
    result = score_match(card, line, visual_evidence=evidence)
    assert result["automatic"] and not result["missing"] and not result["conflicts"]
    assert result["visual_review"]["decision_id"] == "decision"
    assert result["visual_review"]["identity_version_id"] == "version"


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_image_url", "https://images.cardhobby.com/other.jpg"),
        ("target_front_url", "/uploads/replaced.jpg"),
        ("target_back_url", "/uploads/replaced-back.jpg"),
        ("candidate_source_url", "https://cardhobby.com.cn/Market/Item/124"),
        ("confidence", ".89"),
        ("verdict", "compatible"),
        ("source", "text_only_review"),
        ("reasons", []),
    ],
)
def test_stale_or_untrusted_review_cannot_fill_descriptive_parallel(field, value):
    card, line, row = fixture()
    row["review"][field] = value
    evidence = purchase_visual_evidence(card, line, [row])
    assert evidence is None
    assert not score_match(card, line, visual_evidence=evidence)["automatic"]


def test_rejected_same_asset_vetoes_positive_review_and_title():
    card, line, row = fixture()
    rejection = deepcopy(row)
    rejection["decision"] = "rejected"
    rejection["review"]["verdict"] = "different"
    evidence = purchase_visual_evidence(card, line, [row, rejection])
    result = score_match(card, line, visual_evidence=evidence)
    assert not result["automatic"] and "逐图复核确认非同款" in result["conflicts"]


@pytest.mark.parametrize("change", ["product", "year", "autograph", "number"])
def test_visual_review_never_overrides_title_hard_conflicts(change):
    card, line, row = fixture()
    line["title"] = {
        "product": line["title"].replace("Flagship", "Chrome"),
        "year": line["title"].replace("2025", "2024"),
        "autograph": line["title"] + " 球员亲笔签字",
        "number": line["title"] + " 卡号 158",
    }[change]
    result = score_match(card, line, visual_evidence=purchase_visual_evidence(card, line, [row]))
    assert not result["automatic"] and result["conflicts"]


@pytest.mark.parametrize("word", ["Chrome Negative", "Blackout", "底片折"])
def test_descriptive_alias_does_not_merge_other_finish_names(word):
    card, line, row = fixture()
    line["title"] = "2025 Topps Flagship Example Player " + word
    assert not score_match(card, line, visual_evidence=purchase_visual_evidence(card, line, [row]))[
        "automatic"
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("player_name", "Other Player"),
        ("parallel_name", "Gold"),
        ("card_number", "158"),
        ("is_autograph", True),
        ("season_end", 2027),
    ],
)
def test_live_identity_changes_invalidate_old_review(field, value):
    card, _, _ = fixture()
    identity = dict(card)
    assert same_reviewed_identity(card, identity)
    card[field] = value
    assert not same_reviewed_identity(card, identity)


def test_item_id_is_not_public_number_and_notes_are_not_visual_review():
    card, line, row = fixture()
    line["item_url"] = "https://cardhobby.com.cn/market/item/123456789"
    card["notes"] = line["item_url"]
    assert purchase_visual_evidence(card, line, [row]) is None
    assert not score_match(card, line)["automatic"]
