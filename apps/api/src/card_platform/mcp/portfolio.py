"""Owner-scoped adapters over the existing search, projection and ledger."""

from collections import defaultdict
from decimal import Decimal
import hashlib
from types import SimpleNamespace
from uuid import UUID

from pydantic import Field

from card_platform.mcp.schemas import McpFailure, StrictModel
from card_platform.portfolio_search import SearchFilters, search_item_ids
from card_platform.portfolio_sort import SortBy, SortOrder, sorted_page
from card_platform.repository import NotFoundError, PortfolioRepository


class SearchCardsRequest(StrictModel):
    filters: SearchFilters = Field(default_factory=SearchFilters)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=100)
    sort_by: SortBy | None = None
    order: SortOrder = "desc"


class GetCardDetailRequest(StrictModel):
    item_id: UUID


async def card_revision(conn, owner_id: UUID, item_id: UUID, *, lock=False, include_deleted=False):
    deleted = "" if include_deleted else "AND ci.deleted_at IS NULL"
    if lock:
        # A SELECT's MVCC snapshot is acquired before it waits for a row lock.
        # Acquire locks first, then use a NEW statement snapshot for the ledger
        # and identity history; otherwise a just-committed transaction is missed.
        found = await (await conn.execute(
            f"""SELECT ci.id FROM collection_item ci
                JOIN card_definition cd ON cd.id=ci.card_definition_id
                JOIN card_set cs ON cs.id=cd.card_set_id JOIN player p ON p.id=cd.player_id
                JOIN brand b ON b.id=cs.brand_id
                WHERE ci.id=%s AND ci.owner_id=%s {deleted} FOR UPDATE OF ci,cd,cs,p,b""",
            (item_id, owner_id),
        )).fetchone()
        if found is None:
            raise McpFailure("NOT_FOUND", "卡片不存在或无权访问", "重新查询卡片编号")
    row = await (await conn.execute(
        f"""SELECT jsonb_build_array(to_jsonb(ci),to_jsonb(cd),to_jsonb(cs),to_jsonb(p),to_jsonb(b),
          COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id)
                    FROM card_transaction t WHERE t.collection_item_id=ci.id),'[]'::jsonb),
          COALESCE((SELECT jsonb_agg(to_jsonb(v) ORDER BY v.version)
                    FROM card_identity_version v WHERE v.collection_item_id=ci.id),'[]'::jsonb),
          COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY s.id)
                    FROM valuation_snapshot s WHERE s.collection_item_id=ci.id),'[]'::jsonb))::text AS state
          FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id
          JOIN card_set cs ON cs.id=cd.card_set_id JOIN player p ON p.id=cd.player_id
          JOIN brand b ON b.id=cs.brand_id
          WHERE ci.id=%s AND ci.owner_id=%s {deleted}""", (item_id, owner_id)
    )).fetchone()
    if row is None:
        raise McpFailure("NOT_FOUND", "卡片不存在或无权访问", "重新查询卡片编号")
    return hashlib.sha256(row["state"].encode()).hexdigest()


PUBLIC_FIELDS = frozenset({
    "id", "display_id", "holding_status", "player_name", "brand_name", "season_start", "season_end",
    "product_name", "set_name", "card_number", "insert_name", "parallel_name", "is_rookie", "is_autograph",
    "autograph_type", "has_inscription", "inscription_text", "is_memorabilia", "memorabilia_types",
    "memorabilia_usage", "memorabilia_player", "is_patch", "is_numbered", "serial_number", "serial_total",
    "grade_company", "grade_value", "strategy_tags", "quantity_held", "remaining_cost", "purchase_price",
    "purchase_total_cost", "purchase_currency", "realized_profit", "valuation_currency", "valuation_low",
    "fair_value", "valuation_high", "unrealized_profit", "valuation_status", "recognition_confidence",
    "recognition_status", "recognition_quality_issues", "codex_valuation_currency", "codex_fair_value",
})


def public_card(item):
    result = item.model_dump(mode="json", include=PUBLIC_FIELDS)
    result["images"] = {"front": bool(item.image_front_url), "back": bool(item.image_back_url)}
    return result


