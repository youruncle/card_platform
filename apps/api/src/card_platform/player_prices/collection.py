"""Resumable, bounded public sampling; state is keyed by the exact query."""
from datetime import date, datetime, timedelta

import httpx

from card_platform.domain.player_aliases import query_player_aliases
from card_platform.ingestion.cardhobby import CardHobbyProvider
from card_platform.player_prices.domain import TZ, day_end
from card_platform.player_prices.subjects import name_key
from card_platform.public_source_catalog import observe_public_sale


# Search vocabulary only. These terms neither identify a subject nor enter
# the shared recognition aliases. In particular, 七七 is also a seller name.
_MARKET_NAMES = {
    name_key(name): alias
    for name, alias in {
        "stephen curry": "库里",
        "lebron james": "老詹",
        "victor wembanyama": "文班",
        "lamelo ball": "三球",
        "anthony edwards": "华子",
        "luka doncic": "东契奇",
        "cooper flagg": "弗拉格",
        "kon knueppel": "克努佩尔",
    }.items()
}
_MARKET_PRODUCTS = {"Prizm": "pz", "Topps Chrome": "tc"}


def collection_player_aliases(player_name):
    market = _MARKET_NAMES.get(name_key(player_name))
    return list(
        dict.fromkeys(
            [player_name, *query_player_aliases(player_name), *([market] if market else [])]
        )
    )


def _queries(player_name, basket):
    aliases = collection_player_aliases(player_name)
    precise = [
        " ".join(
            str(v)
            for v in [
                player_name,
                entry["identity"].get("season_start"),
                entry["identity"].get("product_name"),
                entry["identity"].get("parallel_name"),
                entry["identity"].get("card_number"),
                entry["identity"].get("grade_company"),
                entry["identity"].get("grade_value"),
            ]
            if v
        )
        for entry in basket[:3]
    ]
    market = _MARKET_NAMES.get(name_key(player_name))
    short = [
        f"{market or aliases[-1]} {_MARKET_PRODUCTS[entry['identity']['product_name']]}"
        for entry in basket[:3]
        if entry["identity"].get("product_name") in _MARKET_PRODUCTS and len(aliases) > 1
    ]
    # At most eight distinct queries: retain precise targets and two broad
    # aliases, add a market nickname and one query per abbreviated product. Sharing
    # this query across seasons/finishes leaves enough budget to advance history.
    return list(dict.fromkeys([*precise, *aliases[:2], *([market] if market else []), *short]))


def _page_allocations(queries, page_budget, allocation_round):
    count = len(queries)
    order = sorted(range(count), key=lambda index: (index - allocation_round) % count)
    budgets = [0] * count
    # First keep the fair latest-page checks, then give rotating queries two
    # extra pages together: rechecking a moved-page anchor alone cannot advance
    # its saved gap. Equal two-page allocations can otherwise stall forever.
    for index in order[:page_budget]:
        budgets[index] = 1
    remaining = max(0, page_budget - count)
    while remaining:
        for index in order:
            extra = min(2, remaining)
            budgets[index] += extra
            remaining -= extra
            if not remaining:
                break
    return budgets


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _state(previous, cutoff, end):
    previous = previous if isinstance(previous, dict) else {}
    old_cutoff = _instant(previous.get("cutoff"))
    # A historical replay requesting an older window cannot inherit completion
    # of a newer/narrower window. Forward rolling windows can retain progress.
    if old_cutoff and cutoff < old_cutoff:
        previous = {}
    try:
        next_page = max(2, int(previous.get("next_page", 2)))
    except (TypeError, ValueError):
        next_page = 2
    state = {"next_page": next_page, "complete": previous.get("complete") is True,
             "cutoff": cutoff.isoformat(), "report_end": end.isoformat(),
             "round": previous.get("round", 0) if isinstance(previous.get("round", 0), int) else 0}
    for key in ("head_at", "oldest_observed_at", "recent_boundary_at", "pending_head_at"):
        instant = _instant(previous.get(key))
        if instant:
            state[key] = instant.isoformat()
    for key in ("head_ids", "pending_head_ids", "resume_anchor_ids"):
        if isinstance(previous.get(key), list):
            state[key] = [str(value) for value in previous[key][:60]
                          if isinstance(value, (str, int))]
    if "recent_boundary_at" in state:
        try:
            state["recent_next_page"] = max(2, int(previous.get("recent_next_page", 2)))
        except (TypeError, ValueError):
            state["recent_next_page"] = 2
        if isinstance(previous.get("resume_anchor_page"), int):
            state["resume_anchor_page"] = max(2, previous["resume_anchor_page"])
    old_end = _instant(previous.get("report_end"))
    if old_end and end > old_end:
        # Earlier runs deliberately did not ingest rows beyond their report
        # end. Revisit that entire deferred interval when it becomes eligible.
        boundary = _instant(state.get("recent_boundary_at") or state.get("head_at"))
        state["recent_boundary_at"] = min(old_end, boundary or old_end).isoformat()
        state["recent_next_page"] = 2
        for key in ("pending_head_at", "pending_head_ids", "resume_anchor_page", "resume_anchor_ids"):
            state.pop(key, None)
    return state


