#!/usr/bin/env python3
"""Offline learned-vector build and source-linked text/image candidate queries.

Use the isolated model runtime for encoding. --inspect needs only NumPy/Pillow.
No API/database credentials, API install or online model download are needed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from card_platform.knowledge_vectors import (  # noqa: E402
    SiglipEncoder,
    VectorIndex,
    build_asset_index,
    file_sha256,
    search_image,
    search_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, help="Ready public-source media JSONL to index")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path,
                        default=ROOT / ".runtime/knowledge-embeddings/model")
    parser.add_argument("--cache-dir", type=Path,
                        default=ROOT / ".runtime/knowledge-embeddings/vector-cache")
    parser.add_argument("--knowledge-version", default="three-seasons-2026-10-21")
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--inspect", action="store_true")
    queries = parser.add_mutually_exclusive_group()
    queries.add_argument("--query-text")
    queries.add_argument("--query-image", type=Path)
    parser.add_argument("--target-modality", choices=["image", "text"], default="image")
    parser.add_argument("--side", choices=["front", "back", "unknown", "detail", "both"])
    parser.add_argument("--season-start", type=int)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if not any((args.assets, args.inspect, args.query_text, args.query_image)):
        parser.error("provide --assets, --inspect, --query-text or --query-image")
    if args.batch_size <= 0 or args.threads <= 0 or args.top_k < 0:
        parser.error("batch size/threads must be positive and top-k nonnegative")
    try:
        encoder = None
        summary = {}
        if args.assets or args.query_text or args.query_image:
            import torch

            torch.set_num_threads(args.threads)
            started = time.monotonic()
            encoder = SiglipEncoder(args.model_dir, device=args.device)
            summary["model_load_seconds"] = round(time.monotonic() - started, 3)
        if args.assets:
            assets = [json.loads(line) for line in args.assets.read_text().splitlines()
                      if line.strip()]
            index, report = build_asset_index(assets, encoder, args.cache_dir,
                                              args.knowledge_version, batch_size=args.batch_size)
            manifest = index.save(args.output)
            report.update({"assets_file": str(args.assets.resolve()),
                           "assets_sha256": file_sha256(args.assets),
                           "knowledge_version": args.knowledge_version,
                           "model_fingerprint": index.model.fingerprint,
                           "index_manifest": manifest,
                           "device": args.device, "batch_size": args.batch_size})
            (args.output / "build-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            provenance = json.loads((args.model_dir / "provenance.json").read_text())
            (args.output / "model-provenance.json").write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
            summary["build"] = report
        else:
            index = VectorIndex.load(args.output, expected_model=encoder.spec if encoder else None)
        if args.inspect:
            summary.update({"count": len(index.records), "model": index.model.__dict__,
                            "knowledge_version": index.knowledge_version,
                            "modalities": dict(Counter(r["modality"] for r in index.records)),
                            "sides": dict(Counter(r["side"] for r in index.records))})
        if args.query_text or args.query_image:
            options = {"top_k": args.top_k, "modality": args.target_modality, "side": args.side,
                       "filters": ({"season_start": args.season_start}
                                   if args.season_start is not None else None)}
            started = time.monotonic()
            hits = (search_text(index, encoder, args.query_text, **options) if args.query_text
                    else search_image(index, encoder, args.query_image, **options))
            summary["query"] = {
                "text": args.query_text, "image": str(args.query_image) if args.query_image else None,
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "candidate_only": True, "hits": hits,
            }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError, KeyError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
