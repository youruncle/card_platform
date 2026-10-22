"""Price rows require real bound evidence, even when a title looks exact."""

from copy import deepcopy

import pytest

from test_player_price_image_evidence import fixture
from test_player_price_image_reviews import reviewed_fixture, archive


def bound_candidate(item_id, *, colours=("white", "gray"), day="2026-10-10", title_prefix=""):
    import hashlib
    from test_player_market import observation
    from test_player_price_image_evidence import HTML, FRONT, BACK, photo, visual
    from card_platform.player_prices.image_evidence import make_receipt
    from card_platform.public_source_catalog import observe_public_sale

    obs, _, _, official = fixture()
    obs = observation(item_id, title_prefix + obs["sale"]["title"])
    obs = observe_public_sale({**obs["raw_row"], "EffectiveDate": day + " 12:00:00"})
    html = HTML.replace("/123", "/" + str(item_id))
    images = [(FRONT, photo(colours[0]), "image/png"), (BACK, photo(colours[1]), "image/png")]
    blobs = {hashlib.sha256(data).hexdigest(): data for _, data, _ in images}
    blobs[hashlib.sha256(html.encode()).hexdigest()] = html.encode()
    receipt = make_receipt(obs, html, images, visual(), model="deepseek-flash", usage={})
    record = dict(
        source_id="item:" + str(item_id),
        observation_sha256=obs["raw_row_sha256"],
        receipt=receipt,
        receipt_sha256=receipt["receipt_sha256"],
        observation=obs,
        approved_review_sha256s=[],
    )
    return obs, record, blobs, official


