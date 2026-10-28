import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/api/tests"))
from test_knowledge_source_inputs import fixture, official, set_captures, specialist


def cli():
    path = Path(__file__).resolve().parents[1] / "plan_knowledge_intake.py"
    spec = importlib.util.spec_from_file_location("plan_knowledge_intake", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def args(tmp_path):
    return ["--input-root", str(tmp_path / "input"), "--baseline", "baseline.json",
            "--scope", "scope.json", "--captures", "captures.json",
            "--cache-dir", str(tmp_path / "cache"), "--output-dir", str(tmp_path / "out")]


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_cli_two_runs_are_byte_identical_and_offline(tmp_path, monkeypatch, capsys):
    import httpx
    import psycopg
    from card_platform import knowledge_source_inputs
    from openai import OpenAI
    root = tmp_path / "input"
    fixture(root)
    set_captures(root, [official(root), specialist(root)])
    before = files(root)
    def forbidden(*args, **kwargs):
        raise AssertionError("Network, DB and models must not be used")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(OpenAI, "__init__", forbidden)
    assert cli().main(args(tmp_path)) == 0
    first = files(tmp_path / "out")
    assert json.loads(first["plan.json"])["llm_calls"] == 0
    assert json.loads(first["coverage.json"])["known_baseline"]["checklist_variant_records"] is None
    monkeypatch.setattr(knowledge_source_inputs, "compile_source", forbidden)
    assert cli().main(args(tmp_path)) == 0
    assert files(tmp_path / "out") == first
    assert files(root) == before
    assert "只读计划，不是录入完成" in first["report.md"].decode()
    assert '"parser_calls":0' in capsys.readouterr().err


def test_cli_bad_input_and_existing_output_are_safe(tmp_path):
    root = tmp_path / "input"
    fixture(root)
    (root / "baseline.json").write_text("{}")
    assert cli().main(args(tmp_path)) == 2
    assert not (tmp_path / "out").exists()
    fixture(root)
    output = tmp_path / "out"
    output.mkdir()
    (output / "user.txt").write_text("keep")
    assert cli().main(args(tmp_path)) == 2
    assert (output / "user.txt").read_text() == "keep"
    (output / "user.txt").unlink()
    output.rmdir()
    item = official(root)
    (root / item["receipt"]["path"]).unlink()
    set_captures(root, [item])
    assert cli().main(args(tmp_path)) == 0
    assert any(i["code"] == "missing_capture" for i in json.loads((output / "plan.json").read_text())["issues"])
    assert cli().main(args(tmp_path)) == 0


def test_cli_failed_write_preserves_complete_previous_generation(tmp_path, monkeypatch):
    root = tmp_path / "input"
    fixture(root)
    module = cli()
    assert module.main(args(tmp_path)) == 0
    before = files(tmp_path / "out")
    original = Path.write_text
    def fail_report(path, *args, **kwargs):
        if path.name == "report.md":
            raise OSError("injected write failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_report)
    assert module.main(args(tmp_path)) == 2
    assert files(tmp_path / "out") == before


@pytest.mark.parametrize("option", ["--publish", "--apply"])
def test_cli_has_no_publication_switch(tmp_path, option):
    with pytest.raises(SystemExit) as error:
        cli().main([*args(tmp_path), option])
    assert error.value.code == 2


def test_failed_switch_and_failed_rollback_keep_previous_generation(tmp_path, monkeypatch, capsys):
    root = tmp_path / "input"
    fixture(root)
    module = cli()
    assert module.main(args(tmp_path)) == 0
    output = tmp_path / "out"
    before = files(output)
    original = Path.replace
    def fail_both(path, target):
        if Path(target) == output and path.name.startswith((".knowledge-plan-", ".knowledge-old-")):
            raise OSError("injected switch and rollback failure")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", fail_both)
    assert module.main(args(tmp_path)) == 2
    backups = list(tmp_path.glob(".knowledge-old-*"))
    assert len(backups) == 1
    assert files(backups[0]) == before
    assert "previous_output_retained" in capsys.readouterr().err


@pytest.mark.parametrize("case", ["attributes", "supplementary_evidence", "reference_images",
                                  "source_metadata", "specialist_evidence", "fact_record"])
def test_nested_invalid_contracts_exit_two_without_traceback(tmp_path, capsys, case):
    from card_platform.knowledge import snapshot_checksum
    from test_knowledge_source_inputs import save
    root = tmp_path / "input"
    baseline, _ = fixture(root)
    if case == "attributes":
        baseline["snapshot"]["entities"][0]["attributes"] = None
    elif case in {"supplementary_evidence", "reference_images"}:
        baseline["snapshot"]["entities"][0]["attributes"][case] = None
    elif case == "source_metadata":
        baseline["sources"] = [{"metadata": None}]
    elif case in {"specialist_evidence", "fact_record"}:
        item = specialist(root)
        set_captures(root, [item])
        path = root / "facts.json"
        document = json.loads(path.read_text())
        if case == "specialist_evidence":
            document["products"][0]["evidence"] = None
        else:
            document["products"][0]["facts"] = None
        save(root, "facts.json", document)
    baseline["published"]["checksum"] = snapshot_checksum(baseline["snapshot"])
    save(root, "baseline.json", baseline)
    assert cli().main(args(tmp_path)) == 2
    assert not (tmp_path / "out").exists()
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "knowledge_plan_failed:InputContractError" in captured.err


def test_checksum_valid_corrupt_gzip_is_an_explicit_artifact_issue(tmp_path):
    import gzip
    import hashlib
    import zlib

    from card_platform.knowledge import snapshot_checksum
    from test_knowledge_source_inputs import save
    root = tmp_path / "input"
    baseline, _ = fixture(root)
    bad = b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\x07" + b"\x00" * 8
    with pytest.raises(zlib.error):
        gzip.decompress(bad)
    (root / "cards.gz").write_bytes(bad)
    digest = hashlib.sha256(bad).hexdigest()
    baseline["snapshot"]["official_artifacts"][0]["checksum"] = digest
    baseline["artifact_files"][0]["checksum"] = digest
    baseline["published"]["checksum"] = snapshot_checksum(baseline["snapshot"])
    save(root, "baseline.json", baseline)
    assert cli().main(args(tmp_path)) == 0
    plan = json.loads((tmp_path / "out/plan.json").read_text())
    assert any(i["code"] == "artifact_shape_mismatch" for i in plan["issues"])
    coverage = json.loads((tmp_path / "out/coverage.json").read_text())
    assert coverage["known_baseline"]["observed_verified_checklist_records"] == 0
    assert coverage["known_baseline"]["checklist_variant_records"] is None
