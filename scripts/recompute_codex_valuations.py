"""Recompute every active card with Codex history; never mutate source snapshots.

The same read-time calculation is used by portfolio list, detail, and totals.
Run from repository root with PYTHONPATH=apps/api/src.
"""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from decimal import Decimal

from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.domain.codex_joint_valuation import RULE_VERSION
from card_platform.repository import PortfolioRepository


async def recompute():
    database = Database(get_settings())
    await database.open()
    rows = []
    try:
        async with database.connection() as connection:
            await connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            owners = await (
                await connection.execute("""
                SELECT DISTINCT ci.owner_id FROM collection_item ci
                JOIN card_analysis_snapshot s ON s.collection_item_id=ci.id
                JOIN card_analysis_job j ON j.id=s.job_id AND j.owner_id=ci.owner_id
                  AND j.collection_item_id=ci.id
                WHERE ci.deleted_at IS NULL AND s.source='codex_multimodal'
                  AND s.status='succeeded'
            """)
            ).fetchall()
            repository = PortfolioRepository()
            for owner in owners:
                for item in await repository.list_items(connection, owner["owner_id"]):
                    if item.codex_analysis_version is None:
                        continue
                    rows.append(
                        dict(
                            display_id=item.display_id,
                            item_id=str(item.id),
                            **item.valuation_synthesis,
                        )
                    )
    finally:
        await database.close()
    return dict(
        rule_version=RULE_VERSION,
        computed_at=datetime.now(timezone.utc).isoformat(),
        cards=len(rows),
        changed_prices=[
            dict(
                display_id=row["display_id"],
                before=row["original_fair_value"],
                after=row["fair_value"],
                status=row["status"],
            )
            for row in rows
            if row["status"] == "joint_recalculated"
            and (
                row["original_fair_value"] is None
                or Decimal(str(row["fair_value"]))
                != Decimal(str(row["original_fair_value"]))
            )
        ],
        recovered_evidence_count=sum(
            len(row.get("recovered_evidence", [])) for row in rows
        ),
        statuses=dict(Counter(row["status"] for row in rows)),
        reasons=dict(Counter(reason for row in rows for reason in row["reasons"])),
        source_snapshots_modified=0,
        items=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(recompute())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "items"},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
