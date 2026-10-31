"""Captured public market research, separate from inventory and formal valuations."""

import asyncio
import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from typing import Literal
from uuid import UUID, uuid4

import httpx
from mcp.types import CallToolResult, ImageContent, TextContent
from psycopg.types.json import Jsonb
from pydantic import Field, field_validator

from card_platform.image_service import detect_image_content_type
from card_platform.ingestion.cardhobby import CardHobbyProvider, CARDHOBBY_SEARCH_URL
from card_platform.ingestion.cardhobby_public_item import parse_public_item_html
from card_platform.market_sales import normalize_row, build_report
from card_platform.mcp.captures import digest
from card_platform.mcp.media import AttachmentDownloader, ChatGPTFile
from card_platform.mcp.schemas import McpFailure, StrictModel

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.cardhobby.com.cn/market/search?ckey=0&keyword=",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/139.0 Safari/537.36",
}


class MarketIdentity(StrictModel):
    brand_name: str | None = Field(default=None, max_length=120)
    set_name: str | None = Field(default=None, max_length=160)
    is_numbered: bool | None = None
    is_patch: bool | None = None
    autograph_type: str | None = Field(default=None, max_length=100)
    memorabilia_type: str | None = Field(default=None, max_length=100)
    player_name: str | None = Field(default=None, max_length=160)
    product_name: str | None = Field(default=None, max_length=160)
    season: str | None = Field(default=None, max_length=40)
    card_number: str | None = Field(default=None, max_length=80)
    parallel_name: str | None = Field(default=None, max_length=120)
    serial_total: int | None = Field(default=None, ge=1, le=1000000)
    grade_company: str | None = Field(default=None, max_length=40)
    grade_value: str | None = Field(default=None, max_length=20)
    condition: str | None = Field(default=None, max_length=160)
    is_autograph: bool | None = None
    is_memorabilia: bool | None = None


class StartMarketRun(StrictModel):
    identity: MarketIdentity
    identity_notes: list[str] = Field(default_factory=list, max_length=20)
    files: list[ChatGPTFile] = Field(default_factory=list, max_length=2)
    idempotency_key: UUID


class GetMarketRun(StrictModel):
    run_id: UUID
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=60)
    capture_ref: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}\.(json|html|jpg)$")


class SearchMarketSales(StrictModel):
    run_id: UUID
    keyword: str = Field(min_length=1, max_length=120)
    page: int = Field(default=1, ge=1, le=5000)

    @field_validator("keyword")
    @classmethod
    def keyword_valid(cls, value):
        if not re.search(r"\w", value):
            raise ValueError("empty keyword")
        return value.strip()


class InspectMarketCandidate(StrictModel):
    run_id: UUID
    candidate_id: str = Field(pattern=r"^\d{1,40}$")
    image_index: int = Field(default=0, ge=0, le=19)


class CandidateReview(StrictModel):
    candidate_id: str = Field(pattern=r"^\d{1,40}$")
    evidence_digest: str = Field(min_length=1, max_length=128)
    classification: Literal["exact", "analog", "reject", "uncertain"]
    identity: MarketIdentity
    condition_comparable: bool = False
    lot: bool | None = None
    conflicts: list[str] = Field(default_factory=list, max_length=20)
    reasons: list[str] = Field(min_length=1, max_length=20)
    duplicate_of: str | None = Field(default=None, pattern=r"^\d{1,40}$")
    serial_number: str | None = Field(default=None, max_length=80)


class ReviewMarketSales(StrictModel):
    run_id: UUID
    reviews: list[CandidateReview] = Field(min_length=1, max_length=60)


class ListMarketRuns(StrictModel):
    limit: int = Field(default=20, ge=1, le=60)


