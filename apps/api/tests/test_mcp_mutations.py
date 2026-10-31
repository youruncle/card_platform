import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
import pytest
import pytest_asyncio

from card_platform.mcp.operations import OperationService
from card_platform.mcp.portfolio import card_revision
from card_platform.mcp.schemas import McpFailure
from card_platform.repository import PortfolioRepository
from card_platform.schemas import CardIdentityInput, CollectionItemCreate, PurchaseInput

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def mutation_case(mcp_database, mcp_principal):
    from card_platform.mcp.mutations import MutationTools
    repository = PortfolioRepository()
    product = f"Prizm {uuid4()}"
    async with mcp_database.connection() as conn:
        ids = []
        for i in range(2):
            ids.append(await repository.create_item(conn, CollectionItemCreate(owner_id=mcp_principal.owner_id,
                card=CardIdentityInput(player_name="Stephen Curry", brand_name="Panini", product_name=product,
                                       season_start=2023, season_end=2024, card_number="30"),
                purchase=PurchaseInput(transacted_at=datetime(2026, 10, 1, tzinfo=timezone.utc), price=100, currency="CNY"))))
        await conn.execute("UPDATE collection_item SET holding_status='sold' WHERE id=%s", (ids[1],))
    operations = OperationService(mcp_database, "https://cardsp.cn")
    return MutationTools(mcp_database, operations), mcp_principal, ids


async def revision(tools, principal, item_id):
    async with tools.database.connection() as conn:
        return await card_revision(conn, principal.owner_id, item_id)


async def approve_execute(tools, principal, operation):
    await tools.operations.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    return await tools.operations.execute(principal, operation["operation_id"])


def transaction(**overrides):
    return {"transaction_type": "sell", "quantity": "1", "unit_price": "150", "fee_amount": "10",
            "fee_direction": "owner_paid", "currency": "CNY", "transacted_at": "2026-10-10T12:00:00+08:00", **overrides}


async def test_patch_of_sold_card_does_not_restore_held_and_identity_uses_review(mutation_case):
    from card_platform.mcp.mutations import PrepareCardChangeRequest
    tools, principal, ids = mutation_case
    operation = await tools.prepare_card_change(principal, PrepareCardChangeRequest(item_id=ids[1],
        expected_revision=await revision(tools, principal, ids[1]), changes={"notes": "owner corrected notes"}, idempotency_key=uuid4()))
    await approve_execute(tools, principal, operation)
    async with tools.database.connection() as conn:
        assert (await (await conn.execute("SELECT holding_status,notes FROM collection_item WHERE id=%s", (ids[1],))).fetchone()) == {
            "holding_status": "sold", "notes": "owner corrected notes"}
    identity = operation["payload"]["target"]
    operation = await tools.prepare_card_change(principal, PrepareCardChangeRequest(item_id=ids[0],
        expected_revision=await revision(tools, principal, ids[0]), changes={"identity": {
            "player_name": identity["player_name"], "brand_name": identity["brand_name"], "product_name": identity["product_name"],
            "season_start": 2023, "season_end": 2024, "card_number": "30", "parallel_name": "Blue",
            "is_rookie": False, "is_autograph": False, "is_memorabilia": False}, "reason": "owner checked physical card"},
        idempotency_key=uuid4()))
    result = await approve_execute(tools, principal, operation)
    assert result["readback"]["identity_version_id"]
    assert result["readback"]["item"]["parallel_name"] == "Blue"
    async with tools.database.connection() as conn:
        assert (await (await conn.execute("SELECT count(*) AS n FROM card_identity_change_audit WHERE collection_item_id=%s", (ids[0],))).fetchone())["n"] == 1
        assert (await (await conn.execute("SELECT count(*) AS n FROM valuation_job WHERE collection_item_id=%s", (ids[0],))).fetchone())["n"] == 1


async def test_money_missing_or_invalid_parameters_are_rejected():
    from card_platform.mcp.mutations import PrepareTransactionRequest
    base = {"item_id": uuid4(), "expected_revision": "r1", "idempotency_key": uuid4()}
    for changes in ({"quantity": "0"}, {"unit_price": "-1"}, {"fee_amount": "-1"},
                    {"currency": "人民币"}, {"transacted_at": "2026-10-01T12:00:00"}, {"fee_direction": None}):
        with pytest.raises(ValidationError):
            PrepareTransactionRequest(**base, transaction=transaction(**changes))
    incomplete = transaction()
    del incomplete["currency"]
    with pytest.raises(ValidationError):
        PrepareTransactionRequest(**base, transaction=incomplete)


async def test_sale_oversell_concurrency_and_three_retries_have_one_ledger_effect(mutation_case):
    from card_platform.mcp.mutations import PrepareTransactionRequest
    tools, principal, ids = mutation_case
    before = await revision(tools, principal, ids[0])
    with pytest.raises(McpFailure):
        await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0], expected_revision=before,
            transaction=transaction(quantity="2"), idempotency_key=uuid4()))
    request = PrepareTransactionRequest(item_id=ids[0], expected_revision=before, transaction=transaction(), idempotency_key=uuid4())
    operation = await tools.prepare_transaction(principal, request)
    assert operation["payload"]["target"]["id"] == str(ids[0])
    assert operation["payload"]["target"]["display_id"] >= 0
    await tools.operations.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    results = await asyncio.gather(*(tools.operations.execute(principal, operation["operation_id"]) for _ in range(3)))
    assert len({r["readback"]["transaction_id"] for r in results}) == 1
    assert sum(not r["already_executed"] for r in results) == 1
    assert Decimal(results[0]["readback"]["item"]["quantity_held"]) == 0
    assert Decimal(results[0]["readback"]["item"]["realized_profit"]) == 40
    assert results[0]["readback"]["item"]["holding_status"] == "sold"
    assert (await tools.prepare_transaction(principal, request))["operation_id"] == operation["operation_id"]


