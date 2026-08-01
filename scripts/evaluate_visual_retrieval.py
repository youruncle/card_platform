#!/usr/bin/env python3
"""Offline baseline for card-design retrieval using only local OpenCV features."""

import asyncio
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import httpx
import numpy as np
from psycopg import AsyncConnection
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from card_platform.image_rectification import rectify_card_image  # noqa: E402


DATABASE_URL = "postgresql://card_platform:card_platform@127.0.0.1:5432/card_platform"
REFERENCE_BATCHES = {
    "f5aa2293-5211-4399-90ec-7c8b29f3e0fa",
    "4138b592-e412-456a-8a15-4c2897be256d",
}
QUERY_BATCH = "98ce8435-a93b-4e6f-84f1-89b5027ce8bc"


def normalize_product(value: str | None) -> str:
    tokens = re.findall(r"[a-z0-9]+", (value or "").casefold())
    ignored = {"panini", "topps", "nba", "basketball", "collection"}
    return "".join(token for token in tokens if token not in ignored)


def identity_for(row: dict) -> dict:
    return (
        row.get("corrected_identity")
        or row.get("assisted_identity")
        or ((row.get("prediction") or {}).get("candidates") or [{}])[0].get("identity")
        or {}
    )


def image_feature(content: bytes, content_type: str) -> np.ndarray | None:
    rectified = rectify_card_image(content, content_type)
    image = cv2.imdecode(np.frombuffer(rectified.content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    portrait = image.shape[0] >= image.shape[1]
    size = (160, 224) if portrait else (224, 160)
    image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [24, 12], [0, 180, 0, 256]).ravel()
    hist /= np.linalg.norm(hist) + 1e-8
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    layout = cv2.resize(gray, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    layout = (layout.ravel() - layout.mean()) / (layout.std() + 1e-6)
    layout /= np.linalg.norm(layout) + 1e-8
    edges = cv2.Canny(gray, 70, 150)
    edge_layout = cv2.resize(edges, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    edge_layout = edge_layout.ravel() / 255
    edge_layout /= np.linalg.norm(edge_layout) + 1e-8
    return np.concatenate([hist * 0.8, layout, edge_layout * 0.8])


async def fetch_feature(client: httpx.AsyncClient, urls: list[str]) -> np.ndarray | None:
    for url in urls[:2]:
        try:
            response = await client.get(url)
            response.raise_for_status()
            feature = image_feature(
                response.content,
                response.headers.get("content-type", "image/jpeg"),
            )
            if feature is not None:
                return feature
        except (httpx.HTTPError, ValueError):
            continue
    return None


async def main() -> None:
    async with await AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as connection:
        result = await connection.execute(
            """
            SELECT batch_id::text, source_item_id, image_urls, prediction,
                   label_result, corrected_identity, assisted_label_result,
                   assisted_identity, assisted_confidence
            FROM recognition_benchmark_sample
            WHERE batch_id = ANY(%s)
            ORDER BY created_at
            """,
            (list(REFERENCE_BATCHES | {QUERY_BATCH}),),
        )
        rows = list(await result.fetchall())
    usable = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        semaphore = asyncio.Semaphore(8)

        async def load(row: dict):
            async with semaphore:
                feature = await fetch_feature(client, row["image_urls"])
                if feature is not None:
                    usable.append((row, feature))

        await asyncio.gather(*(load(row) for row in rows))

    references = [
        (row, feature)
        for row, feature in usable
        if row["batch_id"] in REFERENCE_BATCHES
        and identity_for(row).get("product_name")
        and identity_for(row).get("player_name")
        and row.get("assisted_label_result") != "source_error"
    ]
    queries = [
        (row, feature)
        for row, feature in usable
        if row["batch_id"] == QUERY_BATCH
        and row.get("assisted_label_result") != "source_error"
        and identity_for(row).get("product_name")
    ]
    metrics = defaultdict(int)
    examples = []
    ref_matrix = np.stack([feature for _, feature in references])
    ref_matrix /= np.linalg.norm(ref_matrix, axis=1, keepdims=True) + 1e-8
    for row, feature in queries:
        normalized = feature / (np.linalg.norm(feature) + 1e-8)
        order = np.argsort(-(ref_matrix @ normalized))[:5]
        truth = identity_for(row)
        truth_product = normalize_product(truth.get("product_name"))
        truth_season = truth.get("season_start")
        hits = []
        for index in order:
            candidate = identity_for(references[index][0])
            hits.append(
                (
                    normalize_product(candidate.get("product_name")) == truth_product,
                    candidate.get("season_start") == truth_season,
                    references[index][0]["source_item_id"],
                    candidate.get("product_name"),
                    candidate.get("season_start"),
                )
            )
        metrics["query_count"] += 1
        metrics["product_top1"] += int(hits[0][0])
        metrics["product_top3"] += int(any(hit[0] for hit in hits[:3]))
        metrics["product_season_top1"] += int(hits[0][0] and hits[0][1])
        metrics["product_season_top3"] += int(
            any(hit[0] and hit[1] for hit in hits[:3])
        )
        if len(examples) < 12:
            examples.append(
                {
                    "query": row["source_item_id"],
                    "truth": [truth.get("product_name"), truth_season],
                    "top3": [list(hit[2:]) for hit in hits[:3]],
                }
            )
    denominator = max(metrics["query_count"], 1)
    output = {
        "reference_count": len(references),
        "query_count": metrics["query_count"],
        "product_top1": round(metrics["product_top1"] / denominator, 4),
        "product_top3": round(metrics["product_top3"] / denominator, 4),
        "product_season_top1": round(metrics["product_season_top1"] / denominator, 4),
        "product_season_top3": round(metrics["product_season_top3"] / denominator, 4),
        "examples": examples,
    }
    import json

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
