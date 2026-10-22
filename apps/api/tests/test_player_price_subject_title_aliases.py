"""An independently verified ordinary card can disambiguate a player's nickname."""

from copy import deepcopy

import pytest

from test_player_price_catalog import catalog, fact
from test_player_price_image_evidence import fixture, visual


def candidate(title, *, player="Victor Wembanyama", parallel="Base"):
    result = visual(
        player_name=player,
        brand_name="Panini",
        product_name="Prizm",
        card_number="136",
        parallel_name=parallel,
        season_start=2023,
        season_end=2024,
    )
    for face in result["pair_validation"]["front"], result["pair_validation"]["back"]:
        face.update(
            player_names=[player],
            brand_name="Panini",
            product_name="Prizm",
            evidence=[f"Printed {player} and PANINI PRIZM"],
        )
    result["pair_validation"]["back"]["card_number"] = "136"
    for row in result["physical_evidence"]:
        if row["field"] == "card_number":
            row.update(value="136", detail="Back independent collector No. 136")
        if row["field"] == "parallel_name":
            row.update(
                value=parallel, detail="Complete front plain metallic stock / unmarked reverse"
            )
    obs, receipt, blobs, _ = fixture(result, title=title)
    official = catalog([fact(player=player, season_start=2023, season_end=2024, card_number="136")])
    return obs, receipt, blobs, official


def admit(c):
    from card_platform.player_prices.image_evidence import admit_receipt

    obs, receipt, blobs, official = c
    return admit_receipt(
        obs, receipt, receipt["result"]["player_name"], official, blob_reader=blobs.__getitem__
    )


@pytest.mark.parametrize("suffix", ["斑马", "斑马 外星人"])
def test_bound_plain_prizm_base_resolves_isolated_wemby_nicknames(suffix):
    c = candidate("2023-24 Panini Prizm Victor Wembanyama 文班亚马 " + suffix + " 正Base #136")
    before = deepcopy(c[0])
    result = admit(c)
    assert result["status"] == "confirmed", result
    assert result["identity"]["image_evidence"]["resolved_subject_aliases"] == suffix.split()
    assert c[0] == before


@pytest.mark.parametrize(
    "claim", ["斑马纹", "斑马折", "Zebra", "外星天才", "Extraterrestrial Talent", "斑马 Zebra"]
)
def test_explicit_parallel_and_insert_claims_remain_conflicts(claim):
    assert (
        admit(candidate("2023-24 Panini Prizm Victor Wembanyama 正Base #136 " + claim))["status"]
        == "rejected"
    )


def test_nickname_disambiguation_requires_the_explicit_base_context():
    assert (
        admit(candidate("2023-24 Panini Prizm Victor Wembanyama 斑马 #136"))["status"] == "rejected"
    )


def test_another_player_cannot_borrow_wembys_nickname_context():
    assert (
        admit(
            candidate("2023-24 Panini Prizm Stephen Curry 正Base #136 斑马", player="Stephen Curry")
        )["status"]
        == "rejected"
    )


@pytest.mark.parametrize(
    "claim",
    [
        "斑马 折",
        "斑马 纹",
        "斑马 / 折",
        "斑马 版",
        "版本: 斑马",
        "版别：斑马",
        "外星人 特卡",
        "外星人 插入",
        "卡种：外星人",
        "version: 斑马",
    ],
)
def test_spacing_or_a_kind_label_cannot_turn_a_version_claim_into_a_nickname(claim):
    assert (
        admit(candidate("2023-24 Panini Prizm Victor Wembanyama 正Base #136 " + claim))["status"]
        == "rejected"
    )


@pytest.mark.parametrize(
    "claim",
    [
        "外星人insert",
        "外星人version",
        "insert外星人",
        "外星人 特 卡",
        "外星人 插 入",
        "外星人 特/卡",
    ],
)
def test_chinese_latin_boundaries_and_spaced_kind_words_keep_the_conflict(claim):
    assert (
        admit(candidate("2023-24 Panini Prizm Victor Wembanyama 正Base #136 " + claim))["status"]
        == "rejected"
    )


@pytest.mark.parametrize("claim", ["（外星人）", "【外星人】"])
def test_punctuation_does_not_supply_missing_base_context(claim):
    assert (
        admit(candidate("2023-24 Panini Prizm Victor Wembanyama #136 " + claim))["status"]
        == "rejected"
    )
