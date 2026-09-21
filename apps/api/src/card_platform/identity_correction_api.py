from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from card_platform.identity_audit import (
    IdentityAuditRepository,
    build_identity_suggestions,
)
from card_platform.auth import AuthenticatedUser, current_user

router = APIRouter(
    prefix="/v1/portfolio",
    tags=["identity-correction"],
    dependencies=[Depends(current_user)],
)


class ApplyCorrections(BaseModel):
    owner_id: UUID
    suggestion_ids: list[UUID] = Field(default_factory=list)


@router.post("/identity-corrections/scan")
async def scan_identity_corrections(
    request: Request,
    owner_id: UUID | None = None,
    user: AuthenticatedUser = Depends(current_user),
) -> dict:
    scanned = 0
    suggestions_created = 0
    async with request.app.state.database.connection() as connection, connection.transaction():
        items_result = await connection.execute(
            """
            SELECT ci.id, p.full_name AS player_name, cs.season_start,
                   cs.product_name, cd.card_number, cd.insert_name,
                   cd.parallel_name, ci.serial_total, cd.is_autograph, cd.is_patch
            FROM collection_item ci
            JOIN card_definition cd ON cd.id = ci.card_definition_id
            JOIN player p ON p.id = cd.player_id
            JOIN card_set cs ON cs.id = cd.card_set_id
            WHERE ci.owner_id = %s AND ci.deleted_at IS NULL
            """,
            (user.id,),
        )
        for item in await items_result.fetchall():
            comps_result = await connection.execute(
                """
                SELECT mc.id, mc.listing_title, mc.source_url,
                       mc.source_reliability, mc.match_confidence,
                       mc.comparable_tier, mc.classification
                FROM market_comp mc
                JOIN collection_item ci ON ci.card_definition_id = mc.card_definition_id
                WHERE ci.id = %s
                ORDER BY mc.occurred_at DESC LIMIT 100
                """,
                (item["id"],),
            )
            suggestions = build_identity_suggestions(
                dict(item), [dict(row) for row in await comps_result.fetchall()]
            )
            await IdentityAuditRepository().replace_open_suggestions(
                connection, item["id"], suggestions
            )
            scanned += 1
            suggestions_created += len(suggestions)
    return {"scanned_items": scanned, "open_suggestions": suggestions_created}


async def _owned_item(connection, item_id: UUID, owner_id: UUID, *, lock: bool = False):
    result = await connection.execute(
        f"""
        SELECT ci.id, ci.card_definition_id, ci.serial_total,
               cd.card_set_id, cd.player_id, cd.card_number, cd.insert_name,
               cd.parallel_name, cd.is_rookie, cd.is_autograph, cd.is_patch,
               cd.is_case_hit, cd.print_run, cd.checklist_url, cd.metadata
        FROM collection_item ci
        JOIN card_definition cd ON cd.id = ci.card_definition_id
        WHERE ci.id = %s AND ci.owner_id = %s AND ci.deleted_at IS NULL
        {"FOR UPDATE OF ci" if lock else ""}
        """,
        (item_id, owner_id),
    )
    return await result.fetchone()


@router.get("/identity-corrections")
async def correction_counts(
    request: Request,
    owner_id: UUID | None = None,
    user: AuthenticatedUser = Depends(current_user),
) -> dict[str, int]:
    async with request.app.state.database.connection() as connection:
        result = await connection.execute(
            """
            SELECT c.collection_item_id, count(*) AS issue_count
            FROM card_identity_correction c
            JOIN collection_item ci ON ci.id = c.collection_item_id
            WHERE ci.owner_id = %s AND ci.deleted_at IS NULL AND c.status = 'open'
            GROUP BY c.collection_item_id
            """,
            (user.id,),
        )
        return {str(row["collection_item_id"]): row["issue_count"] for row in await result.fetchall()}


