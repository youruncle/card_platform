"""A bound front can supply an omitted insert name, never a contradiction."""

from copy import deepcopy
import gzip
from hashlib import sha256
import json

import pytest

from test_player_price_catalog import catalog, fact
from test_player_price_image_evidence import fixture, visual


def insert_candidate(title, *, observed="Deep Space", front_proof=True):
    result = visual(
        player_name="Anthony Edwards",
        brand_name="Panini",
        product_name="Prizm",
        card_number="3",
        insert_name=observed,
        parallel_name="Silver Prizm",
        season_start=2024,
        season_end=2025,
    )
    for face in result["pair_validation"]["front"], result["pair_validation"]["back"]:
        face.update(
            player_names=["Anthony Edwards"],
            brand_name="Panini",
            product_name="Prizm",
            insert_name=observed if face["role"] == "front" else None,
            evidence=["Printed ANTHONY EDWARDS and PANINI PRIZM"],
        )
    result["pair_validation"]["back"]["card_number"] = "3"
    for evidence in result["physical_evidence"]:
        if evidence["field"] == "card_number":
            evidence.update(value="3", detail="Back top: independent collector number No. 3")
        if evidence["field"] == "parallel_name":
            evidence.update(
                value="Silver Prizm",
                detail="Front smooth rainbow foil / reverse separate PRIZM label",
            )
    if observed and front_proof:
        result["physical_evidence"].append(
            dict(
                field="insert_name",
                value=observed,
                image_index=0,
                detail="Front top printed DEEP SPACE, outside the player name / product logo",
            )
        )
    obs, receipt, blobs, _ = fixture(result, title=title)
    deep = fact(
        player="Anthony Edwards", card_number="3", insert="Deep Space", section="Deep Space"
    )
    deep["id"] = "official-deep-space-3"
    other = fact(player="Anthony Edwards", card_number="6", insert="Finalists", section="Finalists")
    other["id"] = "official-finalists-6"
    return obs, receipt, blobs, catalog([deep, other])


def admit(candidate):
    from card_platform.player_prices.image_evidence import admit_receipt

    obs, receipt, blobs, official = candidate
    return admit_receipt(obs, receipt, "Anthony Edwards", official, blob_reader=blobs.__getitem__)


@pytest.mark.parametrize("named", [False, True])
def test_bound_front_insert_name_can_resolve_an_omission_without_rewriting_source(named):
    title = "2024-25 Panini Prizm Anthony Edwards 银折" + (" Deep Space" if named else "")
    candidate = insert_candidate(title)
    original = deepcopy(candidate[0])
    decision = admit(candidate)
    assert decision["status"] == "confirmed", decision
    assert decision["identity"]["insert_name"] == "Deep Space"
    assert decision["identity"]["card_number"] == "3"
    assert candidate[0] == original
    if not named:
        assert (
            "标题未命中目标卡种，需图像确认"
            in decision["identity"]["image_evidence"]["resolved_title_conflicts"]
        )
        # A title-only discovery caller still has no authority to supply it.
        from card_platform.public_source_catalog import validated_sale

        sale, _ = validated_sale(original)
        assert candidate[3].confirm_title(decision["identity"], sale) is None


@pytest.mark.parametrize("declared", ["Finalists #3", "Base #3", "Deep Space Finalists #3"])
def test_an_explicit_different_source_kind_is_not_an_omitted_insert_name(declared):
    candidate = insert_candidate("2024-25 Panini Prizm Anthony Edwards 银折 " + declared)
    assert admit(candidate)["status"] == "rejected"


@pytest.mark.parametrize("observed,front_proof", [(None, False), ("Deep Space", False)])
def test_a_named_source_insert_cannot_replace_missing_independent_front_evidence(
    observed, front_proof
):
    candidate = insert_candidate(
        "2024-25 Panini Prizm Anthony Edwards Deep Space #3 银折",
        observed=observed,
        front_proof=front_proof,
    )
    assert admit(candidate)["status"] == "rejected"


def test_a_front_insert_name_still_requires_a_matching_published_official_card():
    candidate = insert_candidate(
        "2024-25 Panini Prizm Anthony Edwards 银折", observed="Unpublished Insert"
    )
    assert admit(candidate)["status"] == "rejected"


def test_a_known_other_kind_is_a_conflict_even_when_absent_from_this_release():
    candidate = list(insert_candidate("2024-25 Panini Prizm Anthony Edwards Finalists #3 银折"))
    candidate[3] = catalog(
        [fact(player="Anthony Edwards", card_number="3", insert="Deep Space", section="Deep Space")]
    )
    assert admit(candidate)["status"] == "rejected"


def test_conflicting_insert_vocabulary_survives_artifact_lru_eviction(tmp_path):
    from card_platform.player_prices.catalog import PriceCardCatalog

    candidate = list(
        insert_candidate("2024-25 Panini Prizm Anthony Edwards Deep Space Orbit Legends #3 银折")
    )
    rows = [
        fact(
            player="Anthony Edwards",
            card_number="6",
            insert="Orbit Legends",
            section="Orbit Legends",
        ),
        fact(player="Anthony Edwards", card_number="3", insert="Deep Space", section="Deep Space"),
        *[fact(player="Another Player", card_number=str(500 + i)) for i in range(7)],
    ]
    artifacts = []
    for i, row in enumerate(rows):
        row["id"] = f"official-artifact-card-{i}"
        raw = gzip.compress(json.dumps([row]).encode())
        path = f"scope-{i}.json.gz"
        (tmp_path / path).write_bytes(raw)
        artifacts.append(
            dict(
                path=path,
                checksum=sha256(raw).hexdigest(),
                brand="Panini",
                product="Prizm",
                season_start=2024,
                season_end=2025,
            )
        )
    candidate[3] = PriceCardCatalog(
        dict(version=21, entities=[], official_artifacts=artifacts), artifact_root=tmp_path
    )
    assert admit(candidate)["status"] == "rejected"


@pytest.mark.parametrize("separate", [False, True])
def test_a_nested_official_kind_name_is_one_declaration_unless_named_separately(separate):
    candidate = list(
        insert_candidate(
            "2024-25 Panini Prizm Anthony Edwards Rookie Penmanship 银折"
            + (" Penmanship" if separate else ""),
            observed="Rookie Penmanship",
        )
    )
    rows = [
        fact(
            player="Anthony Edwards",
            card_number="3",
            insert="Rookie Penmanship",
            section="Rookie Penmanship",
        ),
        fact(player="Another Player", card_number="6", insert="Penmanship", section="Penmanship"),
    ]
    rows[1]["id"] = "official-other-kind-6"
    candidate[3] = catalog(rows)
    assert admit(candidate)["status"] == ("rejected" if separate else "confirmed")
