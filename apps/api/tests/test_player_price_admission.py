"""Price rows require real bound evidence, even when a title looks exact."""

from copy import deepcopy

from test_player_price_image_evidence import fixture
from test_player_price_image_reviews import reviewed_fixture, archive


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
