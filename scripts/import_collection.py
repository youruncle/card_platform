#!/usr/bin/env python3
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REQUIRED = (
    "player_name",
    "brand_name",
    "season_start",
    "product_name",
    "price",
    "currency",
)


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def optional_int(value: str) -> int | None:
    return int(value) if value.strip() else None


def row_to_payload(row: dict[str, str], owner_id: str) -> dict:
    missing = [field for field in REQUIRED if not row.get(field, "").strip()]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    currency = row["currency"].strip().upper()
    if currency not in {"CNY", "USD"}:
        raise ValueError("currency must be CNY or USD")
    season_start = int(row["season_start"])
    return {
        "owner_id": owner_id,
        "card": {
            "player_name": row["player_name"],
            "brand_name": row["brand_name"],
            "season_start": season_start,
            "season_end": optional_int(row.get("season_end", "")),
            "product_name": row["product_name"],
            "insert_name": row.get("insert_name") or None,
            "card_number": row.get("card_number") or None,
            "is_rookie": parse_bool(row.get("is_rookie", "")),
            "is_autograph": parse_bool(row.get("is_autograph", "")),
            "is_patch": parse_bool(row.get("is_patch", "")),
            "print_run": optional_int(row.get("serial_total", "")),
        },
        "serial_number": optional_int(row.get("serial_number", "")),
        "serial_total": optional_int(row.get("serial_total", "")),
        "strategy_tags": [
            tag.strip()
            for tag in row.get("strategy_tags", "").split("|")
            if tag.strip()
        ],
        "notes": row.get("notes") or None,
        "purchase": {
            "transacted_at": row.get("acquired_at")
            or datetime.now(timezone.utc).isoformat(),
            "price": row["price"],
            "currency": currency,
            "fx_rate_to_base": row.get("fx_rate_to_base") or "1",
        },
    }


def post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(
            f"API returned {error.code}: {error.read().decode()}"
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Card Alpha collection CSV")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    success = 0
    with args.csv_file.open(encoding="utf-8-sig", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            try:
                payload = row_to_payload(row, args.owner_id)
                if args.dry_run:
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    result = post_json(
                        f"{args.api_url.rstrip('/')}/v1/portfolio/items", payload
                    )
                    print(f"line {line_number}: created {result['id']}")
                success += 1
            except Exception as error:
                raise SystemExit(f"line {line_number}: {error}") from error
    print(f"validated/imported {success} cards")


if __name__ == "__main__":
    main()