async def test_foreign_exchange_needs_explicit_rate_and_basis(mutation_case):
    from card_platform.mcp.mutations import PrepareTransactionRequest
    tools, principal, ids = mutation_case
    for extra in ({}, {"fx_rate_to_base": "7"}):
        with pytest.raises(McpFailure) as error:
            await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
                expected_revision=await revision(tools, principal, ids[0]),
                transaction=transaction(currency="USD", **extra), idempotency_key=uuid4()))
        assert error.value.code == "INVALID_INPUT"
    operation = await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
        expected_revision=await revision(tools, principal, ids[0]), transaction=transaction(currency="USD",
        fx_rate_to_base="7", fx_basis="bank settlement receipt"), idempotency_key=uuid4()))
    result = await approve_execute(tools, principal, operation)
    assert Decimal(result["readback"]["item"]["realized_profit"]) == Decimal("880")


async def test_counterparty_fee_is_preserved_but_not_charged_to_owner(mutation_case):
    from card_platform.mcp.mutations import PrepareTransactionRequest
    tools, principal, ids = mutation_case
    operation = await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
        expected_revision=await revision(tools, principal, ids[0]), transaction=transaction(fee_direction="counterparty_paid"), idempotency_key=uuid4()))
    result = await approve_execute(tools, principal, operation)
    assert Decimal(result["readback"]["item"]["realized_profit"]) == 50
    assert result["readback"]["posted_fee_amount"] == "0"


async def test_backdated_sale_cannot_make_the_chronological_ledger_negative(mutation_case):
    from card_platform.mcp.mutations import PrepareTransactionRequest
    tools, principal, ids = mutation_case
    with pytest.raises(McpFailure):
        await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
            expected_revision=await revision(tools, principal, ids[0]),
            transaction=transaction(transacted_at="2026-09-01T00:00:00+08:00"), idempotency_key=uuid4()))


async def test_new_transaction_after_approval_conflicts_and_does_not_override(mutation_case):
    from card_platform.mcp.mutations import PrepareTransactionRequest
    from card_platform.schemas import TransactionCreate
    tools, principal, ids = mutation_case
    operation = await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
        expected_revision=await revision(tools, principal, ids[0]), transaction=transaction(), idempotency_key=uuid4()))
    await tools.operations.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    async with tools.database.connection() as conn:
        await PortfolioRepository().add_transaction(conn, ids[0], principal.owner_id, TransactionCreate(
            transaction_type="fee", transacted_at=datetime(2026, 10, 5, tzinfo=timezone.utc), currency="CNY", fee_amount=2))
    with pytest.raises(McpFailure) as error:
        await tools.operations.execute(principal, operation["operation_id"])
    assert error.value.code == "REVISION_CONFLICT"


async def test_private_identity_change_cannot_upgrade_a_season_shared_with_other_cards(mutation_case):
    from card_platform.mcp.mutations import PrepareCardChangeRequest
    tools, principal, ids = mutation_case
    async with tools.database.connection() as conn:
        await conn.execute("""UPDATE card_set SET season_end=2023 WHERE id=(
            SELECT cd.card_set_id FROM card_definition cd JOIN collection_item ci ON ci.card_definition_id=cd.id WHERE ci.id=%s)""", (ids[0],))
        detail = await tools.repository.item_detail(conn, principal.owner_id, ids[0])
    identity = {key: getattr(detail.item, key) for key in (
        "player_name", "brand_name", "product_name", "card_number", "is_rookie", "is_autograph", "is_memorabilia")}
    identity.update(season_start=2023, season_end=2024, parallel_name="Blue")
    with pytest.raises(McpFailure) as error:
        await tools.prepare_card_change(principal, PrepareCardChangeRequest(item_id=ids[0],
            expected_revision=await revision(tools, principal, ids[0]),
            changes={"identity": identity, "reason": "owner checked own physical card"}, idempotency_key=uuid4()))
    assert error.value.code == "REVISION_CONFLICT"
    async with tools.database.connection() as conn:
        assert (await tools.repository.item_detail(conn, principal.owner_id, ids[1])).item.season_end == 2023


async def test_fee_after_full_sale_reduces_realized_profit_without_phantom_holding_cost(mutation_case):
    from card_platform.mcp.mutations import PrepareTransactionRequest
    tools, principal, ids = mutation_case
    sale = await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
        expected_revision=await revision(tools, principal, ids[0]), transaction=transaction(), idempotency_key=uuid4()))
    await approve_execute(tools, principal, sale)
    fee = await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
        expected_revision=await revision(tools, principal, ids[0]), transaction=transaction(
            transaction_type="fee", transacted_at="2026-10-11T12:00:00+08:00", quantity="1", unit_price="0", fee_amount="5"),
        idempotency_key=uuid4()))
    result = await approve_execute(tools, principal, fee)
    assert Decimal(result["readback"]["item"]["realized_profit"]) == 35
    assert Decimal(result["readback"]["item"]["remaining_cost"]) == 0
