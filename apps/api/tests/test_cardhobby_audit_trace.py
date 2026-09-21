import httpx
import pytest
from card_platform.ingestion.cardhobby import CardHobbyProvider
from card_platform.valuation_trace import trace_run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,error,status",
    [
        ({"data": {"PagedMarketItemList": []}}, None, "empty"),
        (
            {
                "data": {
                    "PagedMarketItemList": [None, {}, {"Status": -2, "ByWay": 2, "OutCount": 0}]
                }
            },
            None,
            "succeeded",
        ),
        (None, httpx.ReadTimeout("private request"), "failed"),
    ],
)
async def test_page_and_every_rejected_row_are_traceable(monkeypatch, payload, error, status):
    provider = CardHobbyProvider(None, "unused")
    monkeypatch.setattr(provider, "build_query_plan", lambda identity: [("fixture", "库里")])

    async def fetch(*args):
        if error:
            raise error
        return payload

    monkeypatch.setattr(provider, "_get_search_payload", fetch)
    events = []
    with trace_run(events.append):
        result = await provider._search_direct({"player_name": "Stephen Curry"})
    pages = [e for e in events if e.get("event_kind") == "search_page"]
    assert len(pages) == 1
    assert pages[0]["status"] == status
    assert pages[0]["elapsed_ms"] >= 0
    assert pages[0]["page"] == 1
    if payload:
        assert pages[0]["raw_payload"] == payload
        rows = payload["data"]["PagedMarketItemList"]
        rejected = [
            e for e in events if e.get("event_kind") == "candidate" and e["status"] == "rejected"
        ]
        assert len(rejected) == len(rows)
        assert all(e["reason_codes"] for e in rejected)
    else:
        assert pages[0]["error_type"] == "ReadTimeout"
        assert "private request" not in str(pages)
    assert result.sales == []
