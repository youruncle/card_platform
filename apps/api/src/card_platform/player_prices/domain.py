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
    keys = sorted(groups, key=lambda k: (-len(groups[k]), k))[:3]
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
