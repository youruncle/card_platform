"""Atlas management under the existing authenticated admin knowledge router."""

from __future__ import annotations

from contextlib import closing
import json
from typing import Any, Literal
from uuid import UUID
import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from card_platform.auth import current_user
from card_platform.knowledge_atlas_corrections import CorrectionRepository, FIELDS, fingerprint
from card_platform.knowledge_browser_api import Browser, Limit, Offset, Search, _read
from card_platform.knowledge_repository import (
    KnowledgeConflictError,
    KnowledgePermissionError,
    KnowledgeRepository,
)

router = APIRouter(prefix="/atlas", tags=["knowledge-atlas"])
repository = KnowledgeRepository()
corrections = CorrectionRepository()


class Proposal(BaseModel):
    card_id: str = Field(min_length=1, max_length=500)
    base_version_id: UUID
    base_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    changes: dict[str, Any] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=3, max_length=2000)
    evidence: list[str] = Field(min_length=1, max_length=10)


class Review(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=3, max_length=2000)


async def _role(request, user, allowed):
    workspace = await repository.workspace(request.state.connection)
    try:
        role = await repository.require_role(
            request.state.connection, workspace["id"], user.id, allowed
        )
    except KnowledgePermissionError as error:
        raise HTTPException(403, str(error)) from error
    return workspace, role


@router.get("/permissions")
async def permissions(request: Request, user=Depends(current_user)):
    workspace = await repository.workspace(request.state.connection)
    try:
        role = await repository.require_role(
            request.state.connection,
            workspace["id"],
            user.id,
            {"viewer", "editor", "reviewer", "publisher"},
        )
    except KnowledgePermissionError:
        role = "viewer"
    return {
        "role": role,
        "can_propose": role in {"editor", "admin"},
        "can_review": role in {"reviewer", "admin"},
    }


def _summary(browser):
    with closing(browser._connect()) as db:
        counts = {
            row[0]: row[1]
            for row in db.execute("SELECT identity,count(*) FROM atlas_state GROUP BY identity")
        }
        images = {
            row[0]: row[1]
            for row in db.execute("SELECT image,count(*) FROM atlas_state GROUP BY image")
        }
        return {
            "version": browser.version,
            "version_id": browser.version_id,
            "source_records": sum(counts.values()),
            "confirmed": counts.get("confirmed", 0),
            "pending": counts.get("pending", 0),
            "conflict": counts.get("conflict", 0),
            "image_states": images,
            "global_unique_atlases": None,
        }


@router.get("/summary")
async def summary(browser: Browser):
    return await asyncio.to_thread(_summary, browser)


def _navigation(browser, manufacturer, season, brand, q):
    with closing(browser._connect()) as db:
        where, values = [], []
        if manufacturer:
            where.append("manufacturer=? COLLATE NOCASE")
            values.append(manufacturer)
        if season is not None:
            where.append("season=?")
            values.append(season)
        if q:
            where.append("name LIKE ?")
            values.append("%" + q + "%")
        clause = " AND ".join(where) or "1=1"
        rows = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT data FROM products WHERE " + clause + " ORDER BY name", values
            )
        ]
        brands = sorted({row["brand"] for row in rows if row.get("brand")})
        products = [
            {"id": row["id"], "name": row["name"], "brand": row.get("brand")}
            for row in rows
            if not brand or row.get("brand") == brand
        ]
        return {"brands": brands, "products": products}


@router.get("/navigation")
async def navigation(
    browser: Browser,
    manufacturer: Search = None,
    season: int | None = None,
    brand: Search = None,
    q: Search = None,
):
    return await asyncio.to_thread(_navigation, browser, manufacturer, season, brand, q)


@router.get("/cards")
async def cards(
    browser: Browser,
    product_id: Search = None,
    manufacturer: Search = None,
    season: int | None = None,
    brand: Search = None,
    section: Search = None,
    player: Search = None,
    card_number: Search = None,
    q: Search = None,
    identity_state: Literal["any", "confirmed", "pending", "conflict"] = "any",
    image_state: Literal["any", "none", "single", "both"] = "any",
    offset: Offset = 0,
    limit: Limit = 40,
):
    return await _read(
        browser,
        "cards",
        product_id=product_id,
        manufacturer=manufacturer,
        season=season,
        brand=brand,
        section=section,
        player=player,
        card_number=card_number,
        q=q,
        identity_state=identity_state,
        image_state=image_state,
        offset=offset,
        limit=limit,
    )


@router.get("/detail")
async def detail(browser: Browser, card_id: str = Query(min_length=1, max_length=500)):
    value = await _read(browser, "card", card_id)
    card = value["card"]
    value.update(
        version_id=browser.version_id,
        base_fingerprint=fingerprint(card),
        field_sources={
            key: card.get("field_sources", {}).get(
                key, {"basis": "record_level_not_field_verified", "sources": card["sources"]}
            )
            for key in FIELDS
        },
    )
    return value


@router.get("/proposals")
async def proposals(
    request: Request, card_id: Search = None, offset: Offset = 0, limit: Limit = 100
):
    workspace = await repository.workspace(request.state.connection)
    items = await corrections.list(
        request.state.connection, workspace["id"], card_id, limit, offset
    )
    return {"items": items, "offset": offset, "limit": limit}


@router.post("/proposals", status_code=201)
async def propose(
    payload: Proposal,
    request: Request,
    browser: Browser,
    idempotency_key: str | None = Header(default=None, max_length=200),
    user=Depends(current_user),
):
    async with request.state.connection.transaction():
        workspace, _ = await _role(request, user, {"editor"})
        try:
            return await corrections.submit(
                request.state.connection,
                workspace,
                user.id,
                payload.model_dump(mode="json"),
                idempotency_key,
                browser,
            )
        except KnowledgeConflictError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "图鉴不存在") from error


@router.post("/proposals/{proposal_id}/review")
async def review(
    proposal_id: UUID,
    payload: Review,
    request: Request,
    browser: Browser,
    idempotency_key: str | None = Header(default=None, max_length=200),
    user=Depends(current_user),
):
    async with request.state.connection.transaction():
        workspace, _ = await _role(request, user, {"reviewer"})
        try:
            return await corrections.review(
                request.state.connection,
                workspace,
                user.id,
                str(proposal_id),
                payload.decision,
                payload.reason,
                idempotency_key,
                browser,
            )
        except KnowledgeConflictError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "提案不存在") from error
