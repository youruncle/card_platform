from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from card_platform.auth import current_user
from card_platform.player_prices.api import router


def client():
    app = FastAPI()
    app.include_router(router)

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace()

    app.state.database = SimpleNamespace(connection=connection)
    return app, TestClient(app)


def test_routes_require_login():
    _, c = client()
    assert c.get("/v1/player-prices").status_code == 401
    assert c.post("/v1/player-prices/refresh").status_code == 401


def test_read_and_refresh_use_authenticated_owner(monkeypatch):
    app, c = client()
    owner = uuid4()
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=owner)
    from card_platform.player_prices import api

    monkeypatch.setattr(
        api, "get_settings", lambda: SimpleNamespace(player_price_worker_enabled=True)
    )

    async def board(connection, owner_id, report_date, direction):
        assert owner_id == owner
        assert report_date == date(2026, 10, 10)
        assert direction == "losers"
        return {"players": [], "report_date": report_date.isoformat()}

    async def enqueue(connection, owner_id):
        assert owner_id == owner
        return 2

    monkeypatch.setattr(api.repo, "board", board)
    monkeypatch.setattr(api.repo, "enqueue", enqueue)
    assert c.get("/v1/player-prices?date=2026-10-10&direction=losers").json()["players"] == []
    response = c.post("/v1/player-prices/refresh")
    assert response.status_code == 202 and response.json()["queued"] == 2
    assert c.get("/v1/player-prices?date=2099-01-01").status_code == 422
    assert c.get("/v1/player-prices?direction=invalid").status_code == 422


def test_refresh_reports_disabled_worker(monkeypatch):
    app, c = client()
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=uuid4())
    from card_platform.player_prices import api

    monkeypatch.setattr(
        api, "get_settings", lambda: SimpleNamespace(player_price_worker_enabled=False)
    )
    assert c.post("/v1/player-prices/refresh").status_code == 503
