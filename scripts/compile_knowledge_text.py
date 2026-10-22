#!/usr/bin/env python3
"""Compile verified local official captures; never publish or write production."""
# ruff: noqa: E402

import argparse
import fcntl
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from card_platform.knowledge_text import compile_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path,
                        default=ROOT / "data/knowledge-three-seasons/inventory.json")
    parser.add_argument("--text-root", type=Path,
                        default=ROOT / "data/knowledge-three-seasons/text")
    parser.add_argument("--baseline-root", type=Path, default=ROOT / "data/official-checklists")
    args = parser.parse_args()
    runtime = ROOT / ".runtime/knowledge-text"
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "compile.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = compile_inventory(
            json.loads(args.inventory.read_text()), args.text_root,
            baseline_root=args.baseline_root,
            progress=lambda index, total, row: print(
                f"{index}/{total} {row['source_key']}: {row['status']} "
                f"cards={row.get('card_fact_count', 0)} quarantined={row.get('quarantined_rows', 0)}",
                flush=True,
            ),
        )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return int(report["summary"]["statuses"].get("failed", 0) > 0)


if __name__ == "__main__":
    raise SystemExit(main())
