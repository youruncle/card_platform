"""A source-specific approved role cannot erase another card/version claim."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from test_player_market import observation
from test_player_price_face_observations import compose, faces
from test_player_price_image_evidence import fixture, admit
from test_player_price_image_reviews import check, reviewed_fixture

MARKER = "【sample代拍】"
TITLE = "2025-26 Topps Chrome Stephen Curry 银折 " + MARKER + " #LG001"


def role_fixture(title=TITLE, *, raw_faces=None):
    obs, receipt, blobs, official = fixture(
        compose(raw_faces or faces()).model_dump(mode="json"), title=title
    )
    review = reviewed_fixture()[-1]
    start = title.rfind("#LG001")
    role = dict(
        contract="price-source-number-role-v1",
        code="LG001",
        seller_title_marker=MARKER,
        title_start=start,
        title_end=start + len("#LG001"),
        detail="Independently reviewed original source suffix as a seller batch label across these explicit multi-card listings; no global code exemption.",
        supporting_observations=[
            observation(201, f"2025 Panini Select LeBron James {MARKER} 2张lot #LG001"),
            observation(202, f"2025 Panini Prizm Kevin Durant {MARKER} 7张lot #LG001"),
            observation(203, f"2024 Panini Prizm Anthony Edwards {MARKER} 13张lot #LG001"),
        ],
    )
    review.update(
        based_on_receipt_sha256=receipt["receipt_sha256"],
        observation_sha256=obs["raw_row_sha256"],
        corrections=[],
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        source_number_role_review=role,
    )
    review["reviewed_images"] = [
        dict(
            image_index=i,
            sha256=image["sha256"],
            detail="Original whole face independently viewed; printed collector number remains on the back",
        )
        for i, image in enumerate(receipt["images"])
    ]
    return obs, receipt, blobs, official, review


def test_explicit_approved_source_role_resolves_only_this_inventory_suffix_without_changing_facts():
    obs, receipt, blobs, official, review = role_fixture()
    before = deepcopy((obs, receipt))
    assert admit(obs, receipt, blobs, official)["status"] == "rejected"
    assert check(obs, receipt, blobs, official, review, approved=False)["status"] == "rejected"
    decision = check(obs, receipt, blobs, official, review)
    assert decision["status"] == "confirmed", decision
    proof = decision["identity"]["image_evidence"]["review"]["source_number_role_review"]
    assert proof["code"] == "LG001" and proof["title_span"] == [TITLE.index("#LG001"), len(TITLE)]
    assert proof["supporting_source_ids"] == ["item:201", "item:202", "item:203"]
    assert (obs, receipt) == before
    assert decision["identity"]["card_number"] == "201"


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "target",
        "marker",
        "no_lots",
        "one_product",
        "optic_only",
        "single_card_lot",
        "ordinal",
        "ordinal_space",
        "negated",
        "changed_fact",
        "future_capture",
        "span",
        "code",
    ],
)
def test_source_role_requires_exact_bound_independent_support(change):
    values = list(role_fixture())
    role = values[-1]["source_number_role_review"]
    if change == "missing":
        role["supporting_observations"] = []
    elif change == "duplicate":
        role["supporting_observations"][1] = deepcopy(role["supporting_observations"][0])
    elif change == "target":
        role["supporting_observations"][0] = values[0]
    elif change == "marker":
        role["seller_title_marker"] = "【other代拍】"
    elif change in {"ordinal", "ordinal_space", "negated"}:
        wording = {"ordinal": "第2张lot", "ordinal_space": "第 2张lot", "negated": "不是2张lot"}[
            change
        ]
        role["supporting_observations"] = [
            observation(
                201 + i,
                f"2025 Panini {['Select', 'Prizm', 'Prizm'][i]} Curry {MARKER} {wording} #LG001",
            )
            for i in range(3)
        ]
    elif change in {"optic_only", "single_card_lot"}:
        role["supporting_observations"] = [
            observation(
                201 + i,
                f"2025 Panini {'Donruss Optic' if change == 'optic_only' else ['Select', 'Prizm', 'Prizm'][i]} Curry {MARKER} {'2张lot' if change == 'optic_only' else 'single card auction lot'} #LG001",
            )
            for i in range(3)
        ]
    elif change in {"no_lots", "one_product"}:
        role["supporting_observations"] = [
            observation(
                201 + i,
                f"2025 Panini Prizm Curry {MARKER} {'2张lot' if change == 'one_product' else 'base'} #LG001",
            )
            for i in range(3)
        ]
    elif change == "changed_fact":
        role["supporting_observations"][0]["sale"]["sale_price"] = "999"
    elif change == "future_capture":
        role["supporting_observations"][0]["captured_at"] = "2099-01-01T00:00:00+00:00"
    elif change == "span":
        role["title_start"] -= 1
    else:
        role["code"] = "LG002"
    values[-1]["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    assert check(*values)["status"] == "rejected"


@pytest.mark.parametrize(
    "suffix",
    [
        "卡号 #LG001",
        "No. #LG001",
        "card #LG001",
        "#LG001 其他文字",
        "#LG001 #XYZ002",
        "卡号=#LG001",
        "卡号为 #LG001",
        "Card number = #LG001",
        "No.=#LG001",
        "卡牌编号：#LG001",
        "卡片编号：#LG001",
        "卡面编号：#LG001",
        "No． #LG001",
        "Ｎｏ. #LG001",
    ],
)
def test_declared_collector_numbers_and_nonterminal_codes_cannot_be_reclassified(suffix):
    assert check(*role_fixture(TITLE[: TITLE.index("#LG001")] + suffix))["status"] == "rejected"


@pytest.mark.parametrize("claim", ["No.202", "卡号202", "Card number =202"])
def test_a_formal_number_declaration_elsewhere_in_the_title_still_blocks_role_review(claim):
    assert check(*role_fixture(TITLE.replace(MARKER, claim + " " + MARKER)))["status"] == "rejected"


@pytest.mark.parametrize("claim", ["#202", "RED REFRACTOR", "2张lot", "PSA10"])
def test_other_original_title_gates_remain_active_after_inventory_role_review(claim):
    title = TITLE[: TITLE.index(MARKER)] + claim + " " + TITLE[TITLE.index(MARKER) :]
    assert check(*role_fixture(title))["status"] == "rejected"


@pytest.mark.parametrize("change", ["confidence", "back_number", "trace"])
def test_source_role_does_not_supply_missing_physical_evidence(change):
    raw = faces()
    if change == "confidence":
        raw[0]["confidence"] = "0.87"
    elif change == "back_number":
        raw[1]["card_number"]["value"] = None
    values = list(role_fixture(raw_faces=raw))
    if change == "trace":
        values[2][values[1]["usage"]["observation_trace_sha256"]] = b"changed original response"
    assert check(*values)["status"] == "rejected"