class MarketSalesTools:
    def __init__(self, database, settings, operations, *, transport=None, downloader=None):
        self.database, self.settings, self.operations = database, settings, operations
        self.root = settings.resolved_upload_dir / ".cardhobby-market"
        self.transport = transport
        self.downloader = downloader or AttachmentDownloader()
        self._source_lock = asyncio.Lock()

    def directory(self, row):
        path = self.root / str(row["owner_id"]) / str(row["id"])
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def capture(self, row, body, suffix):
        name = f"{uuid4()}.{suffix}"
        path = self.directory(row) / name
        with path.open("xb") as stream:
            stream.write(body)
        path.chmod(0o600)
        return {"capture_ref": name, "sha256": hashlib.sha256(body).hexdigest()}

    def read_capture(self, row, entry):
        path = self.directory(row) / entry["capture_ref"]
        if path.is_symlink() or path.resolve().parent != self.directory(row).resolve():
            raise McpFailure("NOT_FOUND", "捕获不存在")
        try:
            body = path.read_bytes()
        except OSError:
            raise McpFailure("NOT_FOUND", "捕获不存在") from None
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise McpFailure("REVISION_CONFLICT", "捕获内容已变化")
        return body

    async def owned(self, conn, principal, run_id, *, lock=False):
        await self.operations._grant(conn, principal, frozenset({"cards:read"}))
        row = await (
            await conn.execute(
                "SELECT * FROM chatgpt_market_run WHERE id=%s AND owner_id=%s"
                + (" FOR UPDATE" if lock else ""),
                (run_id, principal.owner_id),
            )
        ).fetchone()
        if not row:
            raise McpFailure("NOT_FOUND", "市场研究任务不存在")
        return row

    def view(self, row, page=1, page_size=20):
        for entry in row["target_images"]:
            self.read_capture(row, entry)
        for candidate in row["candidates"]:
            if candidate.get("detail_capture"):
                self.read_capture(row, candidate["detail_capture"])
            for entry in candidate["images"].values():
                self.read_capture(row, entry)
        candidates = row["candidates"]
        offset = (page - 1) * page_size
        queries = row["queries"]
        latest = list({(q["keyword"], q["page"]): q for q in queries}.values())
        # Exhausted pages of one spelling never imply market-wide completeness.
        complete = (
            bool(latest)
            and all(q["status"] == "ok" for q in latest)
            and all(
                any(
                    p["keyword"] == q["keyword"] and p["page"] == n and p["status"] == "ok"
                    for p in latest
                )
                for q in latest
                for n in range(1, min(q.get("total_pages") or 1, 5000) + 1)
            )
        )
        return dict(
            run_id=str(row["id"]),
            identity=row["identity"],
            target_notes=row["target_notes"],
            target_images=row["target_images"],
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
            candidates=candidates[offset : offset + page_size],
            candidate_count=len(candidates),
            page=page,
            next_page=page + 1 if offset + page_size < len(candidates) else None,
            queries=queries,
            query_count=len(queries),
            request_budget=12,
            search_complete=complete,
            search_scope="本任务实际查询词及已抓取分页；不代表全站历史完整",
            report=build_report(
                row["identity"], candidates, row["reviews"], bool(row["target_images"])
            ),
            reviews=row["reviews"],
            ledger_modified=False,
            valuation_published=False,
        )

    async def start(self, principal, request):
        # Signed download URLs expire; idempotency binds stable file IDs and identity.
        material = request.model_dump(mode="json", exclude={"files"})
        material["file_ids"] = [f.file_id for f in request.files]
        request_hash = digest(material)
        async with self.database.connection() as conn:
            await self.operations._grant(conn, principal, frozenset({"cards:read"}))
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(principal.owner_id) + str(request.idempotency_key),),
            )
            prior = await (
                await conn.execute(
                    "SELECT * FROM chatgpt_market_run WHERE owner_id=%s AND idempotency_key=%s",
                    (principal.owner_id, request.idempotency_key),
                )
            ).fetchone()
            if prior:
                if (
                    prior["request_hash"] != request_hash
                    or prior["connection_id"] != principal.connection_id
                ):
                    raise McpFailure("IDEMPOTENCY_CONFLICT", "编号已用于其他目标或连接")
                return self.view(prior)
            run_id = uuid4()
            row = {"id": run_id, "owner_id": principal.owner_id}
            images = []
            for index, file in enumerate(request.files):
                body = await self.downloader.download(file.download_url)
                images.append(
                    {
                        **self.capture(row, body, "jpg"),
                        "input_index": index,
                        "file_id": file.file_id,
                    }
                )
            await self.operations._grant(conn, principal, frozenset({"cards:read"}))
            row = await (
                await conn.execute(
                    """INSERT INTO chatgpt_market_run
                (id,owner_id,connection_id,idempotency_key,request_hash,identity,target_notes,target_images)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (
                        run_id,
                        principal.owner_id,
                        principal.connection_id,
                        request.idempotency_key,
                        request_hash,
                        Jsonb(request.identity.model_dump(mode="json")),
                        Jsonb(request.identity_notes),
                        Jsonb(images),
                    ),
                )
            ).fetchone()
        return self.view(row)

    async def get(self, principal, run_id, page=1, page_size=20, capture_ref=None):
        async with self.database.connection() as conn:
            row = await self.owned(conn, principal, run_id)
        if capture_ref:
            entries = [*row["target_images"]]
            for q in row["queries"]:
                entries.extend(
                    q.get(k) for k in ("request_capture", "response_capture") if q.get(k)
                )
            for c in row["candidates"]:
                entries.extend(c["images"].values())
                if c.get("detail_capture"):
                    entries.append(c["detail_capture"])
            entry = next((e for e in entries if e["capture_ref"] == capture_ref), None)
            if not entry:
                raise McpFailure("NOT_FOUND", "捕获不属于本任务")
            body = self.read_capture(row, entry)
            if capture_ref.endswith(".jpg"):
                return CallToolResult(
                    content=[
                        ImageContent(
                            type="image",
                            data=base64.b64encode(body).decode(),
                            mimeType=detect_image_content_type(body),
                        )
                    ]
                )
            return {
                "capture_ref": capture_ref,
                "sha256": entry["sha256"],
                "body": body[:256000].decode(errors="replace"),
                "truncated": len(body) > 256000,
            }
        return self.view(row, page, page_size)

    async def fetch(self, url, *, params=None):
        async with self._source_lock:
            await asyncio.sleep(0.3)
            return await self._fetch(url, params=params)

    async def _fetch(self, url, *, params=None):
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=20,
            trust_env=False,
            follow_redirects=False,
            headers=HEADERS,
        ) as client:
            async with client.stream("GET", url, params=params) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 4 * 1024 * 1024:
                        raise ValueError("source_size_limit")
                return response.status_code, bytes(data)

    async def search(self, principal, request):
        qid = str(uuid4())
        async with self.database.connection() as conn:
            row = await self.owned(conn, principal, request.run_id, lock=True)
            matches = [
                q
                for q in row["queries"]
                if q["keyword"] == request.keyword and q["page"] == request.page
            ]
            if matches and matches[-1]["status"] == "ok":
                return self.view(row)
            if (
                matches
                and matches[-1]["status"] == "running"
                and (
                    datetime.now(timezone.utc) - datetime.fromisoformat(matches[-1]["checked_at"])
                ).total_seconds()
                < 60
            ):
                return self.view(row)
            if len(row["queries"]) >= 12:
                raise McpFailure(
                    "INVALID_INPUT",
                    "本任务已达到12页请求预算",
                    "回读并审核已抓取候选，明确搜索截断",
                )
            params = CardHobbyProvider.search_params(request.keyword, request.page, identity={})
            query = dict(
                query_id=qid,
                keyword=request.keyword,
                page=request.page,
                status="running",
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            query["request_capture"] = self.capture(
                row,
                json.dumps(
                    {"url": CARDHOBBY_SEARCH_URL, "headers": HEADERS, "params": params},
                    ensure_ascii=False,
                ).encode(),
                "json",
            )
            queries = [*row["queries"], query]
            await conn.execute(
                "UPDATE chatgpt_market_run SET queries=%s,updated_at=now() WHERE id=%s",
                (Jsonb(queries), row["id"]),
            )
        new = []
        try:
            status, body = await self.fetch(CARDHOBBY_SEARCH_URL, params=params)
            query["response_capture"] = self.capture(row, body, "json")
            query["http_status"] = status
            if status != 200:
                raise ValueError("http_failure")
            payload = json.loads(body)
            data = payload.get("data")
            if (
                payload.get("result") not in (1, "1", True)
                or not isinstance(data, dict)
                or not isinstance(data.get("PagedMarketItemList"), list)
            ):
                raise ValueError("invalid_search_response")
            rows = data["PagedMarketItemList"]
            if (
                data.get("PageIndex") is None
                or data.get("TotalCount") is None
                or int(data["PageIndex"]) != request.page
            ):
                raise ValueError("unbound_search_response")
            query.update(
                status="ok",
                raw_count=len(rows),
                total_count=int(data["TotalCount"]),
                total_pages=max(int(data.get("Total") or 0), (int(data["TotalCount"]) + 59) // 60),
            )
            for raw in rows[:60]:
                if not isinstance(raw, dict):
                    continue
                c = normalize_row(raw)
                if not c["candidate_id"].isdigit():
                    continue
                c["search_capture"] = query["response_capture"]
                c["row_sha256"] = digest(raw)
                c["evidence_digest"] = digest({"row": c["row_sha256"]})
                new.append(c)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
            query.update(status="failed", error_code="SOURCE_BLOCKED")
        async with self.database.connection() as conn:
            row = await self.owned(conn, principal, request.run_id, lock=True)
            queries = [query if q["query_id"] == qid else q for q in row["queries"]]
            candidates = {c["candidate_id"]: c for c in row["candidates"]}
            for c in new:
                if (
                    c["candidate_id"] not in candidates
                    or candidates[c["candidate_id"]]["row_sha256"] != c["row_sha256"]
                ):
                    candidates[c["candidate_id"]] = c
            row = await (
                await conn.execute(
                    "UPDATE chatgpt_market_run SET queries=%s,candidates=%s,updated_at=now() WHERE id=%s RETURNING *",
                    (Jsonb(queries), Jsonb(list(candidates.values())), row["id"]),
                )
            ).fetchone()
        return self.view(row)

    async def inspect(self, principal, request):
        async with self.database.connection() as conn:
            row = await self.owned(conn, principal, request.run_id)
        c = next((c for c in row["candidates"] if c["candidate_id"] == request.candidate_id), None)
        if c is None:
            raise McpFailure("NOT_FOUND", "候选不属于本次检索")
        original = c["row_sha256"]
        if not c.get("detail_capture"):
            try:
                status, body = await self.fetch(c["url"])
                if status != 200:
                    raise ValueError("detail_failed")
                parsed = parse_public_item_html(
                    body.decode(errors="replace"),
                    c["url"],
                    require_shipping=False,
                    bound_search_currency=c["currency"],
                )
            except (httpx.HTTPError, ValueError):
                raise McpFailure(
                    "SOURCE_BLOCKED", "商品详情暂时不可用", "稍后使用原run_id重试"
                ) from None
            c["detail_capture"] = self.capture(row, body, "html")
            sale = parsed.get("sale")
            meta = (sale or {}).get("source_metadata") or parsed.get("source_metadata") or {}
            c["gallery"] = meta.get("image_urls", [])
            c["detail_reasons"] = parsed.get("reasons", [])
            c["detail_verified"] = bool(
                sale
                and c["sale_eligible"]
                and Decimal(sale["sale_price"]) == Decimal(c["price"])
                and sale["bid_count"] == c["bid_count"]
                and datetime.fromisoformat(sale["occurred_at"])
                == datetime.fromisoformat(c["ended_at"])
            )
        if request.image_index >= len(c.get("gallery", [])):
            raise McpFailure(
                "INSUFFICIENT_EVIDENCE",
                "该商品没有所选卡图",
                "选择有效image_index；图片顺序不是正背标签",
            )
        key = str(request.image_index)
        if key not in c["images"]:
            url = c["gallery"][request.image_index]
            if not url.startswith("https://images.cardhobby.com/"):
                raise McpFailure("INVALID_INPUT", "商品图域名未获允许")
            body = await self.downloader.download(url)
            c["images"][key] = {**self.capture(row, body, "jpg"), "url": url}
        body = self.read_capture(row, c["images"][key])
        async with self.database.connection() as conn:
            row = await self.owned(conn, principal, request.run_id, lock=True)
            current = next(
                (v for v in row["candidates"] if v["candidate_id"] == request.candidate_id), None
            )
            if not current or current["row_sha256"] != original:
                raise McpFailure("REVISION_CONFLICT", "检索证据已更新", "重新查看候选")
            if (
                current.get("detail_capture")
                and current["detail_capture"]["sha256"] != c["detail_capture"]["sha256"]
            ):
                raise McpFailure(
                    "REVISION_CONFLICT",
                    "并发读取的商品详情已变化",
                    "重新读取当前详情，避免混合不同图库",
                )
            c["images"] = {**current["images"], **c["images"]}
            c["evidence_digest"] = digest(
                {"row": original, "detail": c["detail_capture"]["sha256"], "images": c["images"]}
            )
            choices = [
                c if v["candidate_id"] == c["candidate_id"] else v for v in row["candidates"]
            ]
            await conn.execute(
                "UPDATE chatgpt_market_run SET candidates=%s,updated_at=now() WHERE id=%s",
                (Jsonb(choices), row["id"]),
            )
        return CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(body).decode(),
                    mimeType=detect_image_content_type(body),
                ),
                TextContent(type="text", text=json.dumps(c, ensure_ascii=False)),
            ],
            structuredContent={
                "candidate": c,
                "image_index": request.image_index,
                "gallery_count": len(c["gallery"]),
                "warning": "实际商品图；顺序不等于正背，审核为模型判断。",
            },
        )

    async def review(self, principal, request):
        async with self.database.connection() as conn:
            row = await self.owned(conn, principal, request.run_id, lock=True)
            cs = {c["candidate_id"]: c for c in row["candidates"]}
            reviews = {r["candidate_id"]: r for r in row["reviews"]}
            for entry in row["target_images"]:
                self.read_capture(row, entry)
            for review in request.reviews:
                c = cs.get(review.candidate_id)
                if not c or review.evidence_digest != c["evidence_digest"]:
                    raise McpFailure("REVISION_CONFLICT", "审核未绑定当前候选证据")
                if review.classification in {"exact", "analog"} and not c["images"]:
                    raise McpFailure("INSUFFICIENT_EVIDENCE", "同款及类比判断须实际查看商品图")
                # Verify physical captures still exist before accepting any image-based judgment.
                for entry in c["images"].values():
                    self.read_capture(row, entry)
                reviews[review.candidate_id] = {
                    **review.model_dump(mode="json"),
                    "source": "conversation_model_image_review",
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                }
            row = await (
                await conn.execute(
                    "UPDATE chatgpt_market_run SET reviews=%s,updated_at=now() WHERE id=%s RETURNING *",
                    (Jsonb(list(reviews.values())), row["id"]),
                )
            ).fetchone()
        return self.view(row)

    async def recent(self, principal, request):
        async with self.database.connection() as conn:
            await self.operations._grant(conn, principal, frozenset({"cards:read"}))
            rows = await (
                await conn.execute(
                    "SELECT id,identity,created_at,updated_at FROM chatgpt_market_run WHERE owner_id=%s ORDER BY created_at DESC,id DESC LIMIT %s",
                    (principal.owner_id, request.limit),
                )
            ).fetchall()
        return {
            "runs": [
                dict(
                    run_id=str(r["id"]),
                    identity=r["identity"],
                    created_at=r["created_at"].isoformat(),
                    updated_at=r["updated_at"].isoformat(),
                )
                for r in rows
            ]
        }

    def register(self, registry):
        from card_platform.mcp.server import ToolAdapter

        async def get(principal, request):
            return await self.get(
                principal, request.run_id, request.page, request.page_size, request.capture_ref
            )

        for name, model, handler, description, readonly in (
            (
                "start_cardhobby_valuation",
                StartMarketRun,
                self.start,
                "创建独立卡淘成交研究，绑定图片和身份，不要求入库；无图片仅搜索，不估价。files接受ChatGPT附件。",
                False,
            ),
            (
                "search_cardhobby_sales",
                SearchMarketSales,
                self.search,
                "实际查询卡淘已售历史一页并保存原始证据；每页60条、每任务12页；关键词仅召回，不代表同款。",
                False,
            ),
            (
                "inspect_cardhobby_candidate",
                InspectMarketCandidate,
                self.inspect,
                "读取候选详情和实际卡图，返回内联图片、图片数量及审核摘要；image_index不代表正背。",
                False,
            ),
            (
                "review_cardhobby_candidates",
                ReviewMarketSales,
                self.review,
                "保存逐候选核图判断，校验当前证据摘要；三独立卖家、同款可比且全部审核后才形成卡淘参考区间。",
                False,
            ),
            (
                "get_cardhobby_valuation",
                GetMarketRun,
                get,
                "恢复本人卡淘研究、分页候选和估值报告；capture_ref可回读本任务原始搜索、详情或图片。",
                True,
            ),
            (
                "list_cardhobby_valuations",
                ListMarketRuns,
                self.recent,
                "跨聊天恢复本人最近卡淘市场研究；与本人买入成本查询独立。",
                True,
            ),
        ):
            meta = {"openai/fileParams": ["files"]} if name == "start_cardhobby_valuation" else None
            registry.register(
                ToolAdapter(
                    name,
                    description,
                    model,
                    frozenset({"cards:read"}),
                    handler,
                    read_only=readonly,
                    open_world=name in {"search_cardhobby_sales", "inspect_cardhobby_candidate"},
                    meta=meta,
                )
            )
