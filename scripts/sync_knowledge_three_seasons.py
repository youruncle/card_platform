#!/usr/bin/env python3
"""Build a resumable three-season inventory without touching existing live holdings."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))
from card_platform.knowledge_annual import build_annual_inventory, capture_topps_directory  # noqa: E402


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/official-checklists/catalog.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/knowledge-three-seasons")
    parser.add_argument("--refresh-topps", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    captures = []
    discovery_status = {"status": "not_requested"}
    if args.refresh_topps:
        import httpx
        with httpx.Client() as client:
            result = capture_topps_directory(catalog, args.output / "sources", client)
        catalog = result["catalog"]
        captures.append(result["capture"])
        discovery_status = result["discovery_status"]
    manifests = {}
    for path in (args.catalog.parent / "manifests").glob("*.json"):
        value = json.loads(path.read_text())
        if isinstance(value, dict):
            manifests[path.stem] = value
    inventory = build_annual_inventory(catalog, manifests, args.catalog.parent)
    inventory["source_captures"] = captures
    inventory["live_topps_discovery"] = discovery_status
    write(args.output / "catalog.json", catalog)
    write(args.output / "inventory.json", inventory)
    print(json.dumps(inventory["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
