"""Scoped collection and ledger mutations, all behind durable approval."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from card_platform.domain.identity_review import ConfirmedIdentity
from card_platform.domain.portfolio import LedgerEntry, TransactionKind, calculate_position
from card_platform.identity_review_api import ConfirmIdentityRequest, confirm_identity_with_connection
from card_platform.mcp.captures import digest
from card_platform.mcp.operations import OperationHandler
from card_platform.mcp.portfolio import card_revision, public_card
from card_platform.mcp.schemas import McpFailure, StrictModel
from card_platform.repository import NotFoundError, PortfolioRepository, PositionError, normalize_name
from card_platform.schemas import TransactionCreate


class ReviewedIdentity(ConfirmedIdentity):
    model_config = ConfigDict(extra="forbid")


class CardChanges(StrictModel):
    notes: str | None = Field(default=None, max_length=4000)
    strategy_tags: list[str] | None = Field(default=None, max_length=20)
    holding_status: Literal["held", "sold"] | None = None
    identity: ReviewedIdentity | None = None
    reason: str | None = Field(default=None, min_length=3, max_length=500)

    @model_validator(mode="after")
    def explicit_changes(self):
        if not self.model_fields_set - {"reason"}:
            raise ValueError("至少指定一个更改字段")
        if self.identity is not None and not self.reason:
            raise ValueError("身份更改必须说明核对依据")
        for field in ("strategy_tags", "holding_status", "identity"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("该字段不能为null")
        return self


class PrepareCardChangeRequest(StrictModel):
    item_id: UUID
    expected_revision: str = Field(min_length=1, max_length=128)
    changes: CardChanges
    idempotency_key: UUID


class ManagerTransaction(StrictModel):
    transaction_type: Literal["buy", "sell", "fee"]
    transacted_at: AwareDatetime
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    fee_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    fee_direction: Literal["owner_paid", "counterparty_paid", "none"]
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    fx_rate_to_base: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)
    fx_basis: str | None = Field(default=None, min_length=3, max_length=500)
    venue: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, value):
        return value.upper()

    @model_validator(mode="after")
    def fee_semantics(self):
        if self.fee_amount > 0 and self.fee_direction == "none":
            raise ValueError("非零费用必须明确承担方")
        if self.transaction_type == "fee" and (
            self.unit_price != 0 or self.quantity != 1 or self.fee_amount <= 0 or self.fee_direction != "owner_paid"
        ):
            raise ValueError("独立费用仅记录本人实际承担的非零支出，单价0、数量1")
        return self


class PrepareTransactionRequest(StrictModel):
    item_id: UUID
    expected_revision: str = Field(min_length=1, max_length=128)
    transaction: ManagerTransaction
    idempotency_key: UUID


class PrepareTrashChangeRequest(StrictModel):
    item_id: UUID
    expected_revision: str = Field(min_length=1, max_length=128)
    action: Literal["trash", "restore"]
    reason: str | None = Field(default=None, max_length=500)
    idempotency_key: UUID


class MutationTools:
    def __init__(self, database, operations):
        self.database, self.operations = database, operations
        self.repository = PortfolioRepository()
        for kind, scopes, execute in (
            ("update", {"cards:write"}, self._update_execute),
            ("transaction", {"transactions:write"}, self._transaction_execute),
            ("trash", {"cards:write"}, self._trash_execute),
            ("restore", {"cards:write"}, self._trash_execute),
        ):
            self.operations.register(kind, OperationHandler(frozenset(scopes), self._snapshot, execute))

    async def _snapshot(self, conn, principal, payload):
        item_id = UUID(payload["item_id"])
        revision = await card_revision(conn, principal.owner_id, item_id, lock=True,
                                       include_deleted=payload["kind"] in {"trash", "restore"})
        if payload["kind"] == "transaction":
            await self._prospective_position(conn, item_id, payload["posted_transaction"])
        if payload["kind"] == "update" and payload["changes"].get("identity"):
            await self._check_shared_identity(conn, item_id, payload["changes"]["identity"])
        return revision

    async def _prepare(self, principal, kind, request, values):
        request_hash = digest(request.model_dump(mode="json"))
        async with self.database.connection() as conn:
            await self.operations._grant(conn, principal, self.operations.handlers[kind].scopes)
            prior = await (await conn.execute("""SELECT * FROM chatgpt_operation
                WHERE owner_id=%s AND kind=%s AND idempotency_key=%s""",
                (principal.owner_id, kind, request.idempotency_key))).fetchone()
            if prior:
                if prior["payload"].get("request_hash") != request_hash or prior["connection_id"] != principal.connection_id:
                    raise McpFailure("IDEMPOTENCY_CONFLICT", "此请求编号已用于不同参数或连接")
                payload = prior["payload"]
            else:
                await card_revision(conn, principal.owner_id, request.item_id,
                                    include_deleted=kind in {"trash", "restore"})
                if kind in {"trash", "restore"}:
                    row = await (await conn.execute("SELECT id,display_id,holding_status,deleted_at FROM collection_item WHERE id=%s AND owner_id=%s",
                                                     (request.item_id, principal.owner_id))).fetchone()
                    target = {key: str(value) if isinstance(value, (UUID, datetime)) else value for key, value in row.items()}
                else:
                    target = public_card((await self.repository.item_detail(conn, principal.owner_id, request.item_id)).item)
                payload = {"kind": kind, "item_id": str(request.item_id), "request_hash": request_hash,
                           "target": target, **values}
        return await self.operations.prepare(principal, kind, request.idempotency_key, payload, request.expected_revision)

    async def prepare_card_change(self, principal, request: PrepareCardChangeRequest):
        return await self._prepare(principal, "update", request,
                                   {"changes": request.changes.model_dump(mode="json", exclude_unset=True)})

    async def prepare_transaction(self, principal, request: PrepareTransactionRequest):
        async with self.database.connection() as conn:
            owner = await (await conn.execute("SELECT base_currency FROM app_user WHERE id=%s", (principal.owner_id,))).fetchone()
        if owner is None:
            raise McpFailure("AUTH_REQUIRED", "账户不存在")
        tx = request.transaction
        if tx.currency != owner["base_currency"]:
            if tx.fx_rate_to_base is None or not tx.fx_basis:
                raise McpFailure("INVALID_INPUT", "外币交易缺少汇率或折算依据", "补充实际汇率和凭据说明")
            fx = tx.fx_rate_to_base
        else:
            if tx.fx_rate_to_base not in {None, Decimal("1")}:
                raise McpFailure("INVALID_INPUT", "同币种交易的折算率必须为1")
            fx = Decimal("1")
        fee = tx.fee_amount if tx.fee_direction == "owner_paid" else Decimal("0")
        notes = "\n".join(value for value in [tx.notes, f"ChatGPT fee_direction={tx.fee_direction}; declared_fee={tx.fee_amount} {tx.currency}",
                                             f"FX basis: {tx.fx_basis}" if tx.fx_basis else None] if value)
        posted = TransactionCreate(transaction_type=tx.transaction_type, transacted_at=tx.transacted_at,
            quantity=tx.quantity, unit_price=tx.unit_price, fee_amount=fee, currency=tx.currency,
            fx_rate_to_base=fx, venue=tx.venue, notes=notes)
        return await self._prepare(principal, "transaction", request,
                                   {"transaction": tx.model_dump(mode="json"), "posted_transaction": posted.model_dump(mode="json")})

    async def prepare_trash_change(self, principal, request: PrepareTrashChangeRequest):
        return await self._prepare(principal, request.action, request, {"action": request.action, "reason": request.reason})

    async def _prospective_position(self, conn, item_id, payload):
        rowset = await (await conn.execute("""SELECT transaction_type,transacted_at,created_at,quantity,unit_price,
             fee_amount,fx_rate_to_base,id FROM card_transaction WHERE collection_item_id=%s
             ORDER BY transacted_at,created_at,id""", (item_id,))).fetchall()
        candidate = TransactionCreate.model_validate(payload)
        now = datetime.now(timezone.utc)
        entries = [(r["transacted_at"], r["created_at"], str(r["id"]), self.repository._to_entry(r)) for r in rowset]
        entries.append((candidate.transacted_at, now, "new", LedgerEntry(TransactionKind(candidate.transaction_type),
            candidate.quantity, candidate.unit_price, candidate.fee_amount, candidate.fx_rate_to_base)))
        entries.sort(key=lambda entry: entry[:3])
        try:
            return calculate_position([entry[3] for entry in entries])
        except ValueError:
            raise McpFailure("INVALID_INPUT", "卖出数量超过对应日期的持仓", "核对数量和买卖日期") from None

    async def _transaction_execute(self, conn, principal, payload):
        item_id = UUID(payload["item_id"])
        position = await self._prospective_position(conn, item_id, payload["posted_transaction"])
        tx = TransactionCreate.model_validate(payload["posted_transaction"])
        try:
            transaction_id = await self.repository.add_transaction(conn, item_id, principal.owner_id, tx)
        except (NotFoundError, PositionError):
            raise McpFailure("INVALID_INPUT", "卡片或持仓已变化", "重新查询后准备交易") from None
        if tx.transaction_type in {"buy", "sell"}:
            await conn.execute("UPDATE collection_item SET holding_status=%s,updated_at=now() WHERE id=%s AND owner_id=%s",
                               ("held" if position.quantity_held > 0 else "sold", item_id, principal.owner_id))
        detail = await self.repository.item_detail(conn, principal.owner_id, item_id)
        return {"transaction_id": str(transaction_id), "item_id": str(item_id), "item": public_card(detail.item),
                "posted_fee_amount": str(tx.fee_amount), "currency": tx.currency}

    async def _update_execute(self, conn, principal, payload):
        changes = payload["changes"]
        item_id = UUID(payload["item_id"])
        result = {}
        if changes.get("identity"):
            try:
                reviewed = await confirm_identity_with_connection(conn, item_id,
                    ConfirmIdentityRequest(identity=changes["identity"], reason=changes["reason"],
                        field_provenance={"source": "chatgpt_owner_approved", "operation": "identity_review"}), principal.owner_id)
            except HTTPException:
                raise McpFailure("REVISION_CONFLICT", "身份审核失败，请重新核对") from None
            result = {"identity_version_id": str(reviewed["identity_version_id"]),
                      "valuation_job_id": str(reviewed["valuation_job_id"])}
        for field in ("notes", "strategy_tags", "holding_status"):
            if field in changes:
                await conn.execute(f"UPDATE collection_item SET {field}=%s,updated_at=now() WHERE id=%s AND owner_id=%s",
                                   (changes[field], item_id, principal.owner_id))
        detail = await self.repository.item_detail(conn, principal.owner_id, item_id)
        return {"item_id": str(item_id), "item": public_card(detail.item), **result}

    async def _trash_execute(self, conn, principal, payload):
        item_id = UUID(payload["item_id"])
        if payload["action"] == "trash":
            await self.repository.soft_delete_item(conn, item_id, principal.owner_id, payload.get("reason"))
        else:
            await self.repository.restore_item(conn, item_id, principal.owner_id)
        row = await (await conn.execute("SELECT id,display_id,holding_status,deleted_at FROM collection_item WHERE id=%s AND owner_id=%s",
                                         (item_id, principal.owner_id))).fetchone()
        return {"item_id": str(item_id), "action": payload["action"],
                "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
                "display_id": row["display_id"], "holding_status": row["holding_status"]}

    async def _check_shared_identity(self, conn, item_id, identity):
        # Existing review code updates shared catalog flags. A private connection
        # cannot silently change another physical card via those shared rows.
        # Lock target product rows before counting references. A statement that
        # waited for a row lock otherwise uses its earlier MVCC snapshot.
        sets = await (await conn.execute("""SELECT cs.id FROM card_set cs JOIN brand b ON b.id=cs.brand_id
            WHERE b.name=%s AND cs.season_start=%s AND cs.product_name=%s
            AND cs.set_name IS NOT DISTINCT FROM %s FOR UPDATE OF cs""",
            (identity["brand_name"], identity["season_start"], identity["product_name"], identity.get("set_name")))).fetchall()
        for product in sets:
            shared = await (await conn.execute("""SELECT cs.season_end,
                EXISTS(SELECT 1 FROM collection_item other JOIN card_definition cd ON cd.id=other.card_definition_id
                       WHERE cd.card_set_id=cs.id AND other.id<>%s) AS shared
                FROM card_set cs WHERE cs.id=%s""", (item_id, product["id"]))).fetchone()
            if (shared["shared"] and shared["season_end"] in {None, identity["season_start"]}
                    and identity.get("season_end") == identity["season_start"] + 1):
                raise McpFailure("REVISION_CONFLICT", "此赛季更改会影响共享产品的其他实卡", "先在平台身份审核中解决共享版本冲突")
        await conn.execute("""SELECT cd.id FROM card_definition cd JOIN player p ON p.id=cd.player_id
            WHERE cd.card_set_id=ANY(%s::uuid[]) AND p.normalized_name=%s
            AND COALESCE(cd.card_number,'')=COALESCE(%s,'') AND COALESCE(cd.insert_name,'')=COALESCE(%s,'')
            AND COALESCE(cd.parallel_name,'')=COALESCE(%s,'') FOR UPDATE OF cd""",
            ([row["id"] for row in sets], normalize_name(identity["player_name"]), identity.get("card_number"),
             identity.get("insert_name"), identity.get("parallel_name")))
        rows = await (await conn.execute("""SELECT cd.*,cs.season_end,
            EXISTS(SELECT 1 FROM collection_item other WHERE other.card_definition_id=cd.id AND other.id<>%s) AS shared,
            EXISTS(SELECT 1 FROM collection_item other JOIN card_definition od ON od.id=other.card_definition_id
                   WHERE od.card_set_id=cs.id AND other.id<>%s) AS shared_set
            FROM card_definition cd JOIN player p ON p.id=cd.player_id JOIN card_set cs ON cs.id=cd.card_set_id
            JOIN brand b ON b.id=cs.brand_id WHERE p.normalized_name=%s AND b.name=%s
            AND cs.season_start=%s AND cs.product_name=%s AND cs.set_name IS NOT DISTINCT FROM %s
            AND COALESCE(cd.card_number,'')=COALESCE(%s,'') AND COALESCE(cd.insert_name,'')=COALESCE(%s,'')
            AND COALESCE(cd.parallel_name,'')=COALESCE(%s,'')""",
            (item_id, item_id, normalize_name(identity["player_name"]), identity["brand_name"], identity["season_start"],
             identity["product_name"], identity.get("set_name"), identity.get("card_number"),
             identity.get("insert_name"), identity.get("parallel_name")))).fetchall()
        for row in rows:
            fields = ("is_rookie", "is_autograph", "autograph_type", "is_memorabilia", "memorabilia_types")
            changes_shared = row["shared"] and (any(row[key] != identity[key] for key in fields)
                                                   or row["print_run"] != identity.get("serial_total"))
            changes_season = (row["shared_set"] and row["season_end"] in {None, identity["season_start"]}
                              and identity.get("season_end") == identity["season_start"] + 1)
            if changes_shared or changes_season:
                raise McpFailure("REVISION_CONFLICT", "此更改会影响共享版本的其他实卡", "先在平台身份审核中解决版本冲突，再重新准备")

    def register(self, registry):
        from card_platform.mcp.server import ToolAdapter
        for name, model, handler, scope, description in (
            ("prepare_card_change", PrepareCardChangeRequest, self.prepare_card_change, "cards:write", "为明确实卡准备局部校正。只更新提供字段；身份必须提供完整核对身份与reason，走原身份审核并保留历史。"),
            ("prepare_transaction", PrepareTransactionRequest, self.prepare_transaction, "transactions:write", "准备买入、卖出或费用记录。必须明确数量、金额、币种、有时区日期和费用承担方；外币必须提供汇率及依据。"),
            ("prepare_trash_change", PrepareTrashChangeRequest, self.prepare_trash_change, "cards:write", "准备把指定实卡移入回收站或恢复，保留账本历史。没有永久删除工具。"),
        ):
            registry.register(ToolAdapter(name, description, model, frozenset({scope}), handler, read_only=False))