class PortfolioTools:
    def __init__(self, database):
        self.database = database
        self.repository = PortfolioRepository()

    async def search_cards(self, principal, request: SearchCardsRequest):
        async with self.database.connection() as conn:
            owner = await (await conn.execute("SELECT base_currency FROM app_user WHERE id=%s",
                                             (principal.owner_id,))).fetchone()
            if owner is None:
                raise McpFailure("AUTH_REQUIRED", "账户不存在")
            candidates = (await search_item_ids(conn, principal.owner_id, request.filters)
                          if request.filters.model_dump(exclude_defaults=True) else None)
            sort_by = request.sort_by or ("relevance" if request.filters.q else "created_at")
            page = await sorted_page(conn, self.repository,
                SimpleNamespace(id=principal.owner_id, base_currency=owner["base_currency"]),
                filters=request.filters, candidate_ids=candidates, sort_by=sort_by, order=request.order,
                page=request.page, page_size=request.page_size, snapshot=False, snapshot_token=None)
            items = []
            for item in page.items:
                row = public_card(item)
                row["revision"] = await card_revision(conn, principal.owner_id, item.id)
                items.append(row)
        return {"items": items, "total": page.total, "page": page.page, "page_size": page.page_size,
                "next_page": page.page + 1 if page.page < page.total_pages else None,
                "sort": {"by": sort_by, "order": request.order},
                "identity_warning": "搜索别名仅用于发现，修改前核对实卡编号、图片和版本"}

    async def get_card_detail(self, principal, request: GetCardDetailRequest):
        async with self.database.connection() as conn:
            revision = await card_revision(conn, principal.owner_id, request.item_id)
            try:
                detail = await self.repository.item_detail(conn, principal.owner_id, request.item_id)
            except NotFoundError:
                raise McpFailure("NOT_FOUND", "卡片不存在") from None
        return {"item": public_card(detail.item), "revision": revision,
                "transactions": [row.model_dump(mode="json") for row in detail.transactions],
                "valuations": [row.model_dump(mode="json") for row in detail.valuations],
                "research_context": "上述估值为历史记录，valued_at为原研究日期；本次没有发起新检索"}

    async def get_portfolio_summary(self, principal, request):
        async with self.database.connection() as conn:
            items = await self.repository.list_items(conn, principal.owner_id)
            owner = await (await conn.execute("SELECT base_currency FROM app_user WHERE id=%s",
                                             (principal.owner_id,))).fetchone()
        if owner is None:
            raise McpFailure("AUTH_REQUIRED", "账户不存在")
        held = [item for item in items if item.holding_status == "held"]
        known = defaultdict(lambda: Decimal("0"))
        unknown = 0
        for item in held:
            if item.fair_value is None or not item.valuation_currency:
                unknown += 1
            else:
                known[item.valuation_currency] += item.fair_value * item.quantity_held
        complete_base = unknown == 0 and all(currency == owner["base_currency"] for currency in known)
        return {"base_currency": owner["base_currency"], "held_item_count": len(held),
                "valued_item_count": len(held) - unknown, "unvalued_item_count": unknown,
                "known_market_value_by_currency": {currency: str(amount) for currency, amount in sorted(known.items())},
                "total_market_value": str(known.get(owner["base_currency"], Decimal("0"))) if complete_base else None,
                "base_currency_cost": str(sum((item.remaining_cost for item in held), Decimal("0"))),
                "base_currency_realized_profit": str(sum((item.realized_profit for item in items), Decimal("0"))),
                "mixed_valuation_currencies": len(known) > 1,
                "valuation_basis": "仅汇总已有估值的持有数量；未知估值不按成本或零补齐；异币种不混加",
                "ledger_basis": "成本及已实现盈亏沿用账本保存的fx_rate_to_base"}

    def register(self, registry):
        from card_platform.mcp.server import EmptyRequest, ToolAdapter
        for name, model, handler, description in (
            ("search_cards", SearchCardsRequest, self.search_cards, "按球员、产品、签字、限编、评级、成本、持仓查询我的云端实卡。含分页/排序及稳定编号；别名不证明身份。"),
            ("get_card_detail", GetCardDetailRequest, self.get_card_detail, "用明确item_id查看我的实卡身份、照片是否存在、账本和历史估值日期。修改前用此工具核对并获取revision。"),
            ("get_portfolio_summary", EmptyRequest, self.get_portfolio_summary, "查看云端持仓成本、已实现盈亏及按币种分列的已知估值；证据不足部分保持未知。"),
        ):
            registry.register(ToolAdapter(name, description, model, frozenset({"cards:read"}), handler))
