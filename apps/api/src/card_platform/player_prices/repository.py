from datetime import date, timedelta
from collections import defaultdict

from psycopg.types.json import Jsonb

from card_platform.player_prices.domain import (
    TZ, calculate_market, calculate_snapshot, day_end, is_mainstream_card, select_basket,
)
from card_platform.player_prices.subjects import SubjectIndex

ROSTER_SQL = """SELECT DISTINCT p.id, p.full_name FROM player p
    JOIN card_definition cd ON cd.player_id=p.id
    JOIN card_set cs ON cs.id=cd.card_set_id
    JOIN collection_item ci ON ci.card_definition_id=cd.id
    WHERE ci.deleted_at IS NULL AND ci.holding_status='held' AND cs.sport='basketball'
      AND p.full_name IS NOT NULL AND btrim(p.full_name)<>'' """


class PriceRepository:
    async def subjects(self, c):
        rows = await (await c.execute(
            "SELECT id,full_name,birth_date,nba_person_id,created_at FROM player WHERE full_name IS NOT NULL AND btrim(full_name)<>''"
        )).fetchall()
        return SubjectIndex(rows)

    async def subject(self, c, player_id):
        return (await self.subjects(c)).get(player_id)

    async def roster(self, c, owner_id=None):
        sql = ROSTER_SQL + (" AND ci.owner_id=%s" if owner_id is not None else "")
        held = await (
            await c.execute(
                sql + " ORDER BY p.full_name", (owner_id,) if owner_id is not None else ()
            )
        ).fetchall()
        if not held:
            return []
        return (await self.subjects(c)).for_ids([p['id'] for p in held])

    async def enqueue(self, c, owner_id=None, player_id=None):
        roster = await self.roster(c, owner_id)
        if player_id is not None:
            roster = [p for p in roster if player_id in p["member_ids"]]
        for player in roster:
            await c.execute(
                """INSERT INTO player_price_state(player_id,requested_at)
                VALUES(%s,now()) ON CONFLICT(player_id) DO UPDATE SET requested_at=now()""",
                (player["id"],),
            )
        return len(roster)

    async def seed_states(self, c):
        roster = await self.roster(c)
        await c.execute(
            "INSERT INTO player_price_state(player_id) SELECT unnest(%s::uuid[]) ON CONFLICT DO NOTHING",
            ([p['id'] for p in roster],),
        )

    async def due_player(self, c, report_date):
        roster = await self.roster(c)
        ids = [p['id'] for p in roster]
        photo_pending = await (await c.execute(
            """SELECT player_id FROM player_price_state WHERE photo_recheck_requested_at IS NOT NULL
            AND (last_success_at IS NULL OR photo_recheck_requested_at>last_success_at)"""
        )).fetchall()
        ids = list(set(ids) | {r['player_id'] for r in photo_pending})
        if not ids:
            return None
        result = await c.execute(
            """SELECT p.id,p.full_name,s.requested_at,s.last_attempt_at FROM player_price_state s
            JOIN player p ON p.id=s.player_id WHERE p.full_name IS NOT NULL AND btrim(p.full_name)<>'' AND
              (s.last_attempt_at IS NULL OR s.last_attempt_at < now()-interval '5 minutes')
              AND ((s.requested_at IS NOT NULL AND
                    (s.last_attempt_at IS NULL OR s.requested_at>s.last_attempt_at))
                   OR (s.photo_recheck_requested_at IS NOT NULL
                       AND (s.last_success_at IS NULL OR s.photo_recheck_requested_at>s.last_success_at)
                       AND (s.error IS NULL OR s.last_attempt_at<now()-interval '6 hours'))
                   OR ((s.last_report_date IS NULL OR s.last_report_date<%s)
                       AND (s.error IS NULL OR s.last_attempt_at<now()-interval '6 hours'))
                   OR (s.coverage->>'history_complete'='false'
                       AND s.last_attempt_at<now()-interval '30 minutes'
                       AND (s.error IS NULL OR s.last_attempt_at<now()-interval '6 hours'))
                   OR (s.coverage->>'incremental_complete'='false'
                       AND (s.error IS NULL OR s.last_attempt_at<now()-interval '6 hours')))
              AND p.id=ANY(%s)
            ORDER BY CASE
                WHEN s.requested_at IS NOT NULL AND
                     (s.last_attempt_at IS NULL OR s.requested_at>s.last_attempt_at) THEN 0
                WHEN s.last_report_date IS NULL OR s.last_report_date<%s THEN 1
                ELSE 2 END,
                s.last_attempt_at NULLS FIRST,p.id LIMIT 1""",
            (report_date, ids, report_date),
        )
        return await result.fetchone()

    async def exclude_sources(self, c, sources):
        for source in sources:
            await c.execute(
                "INSERT INTO player_price_source_conflict(source_id) VALUES(%s) ON CONFLICT DO NOTHING",
                (source,),
            )
        if sources:
            await c.execute(
                "UPDATE player_price_sale SET excluded=true WHERE source_id=ANY(%s)",
                (list(sources),),
            )

    async def store_sales(self, c, player_id, rows):
        subject = await self.subject(c, player_id)
        if subject:
            player_id = subject['id']
        for r in rows:
            await c.execute(
                """INSERT INTO player_price_sale(source_id,player_id,card_key,
                identity,sale_price,occurred_at,source_url,raw_title,excluded)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,EXISTS(
                    SELECT 1 FROM player_price_source_conflict WHERE source_id=%s)) ON CONFLICT(source_id) DO UPDATE SET
                  excluded=player_price_sale.excluded OR
                    player_price_sale.player_id<>EXCLUDED.player_id OR
                    player_price_sale.card_key<>EXCLUDED.card_key OR
                    player_price_sale.sale_price<>EXCLUDED.sale_price OR
                    player_price_sale.occurred_at<>EXCLUDED.occurred_at,
                  observed_at=now()""",
                (
                    r["source_id"],
                    player_id,
                    r["card_key"],
                    Jsonb(r["identity"]),
                    r["sale_price"],
                    r["occurred_at"],
                    r["source_url"],
                    r["raw_title"],
                    r["source_id"],
                ),
            )

    async def sales(self, c, player_id, report_date):
        subject = await self.subject(c, player_id)
        ids = subject['member_ids'] if subject else [player_id]
        end = day_end(report_date)
        return await (
            await c.execute(
                """SELECT source_id,card_key,identity,sale_price,
            occurred_at,source_url,raw_title FROM player_price_sale WHERE player_id=ANY(%s)
              AND NOT excluded AND occurred_at >= %s AND occurred_at < %s
            ORDER BY occurred_at DESC,source_id""",
                (ids, end - timedelta(days=90), end),
            )
        ).fetchall()

    async def basket(self, c, player_id, report_date):
        subject = await self.subject(c, player_id)
        if subject:
            player_id = subject['id']
        row = await (
            await c.execute(
                """SELECT cards,selected_on FROM player_price_basket
            WHERE player_id=%s AND month=%s""",
                (player_id, report_date.replace(day=1)),
            )
        ).fetchone()
        return row

    async def compute(self, c, player_id, report_date, *, coverage=None):
        subject = await self.subject(c, player_id)
        if subject:
            player_id = subject['id']
        await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,81))", (str(player_id),))
        rows = await self.sales(c, player_id, report_date)
        saved = await self.basket(c, player_id, report_date)
        if saved and any(not is_mainstream_card(card["identity"]) for card in saved["cards"]):
            saved = None
        cards = saved["cards"] if saved else select_basket(rows, report_date)
        selected_on = saved["selected_on"] if saved else report_date
        # Insufficient first discovery must not freeze an empty basket for a month.
        snapshot = calculate_snapshot(rows, cards, report_date)
        if saved is None and snapshot["status"] == "ready":
            await c.execute(
                """INSERT INTO player_price_basket(player_id,month,selected_on,cards)
                VALUES(%s,%s,%s,%s) ON CONFLICT(player_id,month) DO UPDATE SET
                selected_on=EXCLUDED.selected_on,cards=EXCLUDED.cards""",
                (player_id, report_date.replace(day=1), report_date, Jsonb(cards)),
            )
        snapshot = calculate_snapshot(rows, cards, report_date)
        snapshot.update(
            basket_selected_on=selected_on.isoformat(),
            basket_month=report_date.replace(day=1).isoformat(),
            basket_frozen=saved is not None or snapshot["status"] == "ready",
            basket_rebased=selected_on == report_date,
        )
        if coverage is not None:
            snapshot["coverage"] = coverage
        await c.execute(
            """INSERT INTO player_price_snapshot(player_id,report_date,result)
            VALUES(%s,%s,%s) ON CONFLICT(player_id,report_date) DO UPDATE SET
              result=EXCLUDED.result,computed_at=now()""",
            (player_id, report_date, Jsonb(snapshot)),
        )
        return snapshot

    async def board(self, c, owner_id, report_date: date, direction: str):
        roster = await self.roster(c, owner_id)
        ids = [p["id"] for p in roster]
        names = {p['id']: p['full_name'] for p in roster}
        data = (
            await (
                await c.execute(
                    """SELECT p.id,p.full_name,snap.result,snap.computed_at,
                s.last_attempt_at,s.last_success_at,s.requested_at,s.error,s.coverage
            FROM player p LEFT JOIN LATERAL (SELECT result,computed_at,report_date FROM player_price_snapshot
                WHERE player_id=p.id AND report_date<=%s ORDER BY report_date DESC LIMIT 1) snap ON true
            LEFT JOIN player_price_state s ON s.player_id=p.id WHERE p.id=ANY(%s)""",
                    (report_date, ids),
                )
            ).fetchall()
            if ids
            else []
        )
        market_rows = defaultdict(list)
        if ids:
            member_to_subject = {member: p['id'] for p in roster for member in p['member_ids']}
            end = day_end(report_date)
            result = await c.execute(
                """SELECT player_id,source_id,card_key,identity,sale_price,occurred_at,source_url,raw_title
                FROM player_price_sale WHERE player_id=ANY(%s) AND NOT excluded
                  AND occurred_at >= %s AND occurred_at < %s
                ORDER BY player_id,occurred_at DESC,source_id""",
                (list(member_to_subject), end - timedelta(days=90), end),
            )
            for sale in await result.fetchall():
                market_rows[member_to_subject[sale["player_id"]]].append(sale)
        players = []
        for row in data:
            snapshot = row["result"] or {"status": "pending", "change_pct": None, "cards": []}
            if any(not is_mainstream_card(card["identity"]) for card in snapshot["cards"]):
                snapshot = {**snapshot, "status": "insufficient", "change_pct": None,
                            "last_change_pct": None, "basket_frozen": False,
                            "cards": [card for card in snapshot["cards"]
                                      if is_mainstream_card(card["identity"])]}
            if snapshot.get("report_date") and snapshot["report_date"] != report_date.isoformat():
                snapshot = {
                    **snapshot,
                    "status": "stale",
                    "last_change_pct": snapshot.get("change_pct"),
                    "change_pct": None,
                }
            queued = row["requested_at"] is not None and (
                row["last_attempt_at"] is None or row["requested_at"] > row["last_attempt_at"]
            )
            players.append(
                {
                    **snapshot,
                    "player_id": str(row["id"]),
                    "player_name": names[row['id']],
                    "computed_at": row["computed_at"],
                    "market": calculate_market(market_rows[row["id"]], report_date),
                    "coverage": row["coverage"] or snapshot.get("coverage", {}),
                    "refresh": dict(
                        queued=queued,
                        error=row["error"],
                        last_attempt_at=row["last_attempt_at"],
                        last_success_at=row["last_success_at"],
                    ),
                }
            )
        players.sort(
            key=lambda p: (
                p["change_pct"] is None,
                (float(p["change_pct"]) * (-1 if direction == "gainers" else 1))
                if p["change_pct"] is not None
                else 0,
                p["player_name"],
            )
        )
        return dict(
            report_date=report_date.isoformat(),
            timezone=str(TZ),
            window_days=7,
            min_sales=3,
            currency="CNY",
            players=players,
            summary=dict(
                tracked=len(players),
                with_sales=sum(p["market"]["sale_count"] > 0 for p in players),
                with_trends=sum(p["market"]["reference_card"] is not None for p in players),
                with_index=sum(p["change_pct"] is not None for p in players),
                queued=sum(p["refresh"]["queued"] for p in players),
                source_errors=sum(bool(p["refresh"]["error"]) for p in players),
            ),
        )
