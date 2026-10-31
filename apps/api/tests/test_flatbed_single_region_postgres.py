import os

import psycopg
import pytest
from psycopg.rows import dict_row

from test_flatbed_pair_sources_postgres import _token

pytest_plugins = ["test_flatbed_pair_sources_postgres"]

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires isolated PostgreSQL")


def test_visible_reference_can_be_confirmed_and_submitted_without_layout_edit(capture):
    client, sid, pairs, root = capture
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as db:
        rows = db.execute("SELECT r.* FROM flatbed_scan_region r JOIN flatbed_scan_sheet s ON s.id=r.sheet_id WHERE s.session_id=%s ORDER BY s.side,r.ordinal", (sid,)).fetchall()
        target = next(r for r in rows if str(r["id"]) == pairs[0]["back_region_id"])
        db.execute("UPDATE flatbed_scan_region SET crop_path=NULL,signals=signals || '{\"crop_quality\":{\"usable\":false,\"usability_score\":0.49}}'::jsonb WHERE id=%s", (target["id"],))
    original_bytes = {r["id"]: (root / r["crop_path"]).read_bytes() for r in rows}
    before = client.get(f"/v1/flatbed/regions/{target['id']}/image")
    assert before.status_code == 200
    assert before.headers["x-flatbed-preview"] == "uncorrected-region"
    confirmed = client.put(f"/v1/flatbed/sessions/{sid}/pairs", json={"pairs": pairs})
    assert confirmed.status_code == 200, confirmed.text
    after = client.get(f"/v1/flatbed/regions/{target['id']}/image")
    assert after.content == before.content
    submitted = client.post(f"/v1/flatbed/sessions/{sid}/submit", json={"confirmation_token": confirmed.json()["confirmation_token"]})
    assert submitted.status_code == 200, submitted.text
    assert len(submitted.json()["job_ids"]) == len(pairs)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as db:
        stored = db.execute("SELECT * FROM flatbed_scan_region WHERE id=%s", (target["id"],)).fetchone()
    assert stored["failure_reason"] == target["failure_reason"]
    assert stored["signals"]["crop_quality"]["usable"] is False
    assert stored["signals"]["crop_fallback"]["method"] == "reviewed_reference_rectangle"
    assert all((root / r["crop_path"]).read_bytes() == original_bytes[r["id"]] for r in rows)


def test_one_card_correction_keeps_every_other_region_and_invalidates_old_confirmation(capture):
    client, sid, _, root = capture
    old_token = _token(client, sid)
    restored = client.get(f"/v1/flatbed/sessions/{sid}").json()
    sheet = next(s for s in restored["sheets"] if s["side"] == "back")
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as db:
        old = db.execute("SELECT * FROM flatbed_scan_region WHERE sheet_id=%s ORDER BY ordinal", (sheet["id"],)).fetchall()
    target = old[0]
    bounds = target["bounds"]
    points = bounds.get("points") or [{"x": bounds["x"], "y": bounds["y"]}, {"x": bounds["x"]+bounds["width"], "y": bounds["y"]}, {"x": bounds["x"]+bounds["width"], "y": bounds["y"]+bounds["height"]}, {"x": bounds["x"], "y": bounds["y"]+bounds["height"]}]
    points = [{"x": max(0, p["x"]-.01), "y": p["y"]} for p in points]
    untouched = (root / old[1]["crop_path"]).read_bytes()
    saved = client.put(f"/v1/flatbed/sheets/{sheet['id']}/layout", json={"region_id": str(target["id"]), "regions": [{"points": points}]})
    assert saved.status_code == 201, saved.text
    assert len(saved.json()["regions"]) == len(old)
    assert client.get(f"/v1/flatbed/sessions/{sid}").json()["confirmed_sources"] is None
    assert client.post(f"/v1/flatbed/sessions/{sid}/submit", json={"confirmation_token": old_token}).status_code == 409
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as db:
        fresh = db.execute("SELECT * FROM flatbed_scan_region WHERE sheet_id=%s ORDER BY ordinal", (saved.json()["id"],)).fetchall()
    assert fresh[0]["crop_path"] != old[0]["crop_path"]
    for key in ["bounds", "confidence", "failure_reason", "crop_path", "signals"]:
        assert fresh[1][key] == old[1][key]
    assert (root / fresh[1]["crop_path"]).read_bytes() == untouched
    stale = client.put(f"/v1/flatbed/sheets/{sheet['id']}/layout", json={"region_id": str(target["id"]), "regions": [{"points": points}]})
    assert stale.status_code == 409