def _page_dates(rows):
    dates = []
    for row in rows:
        try:
            dates.append(datetime.strptime(row["EffectiveDate"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ))
        except (KeyError, TypeError, ValueError):
            continue
    return dates


def _close_gap(state):
    if state.get("pending_head_at"):
        state["head_at"] = state["pending_head_at"]
    if state.get("pending_head_ids"):
        state["head_ids"] = state["pending_head_ids"]
    for key in ("recent_next_page", "recent_boundary_at", "pending_head_at", "pending_head_ids",
                "resume_anchor_page", "resume_anchor_ids"):
        state.pop(key, None)


def _crossed_boundary(state, dates, ids, boundary):
    # Many auctions end in the same second. Equal timestamps alone do not
    # prove we have reached any previously observed physical listing.
    return bool(dates and min(dates) < boundary)


def _pending_head(state, newest, ids):
    if newest:
        state["pending_head_at"] = newest.isoformat()
        state["pending_head_ids"] = ids[:60]


def _restart_gap(state, newest, ids):
    state["recent_next_page"] = 2
    for key in ("resume_anchor_page", "resume_anchor_ids"):
        state.pop(key, None)
    _pending_head(state, newest, ids)


async def collect_player(
    player_name: str, basket: list[dict], report_date: date, *, provider=None,
    page_budget=12, query_state=None,
) -> tuple[list[dict], dict]:
    """Reserve a latest-page check per query, then resume its oldest gap.

    Offset search is a bounded observation, not a claim of complete market
    coverage. A burst of new sales gets a separate cursor so it cannot silently
    disappear between the latest page and the historical scan.
    """
    if type(page_budget) is not int or page_budget < 0:
        raise ValueError('invalid_price_collection_budget')
    provider = provider or CardHobbyProvider(None, "", min_interval=1.0)
    queries = _queries(player_name, basket)
    cutoff, end = day_end(report_date) - timedelta(days=90), day_end(report_date)
    previous = query_state if isinstance(query_state, dict) else {}
    states = {query: _state(previous.get(query), cutoff, end) for query in queries}
    allocation_round = max(state["round"] for state in states.values())
    observations, errors, query_counts = [], [], {}
    pages = 0
    headers = {"Referer": "https://www.cardhobby.com.cn/",
               "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/139 Safari/537.36"}

    async with httpx.AsyncClient(timeout=provider.timeout, headers=headers) as client:
        async def fetch(query, page, state):
            nonlocal pages
            pages += 1
            try:
                payload = await provider._get_search_payload(client, query, page)
                rows = payload["data"]["PagedMarketItemList"]
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("invalid public search page")
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                errors.append({"query": query, "page": page, "reason": "卡淘查询失败，稍后重试"})
                return None
            query_counts[query] = query_counts.get(query, 0) + len(rows)
            for row in rows:
                observation = observe_public_sale(row)
                if observation and cutoff <= _instant(observation["sale"]["occurred_at"]) < end:
                    observations.append(observation)
            dates = _page_dates(rows)
            if dates:
                oldest = _instant(state.get("oldest_observed_at"))
                state["oldest_observed_at"] = min([*dates, *([oldest] if oldest else [])]).isoformat()
            # Only trust a date boundary when every source row supplied a date.
            ended = len(rows) < 60 or (len(dates) == len(rows) and dates and min(dates) < cutoff)
            ids = [str(row["ID"]) for row in rows if isinstance(row.get("ID"), (str, int))]
            return dates, bool(ended), ids

        allocations = _page_allocations(queries, page_budget, allocation_round)
        for index, query in enumerate(queries):
            budget = allocations[index]
            if budget <= 0:
                continue
            state = states[query]
            state["round"] += 1
            first = await fetch(query, 1, state)
            if first is None:
                continue
            dates, ended, ids = first
            newest = max(dates, default=None)
            boundary = _instant(state.get("recent_boundary_at") or state.get("head_at"))
            if (not state.get('head_at') or not state.get('head_ids')) and not ended and state['complete']:
                # A formerly empty query had no nonempty head to bound an
                # increment. Its first full page starts a fresh history scan.
                state['complete'] = False
                state['next_page'] = 2
            first_dates, first_ended, first_ids = dates, ended, ids
            same_head = (bool(ids) and set(ids) == set(state.get('head_ids', []))
                         and newest == _instant(state.get('head_at'))
                         and 'recent_next_page' not in state)
            if boundary and not (ended or same_head or _crossed_boundary(state, dates, ids, boundary)):
                state["recent_boundary_at"] = boundary.isoformat()
                if "recent_next_page" not in state:
                    _restart_gap(state, newest, ids)
                elif not state.get("pending_head_at"):
                    _pending_head(state, newest, ids)
            else:
                _pending_head(state, newest, ids)
                _close_gap(state)
            if ended:
                state["complete"] = True

            anchor_checked = False
            for _ in range(budget - 1):
                recent = "recent_next_page" in state
                if not recent and state["complete"]:
                    break
                if recent and state.get("resume_anchor_page") and not anchor_checked:
                    # Offset pages move when new auctions arrive. Re-read the
                    # last captured page before resuming beyond it; a changed
                    # anchor invalidates the offset and restarts the gap.
                    anchor_page = state["resume_anchor_page"]
                    checked = await fetch(query, anchor_page, state)
                    if checked is None:
                        break
                    dates, ended, ids = checked
                    anchor_checked = True
                    if set(ids) == set(state.get("resume_anchor_ids", [])):
                        continue
                    _restart_gap(state, newest, first_ids)
                    if anchor_page != 2:
                        continue
                    page = 2  # The newly read page is the gap's first page.
                    result = checked
                else:
                    page = state["recent_next_page"] if recent else state["next_page"]
                    result = await fetch(query, page, state)
                if result is None:
                    break  # Failed page remains the next page for a later run.
                dates, ended, ids = result
                if recent:
                    boundary = _instant(state["recent_boundary_at"])
                    if ended or _crossed_boundary(state, dates, ids, boundary):
                        _close_gap(state)
                        committed = _instant(state.get("head_at"))
                        same_head = set(first_ids) == set(state.get("head_ids", []))
                        if (committed and not first_ended and not same_head
                                and not _crossed_boundary(state, first_dates, first_ids, committed)):
                            state["recent_boundary_at"] = committed.isoformat()
                            _restart_gap(state, newest, first_ids)
                        else:
                            _pending_head(state, newest, first_ids)
                            _close_gap(state)
                    else:
                        state["recent_next_page"] = page + 1
                        state["resume_anchor_page"] = page
                        state["resume_anchor_ids"] = ids[:60]
                        anchor_checked = True
                else:
                    state["next_page"] = page + 1
                if ended:
                    state["complete"] = True

    return observations, dict(
        source="cardhobby", scope="bounded_sample", pages=pages, page_budget=page_budget,
        queries=query_counts, errors=errors, budget_exhausted=pages >= page_budget,
        history_queries=states,
        history_complete=all(state["complete"] for state in states.values()),
        incremental_complete=not errors and all(
            query in query_counts and "recent_next_page" not in state
            for query, state in states.items()
        ),
    )
