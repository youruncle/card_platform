#!/usr/bin/env python3
"""Measure Apple Vision OCR anchor coverage on the frozen third benchmark."""

import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
from psycopg import AsyncConnection
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = "postgresql://card_platform:card_platform@127.0.0.1:5432/card_platform"
BATCH_ID = "98ce8435-a93b-4e6f-84f1-89b5027ce8bc"


def normalized(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def token_hit(value: str | None, ocr: str) -> bool:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(token) >= 3 and token not in {"panini", "topps", "basketball", "collection"}
    ]
    compact = normalized(ocr)
    return bool(tokens) and sum(normalized(token) in compact for token in tokens) >= max(
        1, len(tokens) // 2
    )


def truth_identity(row: dict) -> dict:
    if row.get("assisted_identity"):
        return row["assisted_identity"]
    rank = {
        "top1_correct": 1,
        "top2_correct": 2,
        "top3_correct": 3,
    }.get(row.get("assisted_label_result"))
    candidates = (row.get("prediction") or {}).get("candidates") or []
    if rank and len(candidates) >= rank:
        return candidates[rank - 1].get("identity") or {}
    return {}


async def main() -> None:
    async with await AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as connection:
        result = await connection.execute(
            """
            SELECT source_item_id, image_urls, prediction,
                   assisted_label_result, assisted_identity
            FROM recognition_benchmark_sample
            WHERE batch_id = %s AND assisted_label_result <> 'source_error'
            ORDER BY created_at
            """,
            (BATCH_ID,),
        )
        rows = list(await result.fetchall())
    with tempfile.TemporaryDirectory(prefix="card-platform-ocr-") as directory:
        root = Path(directory)
        path_to_row = {}
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            semaphore = asyncio.Semaphore(8)

            async def download(row: dict, index: int, url: str) -> None:
                async with semaphore:
                    try:
                        response = await client.get(url)
                        response.raise_for_status()
                        path = root / f"{row['source_item_id']}-{index}.jpg"
                        path.write_bytes(response.content)
                        path_to_row[str(path)] = row
                    except httpx.HTTPError:
                        return

            await asyncio.gather(
                *(
                    download(row, index, url)
                    for row in rows
                    for index, url in enumerate(row["image_urls"][:2])
                )
            )
        process = subprocess.run(
            ["swift", str(ROOT / "scripts/ocr_card_images.swift"), *path_to_row],
            check=True,
            capture_output=True,
            text=True,
        )
        observations = json.loads(process.stdout)
    by_item: dict[str, list[str]] = {}
    for observation in observations:
        row = path_to_row[observation["path"]]
        by_item.setdefault(row["source_item_id"], []).extend(observation["lines"])
    counts = {
        "sample_count": len(rows),
        "ocr_success_count": len(by_item),
        "player_anchor_count": 0,
        "product_anchor_count": 0,
        "season_anchor_count": 0,
        "rc_anchor_count": 0,
        "autograph_anchor_count": 0,
    }
    examples = []
    for row in rows:
        identity = truth_identity(row)
        text = " ".join(by_item.get(row["source_item_id"], []))
        counts["player_anchor_count"] += int(token_hit(identity.get("player_name"), text))
        counts["product_anchor_count"] += int(token_hit(identity.get("product_name"), text))
        season_start = identity.get("season_start")
        season_end = identity.get("season_end")
        season_hit = bool(
            season_start
            and (
                str(season_start) in text
                or (
                    season_end
                    and f"{season_start}-{str(season_end)[-2:]}" in text
                )
            )
        )
        counts["season_anchor_count"] += int(season_hit)
        counts["rc_anchor_count"] += int(
            identity.get("is_rookie") is True
            and bool(re.search(r"\b(?:RC|ROOKIE)\b", text, re.I))
        )
        counts["autograph_anchor_count"] += int(
            identity.get("is_autograph") is True
            and bool(re.search(r"\b(?:AUTO|AUTOGRAPH|SIGNATURE)\b", text, re.I))
        )
        if len(examples) < 10:
            examples.append(
                {
                    "source_item_id": row["source_item_id"],
                    "truth": {
                        "player": identity.get("player_name"),
                        "product": identity.get("product_name"),
                    },
                    "ocr": by_item.get(row["source_item_id"], [])[:12],
                }
            )
    denominator = max(counts["sample_count"], 1)
    rates = {
        key.removesuffix("_count") + "_rate": round(value / denominator, 4)
        for key, value in counts.items()
        if key.endswith("_anchor_count")
    }
    print(json.dumps({**counts, **rates, "examples": examples}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
