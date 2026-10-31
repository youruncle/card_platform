from datetime import datetime, timezone, timedelta

import pytest

from card_platform.market_sales import normalize_row, build_report


def identity():
    return dict(
        brand_name="Panini",
        set_name="base",
        is_numbered=True,
        is_patch=False,
        autograph_type=None,
        memorabilia_type=None,
        player_name="Stephen Curry",
        product_name="Prizm",
        season="2022-23",
        card_number="30",
        parallel_name="Gold",
        serial_total=10,
        grade_company="raw",
        grade_value=None,
        condition="normal",
        is_autograph=False,
        is_memorabilia=False,
    )


def row(**changes):
    return dict(
        ID=1,
        Title="Curry Gold",
        Status=-2,
        ByWay=2,
        OutCount=3,
        LowestPrice=100,
        EffectiveDate=(datetime.now(timezone.utc) - timedelta(days=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        SellMemberID="s1",
        NewPostageMoney=None,
        **changes,
    )


def candidate(i):
    item = normalize_row({**row(), "ID": i, "SellMemberID": f"s{i}", "LowestPrice": i * 100})
    item.update(evidence_digest=str(i), images={"0": {"sha256": str(i)}}, detail_verified=True)
    return item


def review(i):
    return dict(
        candidate_id=str(i),
        evidence_digest=str(i),
        classification="exact",
        identity=identity(),
        condition_comparable=True,
        lot=False,
        conflicts=[],
        duplicate_of=None,
        serial_number=f"{i}/10",
        reasons=["paired image review"],
    )


def test_normalization_rejects_non_sales_and_preserves_unknown_shipping():
    assert normalize_row(row())["shipping"] is None
    for change in (
        {"OutCount": 0},
        {"Status": 1},
        {"ByWay": 1},
        {"LowestPrice": "NaN"},
        {"EffectiveDate": "2099-01-01 00:00:00"},
    ):
        assert normalize_row({**row(), **change})["sale_eligible"] is False


def test_three_sellers_required_and_prices_come_only_from_captured_candidates():
    cs = [candidate(i) for i in range(1, 4)]
    rs = [review(i) for i in range(1, 4)]
    report = build_report(identity(), cs, rs, True)
    assert report["estimate"] == {
        "low": "100",
        "median": "200",
        "high": "300",
        "currency": "CNY",
        "shipping_included": False,
    }
    assert report["payment_confirmed"] is False
    assert build_report(identity(), cs[:2], rs[:2], True)["estimate"] is None


def test_missing_target_image_identity_or_unreviewed_candidate_blocks_estimate():
    cs = [candidate(i) for i in range(1, 4)]
    rs = [review(i) for i in range(1, 4)]
    assert build_report(identity(), cs, rs, False)["estimate"] is None
    assert build_report({**identity(), "card_number": None}, cs, rs, True)["estimate"] is None
    assert build_report(identity(), cs, rs[:2], True)["review_complete"] is False


def test_null_estimate_explains_missing_images_review_progress_and_seller_threshold():
    cs = [candidate(i) for i in range(1, 4)]
    rs = [review(i) for i in range(1, 4)]
    report = build_report(identity(), cs, rs, False)
    assert {b["code"] for b in report["estimate_blockers"]} == {"target_images_missing"}
    report = build_report(identity(), cs[:2], rs[:2], True)
    blocker = next(
        b for b in report["estimate_blockers"] if b["code"] == "insufficient_independent_sellers"
    )
    assert blocker["observed"] == 2 and blocker["required"] == 3 and blocker["recovery"]
    report = build_report(identity(), cs, rs[:2], True)
    assert report["review_progress"] == {"total": 3, "current": 2, "pending": 1, "stale": 0}
    assert any(b["code"] == "candidate_review_incomplete" for b in report["estimate_blockers"])
    rs[2]["evidence_digest"] = "old"
    assert build_report(identity(), cs, rs, True)["review_progress"]["stale"] == 1


def test_null_estimate_distinguishes_identity_unknown_no_candidates_and_valid_success():
    cs = [candidate(i) for i in range(1, 4)]
    rs = [review(i) for i in range(1, 4)]
    report = build_report({**identity(), "parallel_name": None}, cs, rs, True)
    blocker = next(
        b for b in report["estimate_blockers"] if b["code"] == "target_identity_incomplete"
    )
    assert "parallel_name" in blocker["fields"]
    report = build_report(identity(), [], [], True)
    assert report["review_complete"] is True
    assert report["review_progress"]["total"] == 0
    assert any(b["code"] == "no_captured_candidates" for b in report["estimate_blockers"])
    assert build_report(identity(), cs, rs, True)["estimate_blockers"] == []


def test_unknown_numbering_subset_and_brand_cannot_form_exact_valuation():
    cs = [candidate(i) for i in range(1, 4)]
    rs = [review(i) for i in range(1, 4)]
    for field in ("brand_name", "set_name", "is_numbered"):
        assert build_report({**identity(), field: None}, cs, rs, True)["estimate"] is None


def test_wrong_parallel_stale_digest_same_physical_card_and_same_seller_are_excluded():
    for mode in ("parallel", "digest", "image", "serial", "seller", "condition"):
        cs = [candidate(i) for i in range(1, 4)]
        rs = [review(i) for i in range(1, 4)]
        if mode == "parallel":
            rs[2]["identity"]["parallel_name"] = "Gold Shimmer"
        if mode == "digest":
            rs[2]["evidence_digest"] = "old"
        if mode == "image":
            cs[2]["images"] = cs[1]["images"]
        if mode == "serial":
            rs[2]["serial_number"] = rs[1]["serial_number"]
        if mode == "seller":
            cs[2]["seller_id"] = cs[1]["seller_id"]
        if mode == "condition":
            rs[2]["condition_comparable"] = False
        assert build_report(identity(), cs, rs, True)["estimate"] is None, mode


def test_serial_leading_zero_dedup_and_unreadable_numbered_card_are_not_independent():
    cs = [candidate(i) for i in range(1, 4)]
    rs = [review(i) for i in range(1, 4)]
    rs[1]["serial_number"] = "02/10"
    rs[2]["serial_number"] = "2/10"
    assert build_report(identity(), cs, rs, True)["estimate"] is None

    rs[2]["serial_number"] = None
    assert build_report(identity(), cs, rs, True)["estimate"] is None


@pytest.mark.parametrize("foreign", ["USD", "CHF", "SGD", "KRW", "新台币", "원"])
def test_bound_search_cny_never_overrides_explicit_foreign_currency(foreign):
    from card_platform.ingestion.cardhobby_public_item import parse_public_item_html

    html = """<div class="newcontitle">Curry Gold</div><img class="soldMark" src="/Content/img/sold.png">
    <table><tr><td>结束时间</td><td>2026-10-01 10:00:00</td></tr>
    <tr><td>LABEL<span id="currentPrice">100</span><div id="usdPrice">约$ 15</div></td></tr></table>
    <a href="/market/bidrecords/1"><span id="bidcount">3</span></a>
    <div id="layer-photos-demo"><img src="https://images.cardhobby.com/one.jpg"></div>"""
    args = {"require_shipping": False}
    assert (
        parse_public_item_html(
            html.replace("LABEL", ""), "https://cardhobby.com.cn/market/item/1", **args
        )["sale"]
        is None
    )
    assert (
        parse_public_item_html(
            html.replace("LABEL", ""),
            "https://cardhobby.com.cn/market/item/1",
            bound_search_currency="CNY",
            **args,
        )["sale"]["sale_price"]
        == "100"
    )
    assert (
        parse_public_item_html(
            html.replace("LABEL", foreign),
            "https://cardhobby.com.cn/market/item/1",
            bound_search_currency="CNY",
            **args,
        )["sale"]
        is None
    )
