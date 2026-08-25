#!/usr/bin/env python3
"""Confirm reviewed benchmark identities through the production confirmation service."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from starlette.requests import Request

from card_platform.auth import AuthenticatedUser
from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.domain.identity_review import ConfirmedIdentity
from card_platform.identity_review_api import ConfirmIdentityRequest, confirm_identity
from card_platform.market_data.repository import MarketDataRepository


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARKS = sorted(
    (ROOT / "data" / "benchmarks").glob("valuation-ground-truth-*.json")
)


def load_cases(paths: list[Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text())
        cases.extend({**case, "benchmark_file": path.name} for case in payload["cases"])
    return cases


def infer_brand(product_name: str) -> str:
    first = product_name.split()[0]
    return first if first in {"Topps", "Panini", "Fleer", "Flair"} else "Panini"


def confirmation_identity(case: dict[str, Any]) -> ConfirmedIdentity:
    raw = case["identity"]
    is_autograph = bool(raw.get("is_autograph"))
    is_memorabilia = bool(raw.get("is_patch") or raw.get("is_memorabilia"))
    return ConfirmedIdentity.model_validate(
        {
            **raw,
            "brand_name": raw.get("brand_name") or infer_brand(raw["product_name"]),
            "set_name": raw.get("set_name"),
            "is_rookie": bool(raw.get("is_rookie")),
            "is_autograph": is_autograph,
            "autograph_type": raw.get("autograph_type")
            or ("unknown" if is_autograph else "none"),
            "is_memorabilia": is_memorabilia,
            "memorabilia_types": raw.get("memorabilia_types")
            or (["jersey"] if is_memorabilia else []),
            "grade_company": raw.get("grade_company"),
            "grade_value": raw.get("grade_value"),
        }
    )


async def queue_refresh(database: Database, item_id: UUID, owner_id: UUID) -> UUID:
    async with database.connection() as connection, connection.transaction():
        result = await connection.execute(
            "SELECT identity_fingerprint FROM market_identity_binding "
            "WHERE collection_item_id = %s",
            (item_id,),
        )
        row = await result.fetchone()
        if row is None:
            raise RuntimeError("confirmed item has no market identity binding")
        job_id, _created = await MarketDataRepository().enqueue_refresh(
            connection,
            row["identity_fingerprint"],
            requested_by=owner_id,
            reason="ground_truth_matcher_backtest",
        )
        return job_id


async def run(
    owner_id: UUID,
    cases: list[dict[str, Any]],
    apply: bool,
    queue_market_refresh: bool,
) -> dict[str, Any]:
    database = Database(get_settings())
    await database.open()
    try:
        async with database.connection() as connection:
            user_result = await connection.execute(
                "SELECT email, display_name, base_currency, role FROM app_user WHERE id = %s",
                (owner_id,),
            )
            user_row = await user_result.fetchone()
            if user_row is None:
                raise SystemExit("owner does not exist")
            item_result = await connection.execute(
                """
                SELECT ci.display_id, ci.id, confirmed.identity AS confirmed_identity
                FROM collection_item ci
                LEFT JOIN LATERAL (
                    SELECT identity FROM card_identity_version
                    WHERE collection_item_id = ci.id AND review_status = 'confirmed'
                    ORDER BY version DESC LIMIT 1
                ) confirmed ON true
                WHERE ci.owner_id = %s AND ci.deleted_at IS NULL
                  AND ci.display_id = ANY(%s)
                """,
                (owner_id, [case["display_id"] for case in cases]),
            )
            items = {row["display_id"]: row for row in await item_result.fetchall()}
        user = AuthenticatedUser(
            id=owner_id,
            email=user_row["email"],
            display_name=user_row["display_name"],
            base_currency=user_row["base_currency"],
            role=user_row["role"],
            session_id=uuid4(),
        )
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/ground-truth-confirmation",
                "headers": [],
                "app": type("App", (), {"state": type("State", (), {"database": database})()})(),
            }
        )
        results: list[dict[str, Any]] = []
        for case in cases:
            item = items.get(case["display_id"])
            if item is None:
                results.append({"display_id": case["display_id"], "status": "missing"})
                continue
            try:
                identity = confirmation_identity(case)
            except ValidationError as error:
                results.append(
                    {
                        "display_id": case["display_id"],
                        "status": "invalid_ground_truth",
                        "errors": error.errors(include_url=False),
                    }
                )
                continue
            identity_payload = identity.model_dump(mode="json")
            if item["confirmed_identity"] == identity_payload:
                refresh_job_id = (
                    await queue_refresh(database, item["id"], owner_id)
                    if queue_market_refresh
                    else None
                )
                results.append(
                    {
                        "display_id": case["display_id"],
                        "status": "already_confirmed",
                        "refresh_job_id": refresh_job_id,
                    }
                )
                continue
            if not apply:
                results.append(
                    {
                        "display_id": case["display_id"],
                        "status": "ready",
                        "identity": identity_payload,
                    }
                )
                continue
            response = await confirm_identity(
                item["id"],
                ConfirmIdentityRequest(
                    identity=identity,
                    field_provenance={
                        field: {
                            "source": "codex_ground_truth_benchmark",
                            "benchmark_file": case["benchmark_file"],
                        }
                        for field in identity.model_fields_set
                    },
                    reason=(
                        "Codex 人工复核正反面、权威清单与公开市场证据；"
                        f"基准文件 {case['benchmark_file']}"
                    ),
                ),
                request,
                user,
            )
            refresh_job_id = (
                await queue_refresh(database, item["id"], owner_id)
                if queue_market_refresh
                else None
            )
            results.append(
                {
                    "display_id": case["display_id"],
                    "status": "confirmed",
                    "refresh_job_id": refresh_job_id,
                    **response,
                }
            )
        return {
            "applied": apply,
            "market_refresh_queued": queue_market_refresh,
            "results": results,
        }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-id", type=UUID, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--queue-market-refresh", action="store_true")
    parser.add_argument("benchmarks", type=Path, nargs="*")
    args = parser.parse_args()
    cases = load_cases(args.benchmarks or DEFAULT_BENCHMARKS)
    print(
        json.dumps(
            asyncio.run(
                run(args.owner_id, cases, args.apply, args.queue_market_refresh)
            ),
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