def test_verified_historical_photo_peer_blocks_only_the_current_window_candidate():
    from card_platform.player_prices.admission import verified_price_rows

    current, record, blobs, official = bound_candidate(8802, day="2026-10-20")
    _, peer, stored, _ = bound_candidate(8801, day="2026-07-24")
    blobs.update(stored)
    record["photo_peers"] = [peer, peer]
    rows, coverage = verified_price_rows(
        [current], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert rows == []
    assert coverage["sample_count"] == 1
    assert coverage["rejection_reasons"] == {"reused_photo_pair": 1}
    # Repeated archived captures of the same item cannot create a second sale.
    record["photo_peers"] = [deepcopy(record)]
    rows, _ = verified_price_rows(
        [current], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert len(rows) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_photo_reuse_withdraws_old_sources_when_current_price_evidence_is_unusable(pending):
    from card_platform.player_prices.admission import verified_price_rows

    current, record, blobs, official = bound_candidate(8802, title_prefix="微瑕 ")
    _, anchor, stored, _ = bound_candidate(8802)
    blobs.update(stored)
    _, peer, stored, _ = bound_candidate(8801)
    blobs.update(stored)
    record["photo_peers"] = [{**peer, "photo_anchor": anchor}]
    if pending:
        record["receipt"] = record["receipt_sha256"] = None
    rows, coverage = verified_price_rows(
        [current], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert rows == []
    assert coverage["photo_reuse_sources"] == ["item:8801", "item:8802"]
    assert coverage["sample_count"] == coverage["rejected_count"] == 1
    reason = "image_evidence_pending" if pending else "image_evidence_rejected"
    assert coverage["rejection_reasons"] == {reason: 1}


def test_conflicting_current_source_still_withdraws_its_bound_historical_photo_peers():
    from card_platform.player_prices.admission import verified_price_rows

    first, record, blobs, official = bound_candidate(8802)
    conflicting, _, stored, _ = bound_candidate(8802, title_prefix="lot ")
    blobs.update(stored)
    _, peer, stored, _ = bound_candidate(8801)
    blobs.update(stored)
    record["photo_peers"] = [peer]
    rows, coverage = verified_price_rows(
        [first, conflicting],
        "Stephen Curry",
        official,
        evidence=[record],
        blob_reader=blobs.__getitem__,
    )
    assert rows == [] and coverage["rejection_reasons"] == {"conflicting_source": 1}
    assert coverage["photo_reuse_sources"] == ["item:8801", "item:8802"]


@pytest.mark.parametrize("missing", [False, True])
def test_unverifiable_historical_peer_does_not_silently_establish_independence(missing):
    import hashlib
    from test_player_price_image_evidence import HTML
    from card_platform.player_prices.admission import verified_price_rows

    current, record, blobs, official = bound_candidate(8802)
    _, peer, stored, _ = bound_candidate(8801)
    blobs.update(stored)
    sha = hashlib.sha256(HTML.replace("/123", "/8801").encode()).hexdigest()
    if missing:
        blobs.pop(sha)
    else:
        blobs[sha] = b"changed peer gallery bytes"
    record["photo_peers"] = [peer]
    rows, coverage = verified_price_rows(
        [current], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert rows == []
    assert coverage["sample_count"] == 1
    assert coverage["rejection_reasons"] == {"photo_independence_pending": 1}


def test_different_auction_ids_reusing_a_photo_pair_cannot_supply_independent_samples():
    from card_platform.player_prices.admission import verified_price_rows

    # Actual discovery found three LeBron and three Luka listings each reusing
    # exactly the same two photos. Keep their raw captures but do not choose an
    # arbitrary auction as the true sale or count them towards a trend.
    candidates = [
        bound_candidate(item_id, colours=colours)
        for item_id, colours in [
            (130311004, ("white", "gray")),
            (130311048, ("white", "gray")),
            (130311067, ("gray", "white")),
            (555, ("blue", "black")),
        ]
    ]
    blobs = {sha: data for _, _, stored, _ in candidates for sha, data in stored.items()}
    original = deepcopy([candidate[0] for candidate in candidates])
    for obs, evidence, _, official in candidates:
        single, _ = verified_price_rows(
            [obs], "Stephen Curry", official, evidence=[evidence], blob_reader=blobs.__getitem__
        )
        assert len(single) == 1  # Every receipt passes all its own identity gates.
    for selected in [candidates, list(reversed(candidates))]:
        rows, coverage = verified_price_rows(
            [c[0] for c in selected],
            "Stephen Curry",
            official,
            evidence=[c[1] for c in selected],
            blob_reader=blobs.__getitem__,
        )
        assert [row["source_id"] for row in rows] == ["item:555"]
        assert coverage["identified_count"] == 1 and coverage["sample_count"] == 4
        assert coverage["rejection_reasons"] == {"reused_photo_pair": 3}
    assert [candidate[0] for candidate in candidates] == original


def test_repeated_capture_of_one_auction_is_not_a_reused_photo_pair():
    from card_platform.player_prices.admission import verified_price_rows

    obs, evidence, blobs, official = bound_candidate(125)
    rows, coverage = verified_price_rows(
        [obs, obs], "Stephen Curry", official, evidence=[evidence], blob_reader=blobs.__getitem__
    )
    assert len(rows) == 1 and coverage["rejection_reasons"] == {}


def test_exact_title_without_a_server_receipt_cannot_enter_price_rows():
    from card_platform.player_prices.admission import verified_price_rows

    obs, _, blobs, official = fixture(title="2025-26 Topps Chrome Stephen Curry #201 Refractor")
    rows, coverage = verified_price_rows(
        [obs, obs], "Stephen Curry", official, evidence={}, blob_reader=blobs.__getitem__
    )
    assert rows == []
    assert coverage["sample_count"] == 1
    assert coverage["rejection_reasons"] == {"image_evidence_pending": 1}


def test_reviewed_row_uses_original_sale_facts_and_latest_server_approval():
    from card_platform.player_prices.admission import verified_price_rows

    obs, receipt, blobs, official, review = reviewed_fixture()
    sha = archive(blobs, review)
    record = dict(
        source_id="item:123",
        observation_sha256=obs["raw_row_sha256"],
        receipt=receipt,
        approved_review_sha256s=[sha],
    )
    rows, coverage = verified_price_rows(
        [obs], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert len(rows) == 1 and str(rows[0]["sale_price"]) == "100"
    assert rows[0]["identity"]["card_number"] == "201"
    assert rows[0]["identity"]["image_evidence"]["observation_method"] == "codex_reviewed_model"
    assert rows[0]["raw_title"] == obs["sale"]["title"]
    assert coverage["identified_count"] == 1
    # A later invalidated correction must not fall back to an earlier success.
    record["approved_review_sha256s"].append("f" * 64)
    rows, coverage = verified_price_rows(
        [obs], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert rows == []
    assert coverage["image_rejection_reasons"] == {"invalid_image_review": 1}


def test_changed_source_or_photo_cannot_reuse_an_old_receipt():
    from card_platform.player_prices.admission import verified_price_rows
    from card_platform.public_source_catalog import observe_public_sale

    obs, receipt, blobs, official = fixture()
    record = dict(
        source_id="item:123",
        observation_sha256=obs["raw_row_sha256"],
        receipt=receipt,
        approved_review_sha256s=[],
    )
    changed = observe_public_sale({**obs["raw_row"], "LowestPrice": 999})
    rows, coverage = verified_price_rows(
        [changed], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert rows == [] and coverage["rejection_reasons"] == {"image_evidence_pending": 1}
    # All variants of conflicting real sale facts remain excluded.
    rows, coverage = verified_price_rows(
        [obs, changed], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert rows == [] and coverage["rejection_reasons"] == {"conflicting_source": 1}
    damaged = deepcopy(blobs)
    damaged[receipt["images"][0]["sha256"]] = b"changed image"
    rows, coverage = verified_price_rows(
        [obs], "Stephen Curry", official, evidence=[record], blob_reader=damaged.__getitem__
    )
    assert rows == [] and coverage["image_rejection_reasons"] == {"invalid_image_receipt": 1}


def test_nested_alias_is_one_subject_but_an_independent_second_name_is_rejected():
    from test_player_market import observation
    from card_platform.player_prices.admission import queue_candidates, source_candidates

    single = observation(125, "2025-26 Topps Chrome 小勒布朗詹姆斯 #201 Refractor")
    multi = observation(126, "2025-26 Topps Chrome 小勒布朗詹姆斯 LeBron James #201 Refractor")
    candidates, _, _ = source_candidates([single, multi])
    assert queue_candidates(candidates, "Bronny James") == [single]


def test_sealed_product_search_hits_are_not_single_card_image_jobs():
    from test_player_market import observation
    from card_platform.player_prices.admission import queue_candidates, source_candidates

    titles = [
        "2025-26 Topps Hoops 单包 散包 寻找詹姆斯库里杜兰特 弗拉格新秀年 随机发 第一包",
        "2025-26 Topps Chrome Update TCU Basketball Mega 原盒 一盒6包 一包七张卡 Cooper Flagg LeBron James 第一盒",
    ]
    for title in titles:
        candidates, _, _ = source_candidates([observation(125, title)])
        assert queue_candidates(candidates, "Cooper Flagg") == []
    single = observation(127, "2025-26 Topps Chrome Cooper Flagg #251 Refractor 原盒直出")
    candidates, _, _ = source_candidates([single])
    assert queue_candidates(candidates, "Cooper Flagg") == [single]
