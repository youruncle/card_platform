"""Response and bounded-network contracts for the market fast path."""

import asyncio
from io import BytesIO
import json
from uuid import uuid4

import httpx
from PIL import Image
import pytest

from card_platform.config import Settings
from card_platform.mcp.market_sales import (
    CandidateReview,
    GetMarketRun,
    InspectMarketCandidate,
    MarketSalesTools,
    ReviewMarketSales,
    SearchMarketSales,
    StartMarketRun,
)
from card_platform.mcp.media import ChatGPTFile
from card_platform.mcp.operations import OperationService
from card_platform.mcp.schemas import McpFailure
from test_cardhobby_market_sales import identity, row

pytestmark = pytest.mark.asyncio


@pytest.fixture
def market(mcp_database, tmp_path):
    state = dict(source_calls=0, details=0, downloads=[], active=0, peak=0, fail=set())
    buffer = BytesIO()
    Image.new("RGB", (12, 16), "blue").save(buffer, "PNG")
    image = buffer.getvalue()

    async def download(url):
        state["downloads"].append(url)
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(0.02)
            if url in state["fail"]:
                raise McpFailure("SOURCE_BLOCKED", "temporary")
            return image
        finally:
            state["active"] -= 1

    def source(request):
        state["source_calls"] += 1
        if state.get("blocked"):
            return httpx.Response(200, json={"result": 1, "data": {}})
        if request.url.path.lower().startswith("/market/item/"):
            state["details"] += 1
            return httpx.Response(
                200,
                text=f"""<div class="newcontitle">Curry Gold</div>
                <img class="soldMark" src="/Content/img/sold.png"><table>
                <tr><td>结束时间</td><td>{row()["EffectiveDate"]}</td></tr>
                <tr><td>￥<span id="currentPrice">100</span></td></tr></table>
                <a href="/market/bidrecords/1"><span id="bidcount">3</span></a>
                <div id="layer-photos-demo"><img src="https://images.cardhobby.com/0.jpg">
                <img src="https://images.cardhobby.com/1.jpg"></div>""",
            )
        return httpx.Response(
            200,
            json={
                "result": 1,
                "data": {
                    "PageIndex": int(request.url.params.get("pageIndex", "1")),
                    "Total": 1,
                    "TotalCount": 60,
                    "PagedMarketItemList": [
                        {**row(), "ID": str(i + state.get("offset", 0))} for i in range(1, 61)
                    ],
                },
            },
        )

    class Downloader:
        async def download(self, url):
            return await download(url)

    tools = MarketSalesTools(
        mcp_database,
        Settings(upload_dir=str(tmp_path)),
        OperationService(mcp_database, "https://cardsp.cn"),
        transport=httpx.MockTransport(source),
        downloader=Downloader(),
    )
    return tools, state, image


async def test_compact_response_keeps_audit_and_current_digest(market, mcp_principal):
    tools, state, _ = market
    a = await tools.start(
        mcp_principal, StartMarketRun(identity=identity(), idempotency_key=uuid4())
    )
    r = await tools.search(
        mcp_principal,
        SearchMarketSales(run_id=a["run_id"], keyword="Curry Gold", response_mode="compact"),
    )
    assert r["response_mode"] == "compact"
    assert len(r["candidates"]) == 20 and r["next_page"] == 2
    assert all(c["evidence_digest"] for c in r["candidates"])
    full = await tools.get(mcp_principal, a["run_id"], page_size=60)
    saved = await tools.review(
        mcp_principal,
        ReviewMarketSales(
            run_id=a["run_id"],
            response_mode="compact",
            reviews=[
                CandidateReview(
                    candidate_id=c["candidate_id"],
                    evidence_digest=c["evidence_digest"],
                    classification="reject",
                    identity=identity(),
                    reasons=["different product " * 50],
                )
                for c in full["candidates"]
            ],
        ),
    )
    assert saved["report"]["review_progress"]["current"] == 60
    assert saved["report"]["estimate"] is None and "reviews" not in saved
    summary = await tools.get(mcp_principal, a["run_id"], summary_only=True)
    assert summary["candidate_count"] == 60 and not summary.get("candidates")
    full = await tools.get(mcp_principal, a["run_id"], page_size=60)
    assert len(json.dumps(saved)) < len(json.dumps(full)) / 3
    assert len(full["reviews"]) == 60
    page = await tools.get(mcp_principal, a["run_id"], page=2, page_size=20)
    assert {r["candidate_id"] for r in page["reviews"]} == {
        c["candidate_id"] for c in page["candidates"]
    }
    assert r["performance"]["elapsed_ms"] >= 0
    assert r["queries"][0]["performance"]["source_ms"] >= 0
    original = await tools.get(
        mcp_principal, a["run_id"], capture_ref=r["queries"][0]["response_capture"]["capture_ref"]
    )
    assert json.loads(original["body"])["result"] == 1
    assert GetMarketRun(run_id=a["run_id"], summary_only=True).summary_only


