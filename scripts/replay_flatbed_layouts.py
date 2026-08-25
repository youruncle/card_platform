#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from card_platform.flatbed_detection import detect_cards, detect_layout


def overlap(left, right) -> float:
    width = max(0.0, min(left.x + left.width, right.x + right.width) - max(left.x, right.x))
    height = max(0.0, min(left.y + left.height, right.y + right.height) - max(left.y, right.y))
    return width * height / max(min(left.width * left.height, right.width * right.height), 1e-9)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline LiDE 400 layout replay")
    parser.add_argument("--manifest", default="data/flatbed-replay-manifest.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    paths = sorted(Path().glob(manifest["corpus_glob"]))
    expected = manifest["expected_count_by_session"]
    priors = {}
    for path in paths:
        session = path.parts[-3]
        candidates = detect_cards(path.read_bytes())
        if len(candidates) == expected[session]:
            priors.setdefault(session, candidates)
    results = []
    for path in paths:
        session = path.parts[-3]
        layout = detect_layout(path.read_bytes(), expected_count=expected[session], prior=priors.get(session))
        boxes = layout.detections
        duplicates = sum(overlap(a, b) > .15 for i, a in enumerate(boxes) for b in boxes[i + 1 :])
        whole_sheet = any(box.width * box.height > .35 for box in boxes)
        results.append({"path": str(path), "count": len(boxes), "expected": expected[session], "score": layout.score, "transform": layout.transform, "duplicates": duplicates, "whole_sheet_candidate": whole_sheet, "passed": len(boxes) == expected[session] and not duplicates and not whole_sheet})
    summary = {"files": len(results), "passed": sum(item["passed"] for item in results), "count_recall": sum(item["count"] == item["expected"] for item in results) / max(len(results), 1), "duplicate_free": sum(not item["duplicates"] for item in results) / max(len(results), 1), "whole_sheet_rejections": sum(not item["whole_sheet_candidate"] for item in results) / max(len(results), 1), "results": results}
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if summary["passed"] == summary["files"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
