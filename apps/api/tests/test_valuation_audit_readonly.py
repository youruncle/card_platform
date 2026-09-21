import importlib
import json
import pytest


def audit():
    assert importlib.util.find_spec("scripts.audit_cardhobby_valuation"), "read-only audit missing"
    return importlib.import_module("scripts.audit_cardhobby_valuation")


def test_existing_output_is_never_overwritten(tmp_path):
    m = audit()
    output = tmp_path / "run"
    output.mkdir()
    f = output / "sentinel"
    f.write_text("keep")
    with pytest.raises(FileExistsError):
        m.create_output(output, work_root=tmp_path)
    assert f.read_text() == "keep"


def test_output_cannot_escape_private_work_directory(tmp_path):
    m = audit()
    with pytest.raises(ValueError):
        m.create_output(tmp_path / "outside", work_root=tmp_path / "work")


def test_offline_does_not_open_database_or_network(tmp_path, monkeypatch):
    m = audit()

    def forbidden(*args, **kwargs):
        raise AssertionError("external I/O in offline replay")

    import psycopg
    import httpx

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", forbidden)
    baseline = {
        "frozen_at": "2026-09-21T00:00:00+08:00",
        "items": [],
        "cardhobby_comps": [],
        "identity_versions": [],
        "latest_scanner_results": [],
    }
    assert m.replay(baseline, {"items": []}) == []


def test_multiple_owners_are_not_merged():
    m = audit()
    with pytest.raises(ValueError):
        m.require_single_owner(["one", "two"])
    assert m.require_single_owner(["one"]) == "one"


def test_read_only_connection_is_server_enforced(monkeypatch):
    m = audit()
    options = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql):
            options.append(sql)
            if sql == "SHOW transaction_read_only":
                return self

        def fetchone(self):
            return {"transaction_read_only": "on"}

    import psycopg

    def connect(*args, **kwargs):
        options.append(kwargs["options"])
        return Conn()

    monkeypatch.setattr(psycopg, "connect", connect)
    with m.readonly_connection("not-printed"):
        pass
    assert any("default_transaction_read_only=on" in s for s in options)
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in options


@pytest.mark.parametrize(
    "items,samples",
    [
        (
            [{"display_id": 1, "collection_item_id": "a"}],
            [{"display_id": 2, "split": "development"}],
        ),
        (
            [
                {"display_id": 1, "collection_item_id": "a"},
                {"display_id": 1, "collection_item_id": "b"},
            ],
            [{"display_id": 1, "split": "development"}],
        ),
        (
            [{"display_id": 1, "collection_item_id": "a"}],
            [{"display_id": 1, "split": "development"}, {"display_id": 1, "split": "holdout"}],
        ),
    ],
)
def test_scope_mismatch_and_duplicate_sample_fail_closed(items, samples):
    with pytest.raises(ValueError):
        audit().validate_sample_scope({"items": items}, {"items": samples})


def test_scope_attestation_is_bound_to_exact_baseline_bytes():
    m = audit()
    with pytest.raises(ValueError):
        m.validate_owner_attestation(
            b"new",
            {
                "baseline_sha256": m.content_hash(b"old"),
                "owner_fingerprint": "opaque",
                "owner_count": 1,
            },
        )
    with pytest.raises(ValueError):
        m.validate_owner_attestation(
            b"old",
            {
                "baseline_sha256": m.content_hash(b"old"),
                "owner_fingerprint": "opaque",
                "owner_count": 2,
            },
        )


def test_cli_checks_scope_before_creating_output(tmp_path, monkeypatch):
    m = audit()
    b = tmp_path / "baseline.json"
    b.write_text(json.dumps({"items": []}))
    s = tmp_path / "samples.json"
    s.write_text(json.dumps({"items": [{"display_id": 7, "split": "development"}]}))
    a = tmp_path / "owner.json"
    a.write_text(
        json.dumps(
            {
                "baseline_sha256": m.content_hash(b.read_bytes()),
                "owner_count": 1,
                "owner_fingerprint": "fixture",
            }
        )
    )
    out = tmp_path / "work" / "run"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit",
            "--baseline",
            str(b),
            "--samples",
            str(s),
            "--owner-attestation",
            str(a),
            "--split",
            "development",
            "--out",
            str(out),
        ],
    )
    with pytest.raises(ValueError, match="missing"):
        m.main()
    assert not out.exists()


def test_real_readonly_transaction_rejects_mutation():
    import os
    import psycopg

    if "TEST_DATABASE_URL" not in os.environ:
        pytest.skip("isolated PostgreSQL required")
    with audit().readonly_connection(os.environ["TEST_DATABASE_URL"]) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TABLE audit_must_not_exist (id integer)")
        conn.rollback()