async def test_combined_start_is_idempotent_and_binds_keyword(market, mcp_principal):
    tools, state, _ = market
    req = StartMarketRun(
        identity=identity(),
        idempotency_key=uuid4(),
        keyword="Curry Gold",
        response_mode="compact",
        files=[
            ChatGPTFile(file_id="front", download_url="https://example.com/f.png"),
            ChatGPTFile(file_id="back", download_url="https://example.com/b.png"),
        ],
    )
    a = await tools.start(mcp_principal, req)
    assert a["query_count"] == 1 and len(a["target_images"]) == 2
    again = await tools.start(mcp_principal, req)
    assert again["run_id"] == a["run_id"] and state["source_calls"] == 1
    assert len(state["downloads"]) == 2
    with pytest.raises(McpFailure, match="编号"):
        await tools.start(mcp_principal, req.model_copy(update={"keyword": "other"}))


async def test_batch_images_are_parallel_labeled_and_retry_only_failures(market, mcp_principal):
    tools, state, image = market
    a = await tools.start(
        mcp_principal, StartMarketRun(identity=identity(), idempotency_key=uuid4())
    )
    await tools.search(mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="Curry"))
    state["fail"].add("https://images.cardhobby.com/1.jpg")
    req = InspectMarketCandidate(run_id=a["run_id"], candidate_id="1", image_indexes=[0, 1])
    partial = await tools.inspect(mcp_principal, req)
    assert partial.structuredContent["failed_image_indexes"] == [1]
    assert partial.structuredContent["returned_image_indexes"] == [0]
    assert state["peak"] == 2 and state["details"] == 1
    assert any(v.type == "image" for v in partial.content)
    old_digest = partial.structuredContent["candidate"]["evidence_digest"]
    state["fail"].clear()
    complete = await tools.inspect(mcp_principal, req)
    assert complete.structuredContent["returned_image_indexes"] == [0, 1]
    assert not complete.structuredContent["failed_image_indexes"]
    assert len([v for v in complete.content if v.type == "image"]) == 2
    assert state["downloads"].count("https://images.cardhobby.com/0.jpg") == 1
    assert state["details"] == 1
    assert complete.structuredContent["candidate"]["evidence_digest"] != old_digest
    with pytest.raises(McpFailure):
        await tools.review(
            mcp_principal,
            ReviewMarketSales(
                run_id=a["run_id"],
                reviews=[
                    CandidateReview(
                        candidate_id="1",
                        evidence_digest=old_digest,
                        classification="uncertain",
                        identity=identity(),
                        reasons=["old image snapshot"],
                    )
                ],
            ),
        )
    cached = await tools.inspect(mcp_principal, req)
    assert cached.structuredContent["performance"]["images_downloaded"] == 0
    assert cached.structuredContent["candidate"]["images"]["0"]["sha256"]
    async with tools.database.connection() as conn:
        await conn.execute(
            "UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s",
            (mcp_principal.connection_id,),
        )
    with pytest.raises(McpFailure):
        await tools.inspect(mcp_principal, req)


async def test_compact_search_cursor_preserves_query_selection(market, mcp_principal):
    tools, state, _ = market
    a = await tools.start(
        mcp_principal, StartMarketRun(identity=identity(), idempotency_key=uuid4())
    )
    await tools.search(mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="Curry"))
    state["offset"] = 60
    r = await tools.search(
        mcp_principal,
        SearchMarketSales(run_id=a["run_id"], keyword="Curry Gold", response_mode="compact"),
    )
    assert r["candidates"][0]["candidate_id"] == "61"
    params = r["next_page_args"]
    assert GetMarketRun(**params).query_id
    second = await tools.get(mcp_principal, **params)
    assert second["candidates"][0]["candidate_id"] == "81"
    assert second["selection_count"] == 60 and second["candidate_count"] == 120
    cached = await tools.search(
        mcp_principal,
        SearchMarketSales(run_id=a["run_id"], keyword="Curry", response_mode="compact"),
    )
    assert cached["queries"][0]["keyword"] == "Curry"
    with pytest.raises(McpFailure):
        await tools.get(mcp_principal, a["run_id"], query_id=uuid4())


