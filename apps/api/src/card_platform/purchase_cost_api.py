from uuid import UUID
from decimal import Decimal
from fastapi.encoders import jsonable_encoder
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg import AsyncConnection
from pydantic import Field, StrictBool

from card_platform.api import get_connection, translate_domain_error
from card_platform.auth import AuthenticatedUser, current_user
from card_platform.purchase_cost import StrictModel, OrderCapture
from card_platform.purchase_cost_service import PurchaseCostService
from card_platform.repository import ConflictError, NotFoundError

router = APIRouter(prefix="/v1/purchase-cost", dependencies=[Depends(current_user)])
service = PurchaseCostService()


class SyncRequest(StrictModel):
    orders: list[OrderCapture] = Field(min_length=1, max_length=100)


class ConfirmRequest(StrictModel):
    unit_id: UUID
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    refresh: StrictBool = False


class JobRequest(StrictModel):
    target: int = Field(ge=1, le=500)
    scan_limit: int = Field(default=1000, ge=1, le=2000)
    request_key: UUID
    source_mode: Literal["synced", "browser_search"] = "synced"


class SearchResultRequest(StrictModel):
    orders: list[OrderCapture] = Field(default_factory=list, max_length=100)
    complete: StrictBool
    refresh: StrictBool = False


class SearchStepRequest(SearchResultRequest):
    item_id: UUID


async def domain(call):
    try:
        return jsonable_encoder(await call, custom_encoder={Decimal: str})
    except (ConflictError, NotFoundError) as error:
        raise translate_domain_error(error) from error


@router.post("/sync")
async def sync(
    payload: SyncRequest,
    user: AuthenticatedUser = Depends(current_user),
    conn: AsyncConnection = Depends(get_connection),
):
    if sum(sum(line.quantity for line in order.lines) for order in payload.orders) > 10000:
        raise HTTPException(422, "单次同步件数超过 10000")
    return await domain(service.sync(conn, user.id, payload.orders))


@router.get("/items/{item_id}/candidates")
async def candidates(
    item_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    conn: AsyncConnection = Depends(get_connection),
):
    return await domain(service.candidates(conn, user.id, item_id))


@router.post("/items/{item_id}/confirm")
async def confirm(
    item_id: UUID,
    payload: ConfirmRequest,
    user: AuthenticatedUser = Depends(current_user),
    conn: AsyncConnection = Depends(get_connection),
):
    return await domain(
        service.confirm(conn, user.id, item_id, payload.unit_id, payload.evidence_hash, refresh=payload.refresh)
    )


@router.get("/items/{item_id}/audit")
async def audit(
    item_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    conn: AsyncConnection = Depends(get_connection),
):
    return await domain(service.audit(conn, user.id, item_id))


@router.post("/jobs")
async def create_job(
    payload: JobRequest,
    user: AuthenticatedUser = Depends(current_user),
    conn: AsyncConnection = Depends(get_connection),
):
    return await domain(
        service.create_job(conn, user.id, payload.target, payload.scan_limit, payload.request_key, payload.source_mode)
    )


@router.get("/jobs")
async def jobs(
    user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)
):
    rows = await conn.execute(
        "SELECT * FROM cardhobby_cost_job WHERE owner_id=%s ORDER BY created_at DESC LIMIT 10",
        (user.id,),
    )
    return await rows.fetchall()


@router.get("/jobs/{job_id}")
async def job(
    job_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    conn: AsyncConnection = Depends(get_connection),
):
    return await domain(service.job(conn, user.id, job_id))


@router.get("/items/{item_id}/search-plan")
async def search_plan(item_id: UUID, user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    return await domain(service.search_plan(conn, user.id, item_id, refresh=True))


@router.post("/items/{item_id}/search-result")
async def search_result(item_id: UUID, payload: SearchResultRequest, user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    if sum(sum(line.quantity for line in order.lines) for order in payload.orders) > 10000:
        raise HTTPException(422, "单次搜索件数超过 10000")
    return await domain(service.resolve_search(conn, user.id, item_id, payload.orders, payload.complete, refresh=payload.refresh))


@router.post("/jobs/{job_id}/search-step")
async def search_step(job_id: UUID, payload: SearchStepRequest, user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    if sum(sum(line.quantity for line in order.lines) for order in payload.orders) > 10000:
        raise HTTPException(422, "单次搜索件数超过 10000")
    return await domain(service.finish_search_step(conn, user.id, job_id, payload.item_id, payload.orders, payload.complete))


@router.post("/jobs/{job_id}/{action}")
async def action(
    job_id: UUID,
    action: Literal["cancel", "retry", "search-failed"],
    user: AuthenticatedUser = Depends(current_user),
    conn: AsyncConnection = Depends(get_connection),
):
    return await domain(service.job(conn, user.id, job_id, action))
