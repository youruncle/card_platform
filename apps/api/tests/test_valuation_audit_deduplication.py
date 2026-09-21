from scripts import audit_cardhobby_valuation as audit


def test_missing_source_duplicate_never_displaces_verifiable_latest_source():
    good = {
        "id": "good",
        "external_id": "1",
        "source_url": "https://cardhobby.com.cn/Market/Item/1",
        "provider_raw_payload": {
            "item_id": "1",
            "title": "2024 Curry Prizm #1",
            "source_url": "https://cardhobby.com.cn/Market/Item/1",
            "occurred_at": "2026-01-01T00:00:00+08:00",
            "sale_price": "10",
            "bid_count": 1,
            "match_confidence": "0.95",
            "sale_type": "auction",
            "evidence_level": "ended_auction",
            "sold_status_evidence": "Status=-2",
        },
        "evidence_fetched_at": "2026-02-01T00:00:00+08:00",
    }
    missing = {
        **good,
        "id": "missing",
        "provider_raw_payload": None,
        "evidence_fetched_at": "2026-03-01T00:00:00+08:00",
    }
    assert audit.ordered_source_rows([missing, good])[0]["id"] == "good"
    assert audit.ordered_source_rows([good, missing]) == audit.ordered_source_rows([missing, good])


def test_latest_verifiable_version_wins_without_selecting_highest_price():
    from copy import deepcopy

    # A representative is chosen by observed version, not by its resulting price/tier.
    old = {
        "id": "old",
        "external_id": "2",
        "source_url": "https://cardhobby.com.cn/Market/Item/2",
        "provider_raw_payload": {
            "item_id": "2",
            "title": "card",
            "source_url": "https://cardhobby.com.cn/Market/Item/2",
            "occurred_at": "2026-01-01T00:00:00+08:00",
            "sale_price": "100",
            "bid_count": 1,
            "match_confidence": "0.95",
            "sale_type": "auction",
            "evidence_level": "ended_auction",
            "sold_status_evidence": "ended",
        },
        "evidence_fetched_at": "2026-01-01T00:00:00+08:00",
    }
    new = deepcopy(old)
    new.update(id="new", evidence_fetched_at="2026-02-01T00:00:00+08:00")
    new["provider_raw_payload"]["sale_price"] = "10"
    assert audit.ordered_source_rows([old, new])[0]["id"] == "new"
