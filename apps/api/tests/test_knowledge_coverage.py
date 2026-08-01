from card_platform.knowledge_api import coverage_counts


def test_coverage_counts_exposes_missing_sources_without_losing_status_counts() -> None:
    counts = coverage_counts(
        [
            {"coverage_status": "covered", "source_id": "source-1"},
            {"coverage_status": "pending_source", "source_id": None},
            {"coverage_status": "partial", "source_id": None},
        ]
    )

    assert counts == {
        "covered": 1,
        "pending_source": 1,
        "partial": 1,
        "source_missing": 2,
    }
