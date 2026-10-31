from io import BytesIO
import json
from uuid import uuid4

import httpx
from PIL import Image
import pytest

from card_platform.mcp.market_sales import (
    MarketSalesTools,
    StartMarketRun,
    SearchMarketSales,
    ReviewMarketSales,
    CandidateReview,
    InspectMarketCandidate,
)
from card_platform.mcp.operations import OperationService
from card_platform.mcp.schemas import McpFailure
from test_cardhobby_market_sales import identity, row

pytestmark = pytest.mark.asyncio


async def test_cloud_run_capture_idempotency_scope_and_real_inline_image(
    mcp_database, mcp_principal, tmp_path
):
    from card_platform.config import Settings
    from unittest.mock import AsyncMock
    from card_platform.mcp.media import ChatGPTFile

    buf = BytesIO()
    Image.new("RGB", (30, 40), "red").save(buf, format="JPEG")
    image = buf.getvalue()
    downloader = AsyncMock()
    downloader.download.return_value = image
    source_row = row()
    body = json.dumps(
        {
            "result": 1,
            "data": {
                "PageIndex": 1,
                "Total": 1,
                "TotalCount": 1,
                "PagedMarketItemList": [source_row],
            },
        }
    ).encode()

    def transport(request):
        assert request.url.host == "cardhobby.com.cn"
        assert request.headers["x-requested-with"] == "XMLHttpRequest"
        if request.url.path.lower().startswith("/market/item/"):
            html = f"""<div class="newcontitle">Curry Gold</div><img class="soldMark" src="/Content/img/sold.png">
              <table><tr><td>结束时间</td><td>{source_row["EffectiveDate"]}</td></tr>
              <tr><td><span id="currentPrice">100</span><div id="usdPrice">约$ 15</div></td></tr></table>
              <a href="/market/bidrecords/1"><span id="bidcount">3</span></a>
              <div id="layer-photos-demo"><img src="https://images.cardhobby.com/one.jpg"><img src="https://images.cardhobby.com/two.jpg"></div>"""
            return httpx.Response(200, text=html)
        return httpx.Response(200, content=body)

    tools = MarketSalesTools(
        mcp_database,
        Settings(upload_dir=str(tmp_path)),
        OperationService(mcp_database, "https://cardsp.cn"),
        transport=httpx.MockTransport(transport),
        downloader=downloader,
    )
    request = StartMarketRun(
        identity=identity(),
        files=[ChatGPTFile(file_id="target", download_url="https://example.com/target")],
        idempotency_key=uuid4(),
    )
    a = await tools.start(mcp_principal, request)
    assert (await tools.start(mcp_principal, request))["run_id"] == a["run_id"]
    with pytest.raises(McpFailure):
        await tools.start(
            mcp_principal,
            request.model_copy(
                update={"identity": request.identity.model_copy(update={"season": "2020-21"})}
            ),
        )
    result = await tools.search(
        mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="Curry Gold")
    )
    assert result["candidate_count"] == 1 and result["report"]["estimate"] is None
    assert result["queries"][0]["status"] == "ok"
    assert (
        await tools.search(
            mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="Curry Gold")
        )
    )["query_count"] == 1
    with pytest.raises(McpFailure):
        await tools.get(mcp_principal.model_copy(update={"owner_id": uuid4()}), a["run_id"])
    # A stale/imageless model assertion cannot promote a candidate.
    with pytest.raises(McpFailure):
        await tools.review(
            mcp_principal,
            ReviewMarketSales(
                run_id=a["run_id"],
                reviews=[
                    CandidateReview(
                        candidate_id="1",
                        evidence_digest="fake",
                        classification="exact",
                        identity=identity(),
                        condition_comparable=True,
                        lot=False,
                        reasons=["seen"],
                    )
                ],
            ),
        )
    inspected = await tools.inspect(
        mcp_principal, InspectMarketCandidate(run_id=a["run_id"], candidate_id="1")
    )
    assert inspected.content[0].type == "image"
    c = inspected.structuredContent["candidate"]
    assert c["detail_verified"] is True
    assert inspected.structuredContent["gallery_count"] == 2
    checked = CandidateReview(
        candidate_id="1",
        evidence_digest=c["evidence_digest"],
        classification="exact",
        identity=identity(),
        condition_comparable=True,
        lot=False,
        reasons=["front name and gold border"],
        serial_number="1/10",
    )
    saved = await tools.review(
        mcp_principal, ReviewMarketSales(run_id=a["run_id"], reviews=[checked])
    )
    assert saved["report"]["review_complete"] is True
    assert saved["report"]["estimate"] is None
    target = next(tmp_path.rglob(a["target_images"][0]["capture_ref"]))
    target.write_bytes(b"tampered")
    with pytest.raises(McpFailure):
        await tools.get(mcp_principal, a["run_id"])
    target.write_bytes(image)
    await tools.inspect(
        mcp_principal, InspectMarketCandidate(run_id=a["run_id"], candidate_id="1", image_index=1)
    )
    assert (await tools.get(mcp_principal, a["run_id"]))["report"]["review_complete"] is False
    with pytest.raises(McpFailure):
        await tools.review(mcp_principal, ReviewMarketSales(run_id=a["run_id"], reviews=[checked]))
    async with mcp_database.connection() as conn:
        await conn.execute(
            "UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s",
            (mcp_principal.connection_id,),
        )
    with pytest.raises(McpFailure):
        await tools.get(mcp_principal, a["run_id"])


