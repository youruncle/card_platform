import json
from pathlib import Path


BENCHMARK = (
    Path(__file__).parents[3]
    / "data"
    / "benchmarks"
    / "valuation-ground-truth-20260821-showcase.json"
)
MODERN_BENCHMARK = BENCHMARK.with_name(
    "valuation-ground-truth-20260821-modern.json"
)
PORTFOLIO_BENCHMARK = BENCHMARK.with_name(
    "valuation-ground-truth-20260821-portfolio.json"
)


def test_showcase_ground_truth_is_complete_and_auditable() -> None:
    payload = json.loads(BENCHMARK.read_text())
    cases = payload["cases"]
    assert len(cases) == 8
    assert len({case["display_id"] for case in cases}) == 8
    for case in cases:
        identity = case["identity"]
        estimate = case["ground_truth"]
        assert identity["season_start"] == 2000
        assert identity["season_end"] == 2001
        assert identity["product_name"] == "Fleer Showcase"
        assert estimate["low"] <= estimate["fair"] <= estimate["high"]
        assert 0 <= estimate["confidence"] <= 1
        assert case["previous_failure"]


def test_ground_truth_evidence_never_uses_disallowed_titles() -> None:
    payload = json.loads(BENCHMARK.read_text())
    for case in payload["cases"]:
        for evidence in case["evidence"]:
            title = evidence["title"].casefold()
            assert "psa" not in title
            assert " lot " not in f" {title} "
            assert "打包" not in title


def test_modern_ground_truth_corrects_upstream_identity_failures() -> None:
    payload = json.loads(MODERN_BENCHMARK.read_text())
    cases = {case["display_id"]: case for case in payload["cases"]}
    assert set(cases) == {15, 16, 17, 18}
    assert cases[15]["identity"]["serial_total"] is None
    assert cases[16]["identity"]["parallel_name"] == "X-Fractor"
    assert cases[17]["identity"]["parallel_name"] == "Plaid"
    assert cases[18]["identity"]["card_number"] == "12"
    for case in cases.values():
        estimate = case["ground_truth"]
        assert estimate["low"] <= estimate["fair"] <= estimate["high"]
        assert case["checklist_source"].startswith("https://")


def test_portfolio_ground_truth_has_evidence_or_explicit_gap() -> None:
    payload = json.loads(PORTFOLIO_BENCHMARK.read_text())
    cases = payload["cases"]
    assert {case["display_id"] for case in cases} == set(range(21, 31))
    for case in cases:
        estimate = case["ground_truth"]
        assert estimate["low"] <= estimate["fair"] <= estimate["high"]
        assert case["evidence"] or case.get("evidence_gap")
        assert case["previous_failure"]
