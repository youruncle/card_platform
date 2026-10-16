from datetime import date as Date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.config import get_settings
from card_platform.player_prices.domain import TZ
from card_platform.player_prices.repository import PriceRepository

router = APIRouter(prefix="/v1/player-prices", tags=["player-prices"])
repo = PriceRepository()


@router.get("")
async def board(
    request: Request,
    date: Date | None = Query(None),
    direction: Literal["gainers", "losers"] = "gainers",
    user: AuthenticatedUser = Depends(current_user),
):
    last_completed = datetime.now(TZ).date() - timedelta(days=1)
    report_date = date or last_completed
    if report_date > last_completed:
        raise HTTPException(422, "只能查看已结束自然日的统计")
    async with request.app.state.database.connection() as c:
        data = await repo.board(c, user.id, report_date, direction)
    data["worker_enabled"] = get_settings().player_price_worker_enabled
    return data


@router.post("/refresh", status_code=202)
async def refresh(request: Request, user: AuthenticatedUser = Depends(current_user)):
    if not get_settings().player_price_worker_enabled:
        raise HTTPException(503, "每日价格后台未启用；请启用后再请求采集")
    async with request.app.state.database.connection() as c:
        count = await repo.enqueue(c, user.id)
    return {"queued": count, "message": "已排队，后台将补采公开成交并更新前一天统计"}
