#!/usr/bin/env python3
"""Create an offline read-only plan; there is deliberately no publication path."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from card_platform.knowledge_coverage_report import build_coverage, render_report
from card_platform.knowledge_intake_plan import build_plan
from card_platform.knowledge_source_inputs import (
    InputContractError,
    extract_sources,
    load_inputs,
    validate_output_paths,
)

MARKER = ".knowledge-readonly-plan-v1"
OUTPUT_FILES = {"plan.json", "coverage.json", "report.md", MARKER}


def _check_existing(output):
    if not output.exists():
        return
    if (not output.is_dir() or not (output / MARKER).is_file()
            or (output / MARKER).read_text(encoding="utf-8") != "knowledge-readonly-plan-v1\n"
            or any(p.name not in OUTPUT_FILES or not p.is_file() for p in output.iterdir())):
        raise InputContractError("Refusing to overwrite a directory not owned by this CLI")


def _write_output(output, plan, coverage):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".knowledge-plan-", dir=output.parent))
    backup = None
    committed = False
    try:
        for name, value in [("plan.json", plan), ("coverage.json", coverage)]:
            (temporary / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (temporary / "report.md").write_text(render_report(plan, coverage), encoding="utf-8")
        (temporary / MARKER).write_text("knowledge-readonly-plan-v1\n", encoding="utf-8")
        _check_existing(output)
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=".knowledge-old-", dir=output.parent))
            backup.rmdir()
            output.replace(backup)
        try:
            temporary.replace(output)
            committed = True
        except OSError:
            if backup is not None:
                try:
                    backup.replace(output)
                    backup = None
                except OSError:
                    print(f"previous_output_retained:{backup}", file=sys.stderr)
                    raise
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if committed and backup is not None and backup.exists():
            shutil.rmtree(backup)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input-root", "baseline", "scope", "captures", "cache-dir", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        validate_output_paths(args.input_root, args.output_dir, args.cache_dir)
        _check_existing(args.output_dir)
        inputs = load_inputs(args.input_root, args.baseline, args.scope, args.captures)
        extractions = extract_sources(inputs, args.cache_dir)
        plan = build_plan(inputs, extractions)
        coverage = build_coverage(inputs, plan)
        _write_output(args.output_dir, plan, coverage)
    except (InputContractError, OSError, ValueError) as exc:
        # Never leak source text, local input paths, traceback, or credentials.
        print(f"knowledge_plan_failed:{type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"Read-only plan: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
