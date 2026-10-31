"""Deterministic activity reports from the complete owned ledger history."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import model_validator

from card_platform.domain.portfolio import calculate_position
from card_platform.mcp.schemas import StrictModel
from card_platform.repository import PortfolioRepository


class ActivityReportRequest(StrictModel):
    start: datetime
    end: datetime
    timezone: str = 'Asia/Shanghai'

    @model_validator(mode='after')
    def interval(self):
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('未知时区') from None
        self.start = (self.start.replace(tzinfo=zone) if self.start.tzinfo is None else self.start).astimezone(timezone.utc)
        self.end = (self.end.replace(tzinfo=zone) if self.end.tzinfo is None else self.end).astimezone(timezone.utc)
        if self.start >= self.end:
            raise ValueError('start必须早于end')
        return self


def empty_totals():
    return {key: Decimal('0') for key in ('buy_gross', 'sell_gross', 'fees', 'adjustment_gross')}


class ReportTools:
    def __init__(self, database):
        self.database = database

    async def get_activity_report(self, principal, request: ActivityReportRequest):
        async with self.database.connection() as conn:
            # One query snapshot covers owner currency and complete history,
            # including trash: soft deletion must not erase financial history.
            rows = await (await conn.execute('''SELECT u.base_currency,t.*,ci.display_id,
                op.payload->'transaction'->>'fx_basis' AS fx_basis
                FROM app_user u JOIN collection_item ci ON ci.owner_id=u.id
                JOIN card_transaction t ON t.collection_item_id=ci.id
                LEFT JOIN chatgpt_operation op ON op.owner_id=u.id AND op.kind='transaction'
                  AND op.status='succeeded' AND op.result_ref->>'transaction_id'=t.id::text
                WHERE u.id=%s AND t.transacted_at<%s
                ORDER BY t.transacted_at,t.created_at,t.id''', (principal.owner_id, request.end))).fetchall()
            # An empty history still needs the account's base currency.
            owner = await (await conn.execute('SELECT base_currency FROM app_user WHERE id=%s', (principal.owner_id,))).fetchone()
        currency = owner['base_currency'] if owner else None
        original, base, entries, before = defaultdict(empty_totals), empty_totals(), defaultdict(list), {}
        missing, invalid, transactions = [], [], []
        for row in rows:
            in_range = row['transacted_at'] >= request.start
            known_fx = (row['currency'] == currency or row['fx_rate_to_base'] not in (None, Decimal('1')) or bool(row['fx_basis']))
            if not known_fx:
                missing.append(str(row['id']))
            history = entries[row['collection_item_id']]
            if row['collection_item_id'] not in before and in_range:
                try:
                    before[row['collection_item_id']] = calculate_position(history).realized_profit
                except (ValueError, TypeError):
                    invalid.append(str(row['collection_item_id']))
            if known_fx:
                history.append(PortfolioRepository._to_entry(row))
            if not in_range:
                continue
            gross = row['quantity'] * row['unit_price']
            key = {'buy': 'buy_gross', 'sell': 'sell_gross', 'adjustment': 'adjustment_gross'}.get(row['transaction_type'])
            if key:
                original[row['currency']][key] += gross
                if known_fx:
                    base[key] += gross * row['fx_rate_to_base']
            original[row['currency']]['fees'] += row['fee_amount']
            if known_fx:
                base['fees'] += row['fee_amount'] * row['fx_rate_to_base']
            transactions.append({'transaction_id': str(row['id']), 'item_id': str(row['collection_item_id']),
                'display_id': row['display_id'], 'type': row['transaction_type'], 'at': row['transacted_at'].isoformat(),
                'quantity': str(row['quantity']), 'unit_price': str(row['unit_price']), 'gross': str(gross),
                'fee_amount': str(row['fee_amount']), 'currency': row['currency'],
                'fx_rate_to_base': str(row['fx_rate_to_base']) if known_fx else None})
        realized = Decimal('0')
        for item_id, opening in before.items():
            try:
                realized += calculate_position(entries[item_id]).realized_profit - opening
            except (ValueError, TypeError):
                invalid.append(str(item_id))
        # Missing historical cost conversion also prevents a trustworthy period
        # realized-profit calculation; never sum incomplete subsets as totals.
        base_result = {key: str(value) if not missing else None for key, value in base.items()}
        base_result['realized_profit'] = str(realized) if not missing and not invalid else None
        return {'start': request.start.isoformat(), 'end': request.end.isoformat(), 'timezone': request.timezone,
            'interval': 'start <= transacted_at < end', 'includes_trash': True, 'currency': currency,
            'by_currency': {code: {key: str(value) for key, value in totals.items()} for code, totals in sorted(original.items())},
            'base_currency': base_result, 'missing_fx_transaction_ids': missing, 'invalid_ledger_item_ids': sorted(set(invalid)),
            'transactions': transactions,
            'notes': ['按完整历史账本的移动平均成本计算区间已实现盈亏。',
                      '外币历史记录的默认汇率1且无明确依据时视为缺失；原币种金额保留，合计不补零。']}

    def register(self, registry):
        from card_platform.mcp.server import ToolAdapter
        registry.register(ToolAdapter('get_activity_report', '按时区和半开时间区间汇总本人账本；原币种分列，含回收站历史，缺汇率的合计为null。',
            ActivityReportRequest, frozenset({'cards:read'}), self.get_activity_report))