@router.get("/items/{item_id}/identity-corrections")
async def item_corrections(
    item_id: UUID,
    request: Request,
    owner_id: UUID | None = None,
    user: AuthenticatedUser = Depends(current_user),
) -> list[dict]:
    async with request.app.state.database.connection() as connection:
        if await _owned_item(connection, item_id, user.id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection item not found")
        result = await connection.execute(
            """
            SELECT id, field_name, current_value, suggested_value, confidence,
                   evidence, detector_version, status, detected_at
            FROM card_identity_correction
            WHERE collection_item_id = %s AND status = 'open'
            ORDER BY confidence DESC, detected_at DESC
            """,
            (item_id,),
        )
        return [dict(row) for row in await result.fetchall()]


@router.post("/items/{item_id}/identity-corrections/apply")
async def apply_corrections(
    item_id: UUID,
    payload: ApplyCorrections,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> dict:
    async with request.app.state.database.connection() as connection, connection.transaction():
        item = await _owned_item(connection, item_id, user.id, lock=True)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection item not found")
        result = await connection.execute(
            """
            SELECT id, field_name, suggested_value
            FROM card_identity_correction
            WHERE collection_item_id = %s AND status = 'open'
              AND (%s = '{}'::uuid[] OR id = ANY(%s))
            FOR UPDATE
            """,
            (item_id, payload.suggestion_ids, payload.suggestion_ids),
        )
        corrections = await result.fetchall()
        if not corrections:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="没有可应用的卡片信息修复建议",
            )
        definition = dict(item)
        collection_updates = {"serial_total": item["serial_total"]}
        for correction in corrections:
            field_name = correction["field_name"]
            if field_name == "serial_total":
                collection_updates[field_name] = correction["suggested_value"]
                definition["print_run"] = correction["suggested_value"]
            else:
                definition[field_name] = correction["suggested_value"]
        inserted = await connection.execute(
            """
            INSERT INTO card_definition (
                card_set_id, player_id, card_number, insert_name, parallel_name,
                is_rookie, is_autograph, is_patch, is_case_hit, print_run,
                checklist_url, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                definition["card_set_id"],
                definition["player_id"],
                definition["card_number"],
                definition["insert_name"],
                definition["parallel_name"],
                definition["is_rookie"],
                definition["is_autograph"],
                definition["is_patch"],
                definition["is_case_hit"],
                definition["print_run"],
                definition["checklist_url"],
                Jsonb({**(definition["metadata"] or {}), "identity_correction": True}),
            ),
        )
        created = await inserted.fetchone()
        if created:
            definition_id = created["id"]
        else:
            existing = await connection.execute(
                """
                SELECT id FROM card_definition
                WHERE card_set_id = %s AND player_id = %s
                  AND COALESCE(card_number, '') = COALESCE(%s, '')
                  AND COALESCE(insert_name, '') = COALESCE(%s, '')
                  AND COALESCE(parallel_name, '') = COALESCE(%s, '')
                """,
                (
                    definition["card_set_id"],
                    definition["player_id"],
                    definition["card_number"],
                    definition["insert_name"],
                    definition["parallel_name"],
                ),
            )
            definition_id = (await existing.fetchone())["id"]
        # Comparable sales are evidence for one exact card identity. Never copy
        # them when a collection item is rebound to a corrected definition:
        # even a one-field correction (parallel, insert, card number, grade)
        # can materially change value. The queued valuation job below must fetch
        # and verify fresh evidence for the corrected identity.
        await connection.execute(
            """
            UPDATE collection_item
            SET card_definition_id = %s, serial_total = %s, updated_at = now()
            WHERE id = %s
            """,
            (definition_id, collection_updates["serial_total"], item_id),
        )
        ids = [row["id"] for row in corrections]
        await connection.execute(
            """
            UPDATE card_identity_correction
            SET status = 'applied', applied_at = now(), applied_by = %s, updated_at = now()
            WHERE id = ANY(%s)
            """,
            (user.id, ids),
        )
        await connection.execute(
            """
            UPDATE card_identity_correction
            SET status = 'superseded', updated_at = now()
            WHERE collection_item_id = %s AND status = 'open'
            """,
            (item_id,),
        )
        valuation = await connection.execute(
            """
            INSERT INTO valuation_job (collection_item_id, status)
            VALUES (%s, 'queued')
            ON CONFLICT (collection_item_id)
                WHERE status IN ('queued', 'identifying', 'searching', 'calculating')
            DO UPDATE SET updated_at = now()
            RETURNING id
            """,
            (item_id,),
        )
        return {
            "applied_count": len(corrections),
            "valuation_job_id": str((await valuation.fetchone())["id"]),
        }
