"""Read-only price coverage audit; run with PYTHONPATH=apps/api/src.

No accounts, secrets, or private collection records are emitted. The default
scope is the union of current held basketball players, not a particular account.
"""
import argparse
import asyncio
from collections import Counter
from datetime import date, datetime, timedelta
import json

from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.player_prices.domain import TZ
from card_platform.player_prices.repository import PriceRepository
from card_platform.player_prices.service import analyze_observations, existing_samples


def coverage_breakdown(players):
    availability, rejections = Counter(), Counter()
    diagnosed = 0
    for player in players:
        market, refresh, coverage = player["market"], player["refresh"], player["coverage"]
        if player["change_pct"] is not None:
            state = "with_index"
        elif market["trend_count"]:
            state = "card_trend_only"
        elif market["sale_count"]:
            state = "sales_only"
        elif refresh["error"]:
            state = "source_error_without_sales"
        elif refresh["queued"]:
            state = "queued_without_sales"
        elif coverage.get("sample_count"):
            state = "candidate_only"
        elif refresh.get("last_success_at"):
            state = "no_confirmed_after_search"
        else:
            state = "not_collected"
        availability[state] += 1
        if coverage.get("rejection_reasons"):
            diagnosed += 1
            rejections.update(coverage["rejection_reasons"])
    return dict(availability=dict(availability), per_player_rejection_totals=dict(rejections),
                players_with_rejection_diagnostics=diagnosed)


async def audit(args):
    database = Database(get_settings())
    await database.open()
    try:
        async with database.connection() as c:
            await c.execute('SET TRANSACTION READ ONLY')
            board = await PriceRepository().board(c, None, args.date, 'gainers')
            states = Counter(p['status'] for p in board['players'])
            result = dict(report_date=args.date.isoformat(), scope='all_held_basketball_players',
                          summary=board['summary'], index_statuses=dict(states),
                          **coverage_breakdown(board['players']),
                          confirmed_sales=sum(p['market']['sale_count'] for p in board['players']),
                          confirmed_card_types=sum(p['market']['card_count'] for p in board['players']),
                          comparable_card_types=sum(p['market']['trend_count'] for p in board['players']))
            if args.player:
                observations = await existing_samples(c, args.player, args.date)
                known = await (await c.execute(
                    "SELECT normalized_identity FROM market_identity_cache WHERE lower(normalized_identity->>'player_name')=lower(%s)",
                    (args.player,),
                )).fetchall()
                rows, diagnostics = await asyncio.to_thread(
                    analyze_observations, observations, args.player, [r['normalized_identity'] for r in known]
                )
                from card_platform.player_prices.domain import calculate_market
                market = calculate_market(rows, args.date)
                result['candidate_replay'] = dict(player_name=args.player, **diagnostics,
                                                  card_count=market['card_count'], trend_count=market['trend_count'])
            await c.rollback()
            return result
    finally:
        await database.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat,
                        default=datetime.now(TZ).date() - timedelta(days=1))
    parser.add_argument('--player', help='Optional public player-name candidate diagnosis; no writes')
    args = parser.parse_args()
    if args.date >= datetime.now(TZ).date():
        parser.error('report date must be a completed Shanghai day')
    print(json.dumps(asyncio.run(audit(args)), ensure_ascii=False, indent=2))
