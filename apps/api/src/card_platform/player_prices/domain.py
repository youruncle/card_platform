from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
MIN_SALES = 3


def unique_sales(rows):
    """Ambiguous assignments/prices for the same sale are not market evidence."""
    by_source = defaultdict(list)
    for row in rows:
        price = Decimal(str(row["sale_price"]))
        if price.is_finite() and price > 0 and row["occurred_at"].tzinfo is not None:
            by_source[row["source_id"]].append(row)
    result = []
    for matches in by_source.values():
        facts = {(r["card_key"], Decimal(str(r["sale_price"])), r["occurred_at"]) for r in matches}
        if len(facts) == 1:
            result.append(matches[0])
    return result


def day_end(report_date: date) -> datetime:
    return datetime.combine(report_date + timedelta(days=1), time.min, TZ)


def select_basket(rows, report_date: date) -> list[dict]:
    end = day_end(report_date)
    groups = defaultdict(list)
    for row in unique_sales(rows):
        if end - timedelta(days=90) <= row["occurred_at"] < end:
            groups[row["card_key"]].append(row)
    def priority(key):
        sales = groups[key]
        current = sum(end - timedelta(days=7) <= r["occurred_at"] < end for r in sales)
        previous = sum(end - timedelta(days=8) <= r["occurred_at"] < end - timedelta(days=1) for r in sales)
        return (not (current >= MIN_SALES and previous >= MIN_SALES), -min(current, previous), -len(sales), key)

    keys = sorted(groups, key=priority)[:3]
    return [
        dict(card_key=key, identity=groups[key][0]["identity"], selection_count=len(groups[key]))
        for key in keys
    ]


def calculate_snapshot(rows, basket: list[dict], report_date: date) -> dict:
    end = day_end(report_date)
    current_start, previous_end = end - timedelta(days=7), end - timedelta(days=1)
    previous_start = end - timedelta(days=8)
    groups = defaultdict(list)
    for row in unique_sales(rows):
        if previous_start <= row["occurred_at"] < end:
            groups[row["card_key"]].append(row)
    cards, changes = [], []
    for entry in basket:
        sales = groups[entry["card_key"]]
        current = [r for r in sales if current_start <= r["occurred_at"] < end]
        previous = [r for r in sales if previous_start <= r["occurred_at"] < previous_end]
        now_price = median([Decimal(str(r["sale_price"])) for r in current]) if current else None
        before_price = (
            median([Decimal(str(r["sale_price"])) for r in previous]) if previous else None
        )
        ready = len(current) >= MIN_SALES and len(previous) >= MIN_SALES
        change = (now_price / before_price - 1) * 100 if ready else None
        if ready:
            changes.append(change)
        cards.append(
            {
                **entry,
                "status": "ready" if ready else "insufficient",
                "current_price": str(now_price) if now_price is not None else None,
                "previous_price": str(before_price) if before_price is not None else None,
                "current_count": len(current),
                "previous_count": len(previous),
                "change_pct": str(change.quantize(Decimal(".0001"))) if ready else None,
                "latest_sale_at": max((r["occurred_at"] for r in sales), default=None),
                "sources": [
                    dict(
                        source_id=r["source_id"],
                        raw_title=r["raw_title"],
                        sale_price=str(r["sale_price"]),
                        occurred_at=r["occurred_at"],
                        source_url=r["source_url"],
                    )
                    for r in sorted(sales, key=lambda r: r["occurred_at"], reverse=True)[:30]
                ],
            }
        )
    ready = len(basket) == 3 and len(changes) == 3
    result = dict(
        report_date=report_date.isoformat(),
        status="ready" if ready else "insufficient",
        change_pct=str((sum(changes) / 3).quantize(Decimal(".0001"))) if ready else None,
        cards=cards,
        current_window={"start": current_start.isoformat(), "end": end.isoformat()},
        previous_window={"start": previous_start.isoformat(), "end": previous_end.isoformat()},
    )
    # Persistable/public JSON without losing numeric precision.
    for card in cards:
        if card["latest_sale_at"] is not None:
            card["latest_sale_at"] = card["latest_sale_at"].isoformat()
        for source in card["sources"]:
            source["occurred_at"] = source["occurred_at"].isoformat()
    return result


def calculate_market(rows, report_date: date) -> dict:
    """Observed exact-card prices, independent of eligibility for a player index."""
    end = day_end(report_date)
    current_start, previous_start = end - timedelta(days=30), end - timedelta(days=60)
    groups = defaultdict(list)
    for row in unique_sales(rows):
        if end - timedelta(days=90) <= row["occurred_at"] < end:
            groups[row["card_key"]].append(row)
    cards = []
    for key, sales in groups.items():
        current = [r for r in sales if current_start <= r["occurred_at"] < end]
        previous = [r for r in sales if previous_start <= r["occurred_at"] < current_start]
        now = median([Decimal(str(r["sale_price"])) for r in current]) if current else None
        before = median([Decimal(str(r["sale_price"])) for r in previous]) if previous else None
        ready = len(current) >= MIN_SALES and len(previous) >= MIN_SALES
        latest = max(sales, key=lambda r: r["occurred_at"])
        cards.append(dict(
            card_key=key, identity=latest["identity"], selection_count=len(sales),
            status="ready" if ready else "insufficient",
            current_price=str(now) if now is not None else None,
            previous_price=str(before) if before is not None else None,
            current_count=len(current), previous_count=len(previous),
            change_pct=str(((now / before - 1) * 100).quantize(Decimal(".0001"))) if ready else None,
            latest_sale_at=latest["occurred_at"].isoformat(),
            sources=[dict(source_id=r["source_id"], raw_title=r["raw_title"],
                          sale_price=str(r["sale_price"]), occurred_at=r["occurred_at"].isoformat(),
                          source_url=r["source_url"])
                     for r in sorted(sales, key=lambda r: (r["occurred_at"], r["source_id"]), reverse=True)[:30]],
        ))
    cards.sort(key=lambda c: (c["status"] != "ready", -c["current_count"], -c["selection_count"], c["card_key"]))
    # One disclosed exact card represents this signal at 100% weight. Select
    # across every qualified card before limiting the detail list to 20.
    reference = min(
        (card for card in cards if card['status'] == 'ready'),
        key=lambda card: (-card['selection_count'], card['card_key']),
        default=None,
    )
    return dict(
        report_date=report_date.isoformat(), window_days=30, min_sales=MIN_SALES,
        sale_count=sum(len(sales) for sales in groups.values()), card_count=len(cards),
        trend_count=sum(c["status"] == "ready" for c in cards),
        reference_card=reference,
        latest_sale_at=max((c["latest_sale_at"] for c in cards), key=datetime.fromisoformat, default=None),
        cards=cards[:20], cards_truncated=len(cards) > 20,
        current_window=dict(start=current_start.isoformat(), end=end.isoformat()),
        previous_window=dict(start=previous_start.isoformat(), end=current_start.isoformat()),
    )