async def test_combined_source_failure_keeps_recoverable_target(market, mcp_principal):
    tools, state, _ = market
    state["blocked"] = True
    request = StartMarketRun(
        identity=identity(), keyword="Curry", idempotency_key=uuid4(), response_mode="compact"
    )
    failed = await tools.start(mcp_principal, request)
    assert failed["queries"][0]["status"] == "failed"
    assert failed["report"]["estimate"] is None and not failed["search_complete"]
    state["blocked"] = False
    recovered = await tools.start(mcp_principal, request)
    assert recovered["run_id"] == failed["run_id"] and recovered["query_count"] == 2
    assert recovered["queries"][0]["status"] == "ok"


async def test_compact_positive_report_and_capture_integrity_are_unchanged(market, mcp_principal):
    from datetime import datetime, timezone
    from test_cardhobby_market_sales import candidate, review

    tools, _, image = market
    data = dict(
        id=uuid4(),
        owner_id=mcp_principal.owner_id,
        identity=identity(),
        target_notes=[],
        target_images=[],
        candidates=[],
        reviews=[],
        queries=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    data["target_images"] = [tools.capture(data, image, "jpg")]
    for i in range(1, 4):
        c = candidate(i)
        c["images"] = {"0": tools.capture(data, image + bytes([i]), "jpg")}
        data["candidates"].append(c)
        data["reviews"].append(review(i))
    full = tools.view(data)
    compact = tools.view(data, compact=True)
    assert full["report"]["estimate"] is not None
    assert compact["report"]["estimate"] == full["report"]["estimate"]
    path = tools.directory(data) / data["candidates"][2]["images"]["0"]["capture_ref"]
    path.write_bytes(b"tampered")
    with pytest.raises(McpFailure):
        tools.view(data, summary_only=True)


async def test_running_and_legacy_query_never_borrow_other_query_candidates(market, mcp_principal):
    from psycopg.types.json import Jsonb

    tools, state, _ = market
    a = await tools.start(
        mcp_principal, StartMarketRun(identity=identity(), idempotency_key=uuid4())
    )
    first = await tools.search(
        mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="first")
    )
    state["offset"] = 60
    await tools.search(mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="second"))
    async with tools.database.connection() as conn:
        data = await tools.owned(conn, mcp_principal, a["run_id"])
        queries = data["queries"]
        for q in queries:
            q.pop("candidate_ids", None)  # Pre-optimization persisted format.
        await conn.execute(
            "UPDATE chatgpt_market_run SET queries=%s WHERE id=%s", (Jsonb(queries), a["run_id"])
        )
    cached = await tools.search(
        mcp_principal,
        SearchMarketSales(run_id=a["run_id"], keyword="first", response_mode="compact"),
    )
    assert cached["selection_count"] == 60
    restored = await tools.get(
        mcp_principal, a["run_id"], query_id=first["queries"][0]["query_id"], page=3, page_size=20
    )
    assert {int(c["candidate_id"]) for c in restored["candidates"]} == set(range(41, 61))
    entered, release = asyncio.Event(), asyncio.Event()
    original_fetch = tools.fetch

    async def slow_fetch(url, *, params=None):
        entered.set()
        await release.wait()
        return await original_fetch(url, params=params)

    tools.fetch = slow_fetch
    running = asyncio.create_task(
        tools.search(
            mcp_principal,
            SearchMarketSales(run_id=a["run_id"], keyword="pending", response_mode="compact"),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        duplicate = await tools.search(
            mcp_principal,
            SearchMarketSales(run_id=a["run_id"], keyword="pending", response_mode="compact"),
        )
        assert duplicate["queries"][0]["status"] == "running"
        assert duplicate["selection_count"] == 0 and not duplicate["candidates"]
    finally:
        release.set()
        await running