async def test_empty_shell_is_failure_not_zero_results_and_request_is_captured(
    mcp_database, mcp_principal, tmp_path
):
    from card_platform.config import Settings

    shell = {
        "result": 1,
        "data": dict(
            Keyword=None,
            Total=None,
            TotalCount=None,
            PageIndex=None,
            StatusType=0,
            PagedMarketItemList=[],
        ),
    }
    tools = MarketSalesTools(
        mcp_database,
        Settings(upload_dir=str(tmp_path)),
        OperationService(mcp_database, "https://cardsp.cn"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=shell)),
    )
    a = await tools.start(
        mcp_principal, StartMarketRun(identity=identity(), idempotency_key=uuid4())
    )
    result = await tools.search(
        mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="Curry")
    )
    assert result["queries"][0]["status"] == "failed"
    assert result["search_complete"] is False
    assert list(tmp_path.rglob("*.json"))


async def test_concurrent_pages_merge_and_revoke_during_search_does_not_publish(
    mcp_database, mcp_principal, tmp_path
):
    import asyncio
    from card_platform.config import Settings

    async def handler(request):
        page = int(request.url.params["pageIndex"])
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={
                "result": 1,
                "data": {
                    "PageIndex": page,
                    "Total": 2,
                    "TotalCount": 2,
                    "PagedMarketItemList": [{**row(), "ID": page}],
                },
            },
        )

    tools = MarketSalesTools(
        mcp_database,
        Settings(upload_dir=str(tmp_path)),
        OperationService(mcp_database, "https://cardsp.cn"),
        transport=httpx.MockTransport(handler),
    )
    a = await tools.start(
        mcp_principal, StartMarketRun(identity=identity(), idempotency_key=uuid4())
    )
    await asyncio.gather(
        *(
            tools.search(
                mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="Curry", page=p)
            )
            for p in (1, 2)
        )
    )
    result = await tools.get(mcp_principal, a["run_id"])
    assert result["candidate_count"] == 2 and result["search_complete"] is True

    async def revoke(request):
        async with mcp_database.connection() as conn:
            await conn.execute(
                "UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s",
                (mcp_principal.connection_id,),
            )
        return await handler(request)

    tools.transport = httpx.MockTransport(revoke)
    with pytest.raises(McpFailure):
        await tools.search(mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="other"))
    async with mcp_database.connection() as conn:
        result = await (
            await conn.execute(
                "SELECT candidates FROM chatgpt_market_run WHERE id=%s", (a["run_id"],)
            )
        ).fetchone()
    assert len(result["candidates"]) == 2


@pytest.mark.parametrize("concurrent", [False, True])
async def test_inconsistent_detail_snapshot_and_incomplete_pagination(
    mcp_database, mcp_principal, tmp_path, concurrent
):
    import asyncio
    from card_platform.config import Settings
    from unittest.mock import AsyncMock

    source = row()
    calls = 0

    def handler(request):
        nonlocal calls
        if request.url.path.lower().startswith("/market/item/"):
            calls += 1
            tag = str(calls)
            bids = 3 if concurrent else 1
            return httpx.Response(
                200,
                text=f"""<div class="newcontitle">Curry Gold</div><img class="soldMark" src="/Content/img/sold.png">
              <table><tr><td>结束时间</td><td>{source["EffectiveDate"]}</td></tr><tr><td>￥<span id="currentPrice">100</span></td></tr></table>
              <a href="/market/bidrecords/1"><span id="bidcount">{bids}</span></a>
              <div id="layer-photos-demo"><img src="https://images.cardhobby.com/{tag}-one.jpg"><img src="https://images.cardhobby.com/{tag}-two.jpg"></div>""",
            )
        return httpx.Response(
            200,
            json={
                "result": 1,
                "data": {
                    "PageIndex": 1,
                    "Total": None,
                    "TotalCount": 120,
                    "PagedMarketItemList": [source],
                },
            },
        )

    buf = BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    downloader = AsyncMock()
    downloader.download.return_value = buf.getvalue()
    tools = MarketSalesTools(
        mcp_database,
        Settings(upload_dir=str(tmp_path)),
        OperationService(mcp_database, "https://cardsp.cn"),
        transport=httpx.MockTransport(handler),
        downloader=downloader,
    )
    a = await tools.start(
        mcp_principal, StartMarketRun(identity=identity(), idempotency_key=uuid4())
    )
    result = await tools.search(
        mcp_principal, SearchMarketSales(run_id=a["run_id"], keyword="Curry")
    )
    if not concurrent:
        assert result["search_complete"] is False
        c = await tools.inspect(
            mcp_principal, InspectMarketCandidate(run_id=a["run_id"], candidate_id="1")
        )
        assert c.structuredContent["candidate"]["detail_verified"] is False
    else:
        results = await asyncio.gather(
            *(
                tools.inspect(
                    mcp_principal,
                    InspectMarketCandidate(run_id=a["run_id"], candidate_id="1", image_index=i),
                )
                for i in (0, 1)
            ),
            return_exceptions=True,
        )
        assert any(isinstance(r, McpFailure) and r.code == "REVISION_CONFLICT" for r in results)
