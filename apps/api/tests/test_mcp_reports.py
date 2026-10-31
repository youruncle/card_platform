from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
import pytest

from test_mcp_mutations import mutation_case as shared_mutation_case

mutation_case = shared_mutation_case
pytestmark = pytest.mark.asyncio


async def test_report_shanghai_half_open_month_includes_trashed_cards(mutation_case):
    from card_platform.mcp.reports import ActivityReportRequest, ReportTools
    tools, principal, ids = mutation_case
    async with tools.database.connection() as conn:
        for at, kind, price, fee in [('2026-10-01T00:00:00+08:00', 'sell', 150, 10),
                                      ('2026-10-31T23:59:59+08:00', 'fee', 0, 5),
                                      ('2026-11-01T00:00:00+08:00', 'fee', 0, 99)]:
            await conn.execute("""INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,fee_amount,currency)
                VALUES (%s,%s,%s,1,%s,%s,'CNY')""", (ids[0], kind, at, price, fee))
        # Move the seed purchase before the report interval.
        await conn.execute("UPDATE card_transaction SET transacted_at='2026-09-01T00:00:00Z' WHERE collection_item_id=%s AND transaction_type='buy'", (ids[0],))
        await conn.execute('UPDATE collection_item SET deleted_at=now() WHERE id=%s', (ids[0],))
    result = await ReportTools(tools.database).get_activity_report(principal, ActivityReportRequest(start='2026-10-01', end='2026-11-01'))
    assert result['timezone'] == 'Asia/Shanghai'
    assert len(result['transactions']) == 3  # second card's October seed purchase included
    assert Decimal(result['by_currency']['CNY']['sell_gross']) == 150
    assert Decimal(result['by_currency']['CNY']['fees']) == 15
    assert Decimal(result['base_currency']['realized_profit']) == 35
    assert result['includes_trash'] is True


async def test_report_foreign_default_one_is_unknown_and_separates_original_currencies(mutation_case):
    from card_platform.mcp.reports import ActivityReportRequest, ReportTools
    tools, principal, ids = mutation_case
    async with tools.database.connection() as conn:
        await conn.execute("UPDATE card_transaction SET currency='USD' WHERE collection_item_id=%s", (ids[0],))
    result = await ReportTools(tools.database).get_activity_report(principal, ActivityReportRequest(start='2026-10-01', end='2026-11-01'))
    assert Decimal(result['by_currency']['USD']['buy_gross']) == 100
    assert Decimal(result['by_currency']['CNY']['buy_gross']) == 100
    assert result['base_currency']['buy_gross'] is None
    assert result['base_currency']['realized_profit'] is None
    assert result['missing_fx_transaction_ids']


async def test_report_never_includes_other_owner_and_empty_is_zero(mcp_database, mcp_principal):
    from card_platform.mcp.reports import ActivityReportRequest, ReportTools
    result = await ReportTools(mcp_database).get_activity_report(mcp_principal, ActivityReportRequest(start='2026-10-01', end='2026-11-01', timezone='UTC'))
    assert result['transactions'] == []
    assert Decimal(result['base_currency']['realized_profit']) == 0


async def test_report_rejects_reverse_range_unknown_zone_and_owner_override():
    from card_platform.mcp.reports import ActivityReportRequest
    for extra in ({'start': '2026-11-01', 'end': '2026-10-01'}, {'timezone': 'Mars/Base'}, {'owner_id': str(uuid4())}):
        values = {'start': '2026-10-01', 'end': '2026-11-01', **extra}
        with pytest.raises(ValidationError):
            ActivityReportRequest(**values)


async def test_report_adjustment_uses_existing_ledger_cost_semantics(mutation_case):
    from card_platform.mcp.reports import ActivityReportRequest, ReportTools
    tools, principal, ids = mutation_case
    async with tools.database.connection() as conn:
        await conn.execute("""INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,fee_amount,currency)
          VALUES (%s,'adjustment','2026-10-05T00:00:00Z',1,20,0,'CNY'),
                 (%s,'sell','2026-10-10T00:00:00Z',1,150,0,'CNY')""", (ids[0],ids[0]))
    result = await ReportTools(tools.database).get_activity_report(principal, ActivityReportRequest(start='2026-10-01', end='2026-11-01'))
    assert Decimal(result['by_currency']['CNY']['adjustment_gross']) == 20
    assert Decimal(result['base_currency']['realized_profit']) == 30


async def test_report_uses_explicit_one_to_one_fx_from_approved_transaction(mutation_case):
    from card_platform.mcp.reports import ActivityReportRequest, ReportTools
    from card_platform.mcp.mutations import PrepareTransactionRequest
    from test_mcp_mutations import transaction, revision, approve_execute
    tools, principal, ids = mutation_case
    operation = await tools.prepare_transaction(principal, PrepareTransactionRequest(item_id=ids[0],
        expected_revision=await revision(tools, principal, ids[0]), idempotency_key=uuid4(),
        transaction=transaction(currency='USD', fx_rate_to_base='1', fx_basis='explicit settlement rate')))
    await approve_execute(tools, principal, operation)
    result = await ReportTools(tools.database).get_activity_report(principal, ActivityReportRequest(start='2026-10-01', end='2026-11-01'))
    assert result['missing_fx_transaction_ids'] == []
    assert Decimal(result['base_currency']['realized_profit']) == 40
