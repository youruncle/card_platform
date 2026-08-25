import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.main import app


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="requires a migrated PostgreSQL database",
)


def test_expert_analysis_is_persisted_read_only_and_versioned(tmp_path) -> None:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["VALUATION_WORKER_ENABLED"] = "false"
    os.environ["RECOGNITION_WORKER_ENABLED"] = "false"
    os.environ["SCANNER_WORKER_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = str(tmp_path / "uploads")
    get_settings.cache_clear()
    with TestClient(app) as client:
        registered = client.post(
            "/v1/auth/register",
            json={
                "email": f"expert-analysis-{uuid4()}@example.com",
                "password": "A-safe-integration-password-2026!",
                "display_name": "Expert Analysis Integration",
                "base_currency": "CNY",
            },
        )
        assert registered.status_code == 201, registered.text
        created = client.post(
            "/v1/portfolio/items",
            json={
                "owner_id": registered.json()["id"],
                "card": {
                    "player_name": "Test Player",
                    "brand_name": "Panini",
                    "season_start": 2025,
                    "product_name": "Prizm",
                    "card_number": "1",
                    "is_rookie": True,
                },
            },
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]

        empty = client.get(f"/v1/portfolio/items/{item_id}/expert-analysis")
        assert empty.status_code == 200
        assert empty.json()["latest"] is None

        blocked = client.post(
            f"/v1/portfolio/items/{item_id}/expert-analysis", json={"force": False}
        )
        assert blocked.status_code == 409

        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as connection:
            job_id = connection.execute(
                """INSERT INTO card_analysis_job(
                       owner_id,collection_item_id,status,requested_source,attempt_count,
                       started_at,finished_at)
                   VALUES (%s,%s,'succeeded','codex_multimodal',1,now(),now()) RETURNING id""",
                (registered.json()["id"], item_id),
            ).fetchone()[0]
            snapshot_id = connection.execute(
                """INSERT INTO card_analysis_snapshot(
                       job_id,collection_item_id,analysis_version,source,model_name,
                       identity_summary,identity_confidence,currency,fair_value_low,
                       fair_value,fair_value_high,valuation_confidence,evidence_summary,
                       value_drivers,risks_and_conflicts,investment_conclusion,
                       recommended_actions,raw_result,status)
                   VALUES (%s,%s,1,'codex_multimodal','codex-test','Visual identity',.8,
                           'CNY',10,20,30,.4,'[]','[]','[]','Visual conclusion','[]',%s,
                           'succeeded') RETURNING id""",
                (
                    job_id,
                    item_id,
                    Jsonb(
                        {
                            "prompt": "根据正反面图片识别并分析价值",
                            "front_sha256": "front",
                            "back_sha256": "back",
                        }
                    ),
                ),
            ).fetchone()[0]

        read = client.get(f"/v1/portfolio/items/{item_id}/expert-analysis")
        assert read.status_code == 200
        assert read.json()["latest"]["source"] == "codex_multimodal"
        assert len(read.json()["history"]) == 1

        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as connection:
            with pytest.raises(psycopg.errors.RaiseException):
                connection.execute(
                    "UPDATE card_analysis_snapshot SET identity_summary='changed' WHERE id=%s",
                    (snapshot_id,),
                )
