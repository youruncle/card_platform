"""Sort the repository's final display projection; bound, private process-local snapshots."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from secrets import token_urlsafe
from time import monotonic
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field

from card_platform.schemas import PortfolioItem, PortfolioPage, PortfolioSummary

SortBy = Literal["created_at", "player_name", "season_start", "remaining_cost", "fair_value", "unrealized_profit", "relevance"]
SortOrder = Literal["asc", "desc"]


class PortfolioProjection(PortfolioItem):
    # Internal ordering metadata does not change the existing item wire contract.
    created_at: datetime = Field(exclude=True)
    cost_comparable: bool = Field(default=True, exclude=True)


class SortedPortfolioPage(PortfolioPage):
    summary: PortfolioSummary | None = None
    snapshot_token: str | None = None
    snapshot_at: datetime | None = None


def sorted_items(items, sort_by: SortBy, order: SortOrder, base_currency: str):
    def value(item):
        if sort_by == "fair_value" and item.valuation_currency != base_currency:
            return None
        if sort_by == "remaining_cost" and not getattr(item, "cost_comparable", True):
            return None
        result = getattr(item, sort_by, None)
        if isinstance(result, str):
            result = " ".join(result.casefold().split()) or None
        return result

    # Stable secondary key never reverses when the primary direction changes.
    known, unknown = [], []
    for item in sorted(items, key=lambda item: item.id):
        key = value(item)
        (unknown if key is None else known).append((key, item))
    known.sort(key=lambda pair: pair[0], reverse=order == "desc")
    return [item for _, item in known + unknown]


def stale_snapshot():
    return HTTPException(
        409, {"code": "snapshot_expired", "message": "列表快照已失效，请从第一页刷新"}
    )


@dataclass(frozen=True)
class Snapshot:
    owner_id: UUID
    binding: tuple
    expires: float
    at: datetime
    ids: tuple[UUID, ...]
    rows: tuple[bytes, ...]
    size: int
    summary: PortfolioSummary | None = None


class SnapshotCache:
    def __init__(self, *, max_bytes=None, ttl=60, max_entries=128):
        self.max_bytes = (
            max_bytes
            if max_bytes is not None
            else int(os.environ.get("PORTFOLIO_SNAPSHOT_MAX_BYTES", str(32 * 1024 * 1024)))
        )
        self.ttl, self.max_entries = ttl, max_entries
        self.entries = OrderedDict()
        self.size = 0

    def discard(self, token):
        snapshot = self.entries.pop(token, None)
        if snapshot:
            self.size -= snapshot.size

    def prune(self):
        now = monotonic()
        for token, snapshot in list(self.entries.items()):
            if snapshot.expires <= now:
                self.discard(token)

    def create(self, owner_id, binding, items, summary=None, *, ids=None):
        self.prune()
        rows = tuple(item.model_dump_json().encode() for item in items)
        member_ids = tuple(ids) if ids is not None else tuple(item.id for item in items)
        # Account for per-row bytes, UUID and tuple references as well as payload.
        size = sum(len(row) + 160 for row in rows) + len(repr(binding).encode()) + 1024 + len(member_ids) * 64
        if summary is not None:
            size += len(summary.model_dump_json().encode()) + 1024
        if size > self.max_bytes or self.max_entries < 1:
            raise HTTPException(
                503, {"code": "snapshot_capacity", "message": "列表快照容量不足，请稍后重试"}
            )
        owned = [token for token, value in self.entries.items() if value.owner_id == owner_id]
        for token in owned[:-2]:
            self.discard(token)
        while self.entries and (
            self.size + size > self.max_bytes or len(self.entries) >= self.max_entries
        ):
            self.discard(next(iter(self.entries)))
        token = token_urlsafe(32)
        snapshot = Snapshot(
            owner_id,
            binding,
            monotonic() + self.ttl,
            datetime.now(timezone.utc),
            member_ids,
            rows,
            size,
            summary,
        )
        self.entries[token] = snapshot
        self.size += size
        return token, snapshot

    def get(self, token, owner_id, binding):
        self.prune()
        snapshot = self.entries.get(token)
        if snapshot is None or snapshot.owner_id != owner_id or snapshot.binding != binding:
            raise stale_snapshot()
        return snapshot


snapshots = SnapshotCache()


async def sorted_page(
    connection,
    repository,
    user,
    *,
    filters,
    candidate_ids,
    sort_by,
    order,
    page,
    page_size,
    snapshot,
    snapshot_token,
    include_summary=False,
    snapshot_mode="full",
):
    membership = snapshot_mode == "membership" and sort_by == "created_at" and not include_summary
    binding = (filters.model_dump_json() if filters else "{}", sort_by, order, user.base_currency, membership)
    if membership:
        cached = snapshots.get(snapshot_token, user.id, binding) if snapshot_token else None
        if cached:
            result = await connection.execute(
                "SELECT id FROM collection_item WHERE owner_id=%s AND deleted_at IS NULL AND id=ANY(%s::uuid[])",
                (user.id, list(cached.ids)),
            )
            if {row["id"] for row in await result.fetchall()} != set(cached.ids):
                snapshots.discard(snapshot_token)
                raise stale_snapshot()
            ids = cached.ids
        else:
            # SQL controls the global order; expensive display projection is page-only.
            direction = "ASC" if order == "asc" else "DESC"
            candidate_sql = " AND id=ANY(%s::uuid[])" if candidate_ids is not None else ""
            params = (user.id, candidate_ids) if candidate_ids is not None else (user.id,)
            result = await connection.execute(
                "SELECT id FROM collection_item WHERE owner_id=%s AND deleted_at IS NULL"
                + candidate_sql + f" ORDER BY created_at {direction}, id ASC", params,
            )
            ids = tuple(row["id"] for row in await result.fetchall())
            if snapshot:
                snapshot_token, cached = snapshots.create(user.id, binding, [], ids=ids)
        total_pages = max(1, (len(ids) + page_size - 1) // page_size)
        page = min(page, total_pages)
        selected = list(ids[(page - 1) * page_size:page * page_size])
        items = await repository.list_items(connection, user.id, item_ids=selected) if selected else []
        if {item.id for item in items} != set(selected):
            if snapshot_token:
                snapshots.discard(snapshot_token)
            raise stale_snapshot()
        rank = {item_id: index for index, item_id in enumerate(selected)}
        items.sort(key=lambda item: rank[item.id])
        return SortedPortfolioPage(items=items, total=len(ids), page=page, page_size=page_size,
            total_pages=total_pages, snapshot_token=snapshot_token,
            snapshot_at=cached.at if cached else None)
    if snapshot_token:
        cached = snapshots.get(snapshot_token, user.id, binding)
        # current_user validates the session on every request. Recheck every cached ID,
        # including off-page rows, so deleted/transferred cards invalidate the snapshot.
        result = await connection.execute(
            "SELECT id FROM collection_item WHERE owner_id=%s AND deleted_at IS NULL AND id=ANY(%s::uuid[])",
            (user.id, list(cached.ids)),
        )
        if {row["id"] for row in await result.fetchall()} != set(cached.ids):
            snapshots.discard(snapshot_token)
            raise stale_snapshot()
        rows = cached.rows
        total_pages = max(1, (len(rows) + page_size - 1) // page_size)
        page = min(page, total_pages)
        items = [
            PortfolioItem.model_validate_json(row)
            for row in rows[(page - 1) * page_size : page * page_size]
        ]
        return SortedPortfolioPage(
            items=items,
            total=len(rows),
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            snapshot_token=snapshot_token,
            snapshot_at=cached.at,
            summary=cached.summary if include_summary else None,
        )
    if sort_by == "created_at" and not snapshot and not include_summary:
        direction = "ASC" if order == "asc" else "DESC"
        candidate_sql = " AND id=ANY(%s::uuid[])" if candidate_ids is not None else ""
        params = (user.id, candidate_ids) if candidate_ids is not None else (user.id,)
        count = await connection.execute("SELECT count(*) AS total FROM collection_item WHERE owner_id=%s AND deleted_at IS NULL" + candidate_sql, params)
        total = (await count.fetchone())["total"]
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        result = await connection.execute("SELECT id FROM collection_item WHERE owner_id=%s AND deleted_at IS NULL" + candidate_sql + f" ORDER BY created_at {direction}, id ASC LIMIT %s OFFSET %s", params + (page_size, (page - 1) * page_size))
        ids = [row["id"] for row in await result.fetchall()]
        items = await repository.list_items(connection, user.id, item_ids=ids) if ids else []
        rank = {item_id: index for index, item_id in enumerate(ids)}
        items.sort(key=lambda item: rank[item.id])
        return SortedPortfolioPage(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)
    items = await repository.list_items(connection, user.id, item_ids=None if include_summary else candidate_ids)
    summary = None
    if include_summary:
        from card_platform.repository import build_portfolio_summary

        summary = build_portfolio_summary(items, user.id, user.base_currency)
        if candidate_ids is not None:
            allowed = set(candidate_ids)
            items = [item for item in items if item.id in allowed]
    if sort_by == "relevance" and candidate_ids is not None:
        rank = {item_id: index for index, item_id in enumerate(candidate_ids)}
        items = sorted(items, key=lambda item: rank[item.id], reverse=order == "asc")
    else:
        items = sorted_items(items, "created_at" if sort_by == "relevance" else sort_by, order, user.base_currency)
    token, at = None, None
    if snapshot:
        token, cached = snapshots.create(user.id, binding, items, summary)
        at = cached.at
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(page, total_pages)
    return SortedPortfolioPage(
        items=items[(page - 1) * page_size : page * page_size],
        total=len(items),
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        snapshot_token=token,
        snapshot_at=at,
        summary=summary,
    )
