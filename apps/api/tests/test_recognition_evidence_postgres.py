"""The actual scanner, stored JSON and purchase reader share one evidence contract."""

import os
from datetime import datetime
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from card_platform.config import get_settings
from card_platform.image_service import CardVisionService
from card_platform.card_orientation import OrientationDecision, rotate_image_content
from card_platform.main import app
from card_platform.purchase_cost_service import PurchaseCostService
from card_platform.scanner_queue import ScannerRecognitionWorker
from card_platform.schemas import CardRecognitionResult
from test_purchase_cost_postgres import client as client, register, item, order
from test_mobile_postgres import photos

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="isolated migrated PostgreSQL required"
)


def test_auto_import_compatibility_year_is_not_purchase_evidence(client, monkeypatch):
    owner = register(client)
    result = client.post("/v1/scanner/jobs", files=photos())
    assert result.status_code == 202, result.text
    job_id = result.json()["id"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET status='running',started_at=clock_timestamp() WHERE id=%s",
            (job_id,),
        )

    async def orient(self, original, display, content_type, vision):
        display.write_bytes(rotate_image_content(original.read_bytes(), 0))
        return OrientationDecision(0, 0.99, "vision_model")

    async def recognize(*args):
        return CardRecognitionResult(
            player_name="Test Player",
            brand_name="Topps",
            product_name="Topps Now",
            card_number="123",
            confidence=".99",
            is_rookie=False,
            is_autograph=False,
            is_memorabilia=False,
            is_numbered=False,
            identity_storage_binding={"provider_supplied": "must be discarded"},
        )

    monkeypatch.setattr(ScannerRecognitionWorker, "_orient_side", orient)
    monkeypatch.setattr(CardVisionService, "recognize_pair_contents", recognize)
    client.portal.call(
        ScannerRecognitionWorker(app.state.database, get_settings())._process, job_id
    )
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as db:
        job = db.execute("SELECT * FROM scanner_recognition_job WHERE id=%s", (job_id,)).fetchone()
        assert job["status"] == "succeeded", job["error_message"]
        assert job["result"]["field_assessments"]["season_start"]["status"] == "unknown"
        assert job["result"]["identity_storage_binding"]["version"] == 1
        assert len(job["result"]["identity_storage_binding"]["image_content_hashes"]) == 2
        item_id = job["collection_item_id"]
        raw = db.execute(
            "SELECT cs.season_start FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id JOIN card_set cs ON cs.id=cd.card_set_id WHERE ci.id=%s",
            (item_id,),
        ).fetchone()
        assert raw["season_start"] == datetime.now().year

    async def read():
        async with app.state.database.connection() as conn:
            return await PurchaseCostService().card(conn, UUID(owner), item_id)

    projected = client.portal.call(read)
    assert projected["season_start"] is None
    # Even an exact-looking title with the guessed year is insufficient.
    captured = order(title=f"{datetime.now().year} Topps Now Test Player 卡号 123")
    response = client.post(
        f"/v1/purchase-cost/items/{item_id}/search-result",
        json={"orders": [captured], "complete": True},
    )
    assert response.json()["status"] == "unmatched", response.json()
    detail = client.get(f"/v1/portfolio/items/{item_id}/detail").json()
    assert detail["transactions"] == []
    # Explicit owner confirmation can validate the exact same storage value.
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        confirmation = db.execute(
            """INSERT INTO card_identity_version(collection_item_id,version,identity,review_status,
            source,confirmed_at,confirmed_by) VALUES(%s,1,'{}','confirmed','identity_review',now(),%s)
            RETURNING id""",
            (item_id, owner),
        ).fetchone()[0]
    assert client.portal.call(read)["season_start"] == datetime.now().year
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            """INSERT INTO card_identity_version(collection_item_id,version,identity,review_status,
                   source,supersedes_id) VALUES(%s,2,'{}','rejected','identity_review',%s)""",
            (item_id, confirmation),
        )
    assert client.portal.call(read)["season_start"] is None
    # A later owner edit makes the old snapshot inapplicable.
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE collection_item SET serial_number=3,serial_total=50,is_numbered=true WHERE id=%s",
            (item_id,),
        )
    assert client.portal.call(read)["season_start"] == datetime.now().year


@pytest.mark.parametrize(
    "complete,has_orders,expected",
    [
        (True, True, "unmatched"),
        (True, False, "not_found"),
        (False, True, "incomplete"),
    ],
)
def test_search_distinguishes_no_orders_from_unverified_orders_without_writes(
    client, complete, has_orders, expected
):
    owner = register(client)
    item_id = item(client, owner, source="900999")
    captured = order(title="2025 Panini Prizm Other Unrelated Athlete")
    result = client.post(
        f"/v1/purchase-cost/items/{item_id}/search-result",
        json={"orders": [captured] if has_orders else [], "complete": complete},
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == expected, result.json()
    if expected == "unmatched":
        assert "购买记录" in result.json()["reason"]
    assert client.get(f"/v1/portfolio/items/{item_id}/detail").json()["transactions"] == []


def test_single_roster_subject_in_an_order_is_never_an_automatic_purchase(client):
    owner = register(client)
    item_id = item(client, owner, player="Curry / James / Durant", source="900999")
    captured = order(title="2025 Topps NBA Hoops Stephen Curry Gold Bounce House 卡号 123 7/50编")
    result = client.post(
        f"/v1/purchase-cost/items/{item_id}/search-result",
        json={"orders": [captured], "complete": True},
    )
    assert result.json()["status"] == "unmatched", result.json()
    assert client.get(f"/v1/portfolio/items/{item_id}/detail").json()["transactions"] == []
