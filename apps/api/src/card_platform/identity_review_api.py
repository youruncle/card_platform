from typing import Any
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.domain.identity_review import ConfirmedIdentity, structured_evidence_decision
from card_platform.domain.identity_research import (
    ACE_BAILEY_FIXTURE_VISUAL,
    ANDREW_NEMBHARD_FIXTURE_VISUAL,
    BRANDON_MILLER_FIXTURE_VISUAL,
    CHECKLIST_VERSION,
    JAREN_JACKSON_FIXTURE_VISUAL,
    JAREN_JACKSON_88_FIXTURE_VISUAL,
    LEBRON_JAMES_FIXTURE_VISUAL,
    LUKA_DONCIC_FIXTURE_VISUAL,
    SCOTTIE_BARNES_FIXTURE_VISUAL,
    STEPHEN_CURRY_FIXTURE_VISUAL,
    ResearchStatus,
    generate_candidates,
)
from card_platform.domain.valuation_quality import identity_review_reasons
from card_platform.domain.comparables import ComparableTier
from card_platform.domain.valuation_quality import ShadowSale, ShadowValuationEstimator
from card_platform.market_data.models import MarketIdentity
from card_platform.market_data.repository import MarketDataRepository
from card_platform.repository import PortfolioRepository
from card_platform.schemas import CardIdentityInput, CollectionItemCreate


router = APIRouter(
    prefix="/v1/portfolio/identity-review",
    tags=["identity-review"],
    dependencies=[Depends(current_user)],
)


class ConfirmIdentityRequest(BaseModel):
    identity: ConfirmedIdentity
    field_provenance: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=500)


class ResearchIdentityRequest(BaseModel):
    visual_observations: dict[str, Any] = Field(default_factory=dict)


async def _item_identity(connection, item_id: UUID, owner_id: UUID, lock: bool = False):
    result = await connection.execute(
        f"""
        SELECT ci.id, ci.card_definition_id, ci.image_front_url, ci.image_back_url,
               ci.serial_number, ci.serial_total, ci.grade_company, ci.grade_value,
               p.full_name AS player_name, b.name AS brand_name,
               cs.season_start, cs.season_end, cs.product_name, cs.set_name,
               cd.card_number, cd.insert_name, cd.parallel_name, cd.is_rookie,
               cd.is_autograph, cd.autograph_type, cd.is_memorabilia,
               cd.memorabilia_types
        FROM collection_item ci
        JOIN card_definition cd ON cd.id = ci.card_definition_id
        JOIN player p ON p.id = cd.player_id
        JOIN card_set cs ON cs.id = cd.card_set_id
        JOIN brand b ON b.id = cs.brand_id
        WHERE ci.id = %s AND ci.owner_id = %s AND ci.deleted_at IS NULL
        {"FOR UPDATE OF ci" if lock else ""}
        """,
        (item_id, owner_id),
    )
    return await result.fetchone()


def _identity(row: dict) -> dict:
    keys = ConfirmedIdentity.model_fields.keys()
    # Database numerics arrive as Decimal.  Identity snapshots are written to
    # jsonb audit columns, so normalize them before handing the value to Jsonb.
    return jsonable_encoder({key: row.get(key) for key in keys})


async def _evidence_rows(connection, item: dict) -> list[dict]:
    result = await connection.execute(
        """
        SELECT DISTINCT ON (me.provider_code, me.external_sale_id)
               me.id, mc.id AS market_comp_id, me.provider_code,
               me.external_sale_id, me.raw_title,
               me.sale_price, me.shipping_price, me.currency, me.occurred_at,
               me.source_url, me.evidence_level, me.match_confidence,
               me.identity_classification,
               NULLIF(me.raw_payload->>'image_url', '') AS image_url
        FROM market_evidence me
        LEFT JOIN market_identity_binding mib
          ON mib.identity_fingerprint = me.identity_fingerprint
         AND mib.collection_item_id = %s
        LEFT JOIN market_comp mc ON mc.market_evidence_id = me.id
        WHERE mib.collection_item_id IS NOT NULL OR mc.card_definition_id = %s
        ORDER BY me.provider_code, me.external_sale_id, me.occurred_at DESC
        """,
        (item["id"], item["card_definition_id"]),
    )
    return [dict(row) for row in await result.fetchall()]


async def _shadow_source_rows(connection, used_ids: list[str]) -> list[dict]:
    database_ids = _market_comp_uuid_ids(used_ids)
    if not database_ids:
        return []
    result = await connection.execute(
        """
        SELECT mc.id, mc.id AS market_comp_id, ds.code AS provider_code,
               mc.external_id AS external_sale_id, mc.listing_title AS raw_title,
               mc.sale_price, mc.shipping_price, mc.currency, mc.occurred_at,
               mc.source_url,
               NULLIF(mc.raw_payload->>'image_url', '') AS image_url,
               COALESCE(NULLIF(mc.raw_payload->>'evidence_level', ''),
                        'aggregated_sale') AS evidence_level,
               ARRAY['used_by_shadow_valuation']::text[] AS reason_codes
        FROM market_comp mc
        JOIN data_source ds ON ds.id = mc.data_source_id
        WHERE mc.id = ANY(%s::uuid[])
        ORDER BY mc.occurred_at DESC
        """,
        (database_ids,),
    )
    return [dict(row) for row in await result.fetchall()]


def _market_comp_uuid_ids(used_ids: list[str]) -> list[str]:
    database_ids = []
    for value in used_ids:
        try:
            database_ids.append(str(UUID(str(value))))
        except (TypeError, ValueError, AttributeError):
            # Research fixtures and provider-native evidence IDs are valid
            # audit identifiers, but they are not market_comp primary keys.
            continue
    return database_ids


def _enrich_candidate_evidence(
    candidates: list[dict],
    evidence: list[dict],
    confirmed_identity: dict | None = None,
    shadow_used_ids: list[str] | None = None,
) -> list[dict]:
    """Attach canonical source metadata without changing candidate decisions."""
    by_provider_sale = {
        (row.get("provider_code"), row.get("external_sale_id")): row
        for row in evidence
    }
    enriched = []
    source_fields = (
        "raw_title",
        "source_url",
        "image_url",
        "sale_price",
        "shipping_price",
        "currency",
        "occurred_at",
        "evidence_level",
    )
    used_ids = set(shadow_used_ids or [])
    for candidate in candidates:
        sales = []
        for sale in candidate.get("market_evidence") or []:
            canonical = by_provider_sale.get(
                (sale.get("provider_code"), sale.get("external_sale_id")), {}
            )
            merged = dict(sale)
            for field in source_fields:
                if merged.get(field) in (None, "") and canonical.get(field) not in (None, ""):
                    merged[field] = canonical[field]
            sales.append(merged)
        if confirmed_identity and candidate.get("identity") == confirmed_identity:
            existing = {
                (sale.get("provider_code"), sale.get("external_sale_id"))
                for sale in sales
            }
            for canonical in evidence:
                key = (canonical.get("provider_code"), canonical.get("external_sale_id"))
                source_ids = {
                    str(canonical.get("id")),
                    str(canonical.get("market_comp_id")),
                }
                if source_ids & used_ids and key not in existing:
                    sales.append(dict(canonical))
                    existing.add(key)
        enriched.append({**candidate, "market_evidence": sales})
    return enriched


@router.get("/queue")
async def review_queue(request: Request, user: AuthenticatedUser = Depends(current_user)) -> dict:
    async with request.app.state.database.connection() as connection:
        result = await connection.execute(
            """
            SELECT ci.id, p.full_name AS player_name, cs.season_start, cs.product_name,
                   cd.card_number, cd.parallel_name, ci.serial_number, ci.serial_total,
                   latest.review_status, latest.version, latest.conflicts,
                   CASE
                     WHEN p.full_name ILIKE '%%Curry%%' THEN 100
                     WHEN p.full_name ILIKE ANY(ARRAY['%%Nembhard%%','%%Scottie%%','%%Ace Bailey%%','%%LeBron%%','%%Jaren Jackson%%','%%Luka%%']) THEN 90
                     WHEN latest.review_status IS DISTINCT FROM 'confirmed' THEN 70
                     ELSE 30
                   END AS priority
            FROM collection_item ci
            JOIN card_definition cd ON cd.id = ci.card_definition_id
            JOIN player p ON p.id = cd.player_id
            JOIN card_set cs ON cs.id = cd.card_set_id
            LEFT JOIN LATERAL (
                SELECT version, review_status, conflicts FROM card_identity_version
                WHERE collection_item_id = ci.id
                ORDER BY version DESC LIMIT 1
            ) latest ON true
            WHERE ci.owner_id = %s AND ci.deleted_at IS NULL
            ORDER BY priority DESC, ci.created_at
            """,
            (user.id,),
        )
        rows = [dict(row) for row in await result.fetchall()]
        return {
            "total": len(rows),
            "confirmed": sum(row.get("review_status") == "confirmed" for row in rows),
            "items": rows,
        }


@router.get("/items/{item_id}")
async def review_detail(item_id: UUID, request: Request, user: AuthenticatedUser = Depends(current_user)) -> dict:
    async with request.app.state.database.connection() as connection:
        item_row = await _item_identity(connection, item_id, user.id)
        if item_row is None:
            raise HTTPException(status_code=404, detail="collection item not found")
        item = dict(item_row)
        version_result = await connection.execute(
            """SELECT * FROM card_identity_version WHERE collection_item_id = %s
               ORDER BY version DESC LIMIT 1""",
            (item_id,),
        )
        version = await version_result.fetchone()
        corrections = await connection.execute(
            """SELECT field_name, suggested_value, confidence, evidence
               FROM card_identity_correction
               WHERE collection_item_id = %s AND status = 'open' ORDER BY confidence DESC""",
            (item_id,),
        )
        suggestions = [dict(row) for row in await corrections.fetchall()]
        suggestion_map = {row["field_name"]: row["suggested_value"] for row in suggestions}
        current = _identity(item)
        evidence = await _evidence_rows(connection, item)
        decisions = []
        for sale in evidence:
            decision, reasons = structured_evidence_decision(
                sale.get("identity_classification") or {}, sale["evidence_level"]
            )
            decisions.append({**sale, "decision": decision, "reason_codes": reasons})
        research_result = await connection.execute(
            """SELECT id, status, trigger_reasons, source_version, attempt_count, last_error
               FROM identity_research_case WHERE collection_item_id = %s
               ORDER BY created_at DESC LIMIT 1""",
            (item_id,),
        )
        research = await research_result.fetchone()
        shadow_result = await connection.execute(
            """SELECT id, identity_version_id, status, currency, low_value, fair_value,
                      high_value, confidence, evidence_count, review_reasons, evidence, created_at
               FROM valuation_shadow_result WHERE collection_item_id = %s
               ORDER BY created_at DESC LIMIT 1""",
            (item_id,),
        )
        latest_shadow = await shadow_result.fetchone()
        shadow_used_ids = ((latest_shadow or {}).get("evidence") or {}).get(
            "used_ids", []
        )
        shadow_sources = await _shadow_source_rows(connection, shadow_used_ids)
        candidates = []
        if research:
            candidate_result = await connection.execute(
                """SELECT id, rank, identity, field_provenance, visual_reasons,
                          checklist_evidence, market_evidence, confidence,
                          validation_reasons, is_valid
                   FROM identity_research_candidate WHERE research_case_id = %s ORDER BY rank""",
                (research["id"],),
            )
            candidates = _enrich_candidate_evidence(
                [dict(row) for row in await candidate_result.fetchall()],
                [*decisions, *shadow_sources],
                (version or {}).get("identity")
                if (version or {}).get("review_status") == "confirmed"
                else None,
                shadow_used_ids,
            )
        return {
            "item_id": item_id,
            "images": {"front": item["image_front_url"], "back": item["image_back_url"]},
            "current_identity": current,
            "latest_version": dict(version) if version else None,
            "field_provenance": (version or {}).get("field_provenance", {}),
            "conflicts": (version or {}).get("conflicts", identity_review_reasons(current)),
            "review_reasons": identity_review_reasons(current),
            "suggested_identity": {**current, **suggestion_map},
            "suggestions": suggestions,
            "market_evidence": decisions,
            "research": dict(research) if research else None,
            "candidates": candidates,
            "latest_shadow": dict(latest_shadow) if latest_shadow else None,
        }


@router.post("/items/{item_id}/publish-shadow", status_code=status.HTTP_201_CREATED)
async def publish_shadow_valuation(item_id: UUID, request: Request,
                                   user: AuthenticatedUser = Depends(current_user)) -> dict:
    async with request.app.state.database.connection() as connection, connection.transaction():
        item = await _item_identity(connection, item_id, user.id, lock=True)
        if item is None:
            raise HTTPException(status_code=404, detail="collection item not found")
        result = await connection.execute(
            """SELECT s.*, v.review_status
               FROM valuation_shadow_result s
               JOIN card_identity_version v ON v.id = s.identity_version_id
               WHERE s.collection_item_id = %s AND v.review_status = 'confirmed'
                 AND NOT EXISTS (
                     SELECT 1 FROM card_identity_version revocation
                     WHERE revocation.supersedes_id = v.id
                       AND revocation.review_status = 'rejected'
                 )
                 AND s.fair_value IS NOT NULL
               ORDER BY s.created_at DESC LIMIT 1 FOR UPDATE OF s""",
            (item_id,),
        )
        shadow = await result.fetchone()
        if shadow is None:
            raise HTTPException(status_code=409, detail="没有可发布的已确认身份影子估值")
        existing_result = await connection.execute(
            """SELECT id, fair_value, currency FROM valuation_snapshot
               WHERE collection_item_id = %s
                 AND evidence->>'source' = 'approved_shadow_valuation'
                 AND evidence->>'shadow_result_id' = %s
               ORDER BY created_at DESC LIMIT 1""",
            (item_id, str(shadow["id"])),
        )
        existing = await existing_result.fetchone()
        if existing:
            return {"valuation_id": existing["id"], "shadow_result_id": shadow["id"],
                    "fair_value": existing["fair_value"], "currency": existing["currency"],
                    "history_preserved": True, "already_published": True}
        inserted = await connection.execute(
            """INSERT INTO valuation_snapshot (
                   collection_item_id, method, currency, low_value, fair_value,
                   high_value, confidence, evidence, model_version,
                   identity_compatibility, identity_compatibility_reasons)
               VALUES (%s, 'model', %s, %s, %s, %s, %s, %s, %s,
                       'compatible', '[]'::jsonb) RETURNING id""",
            (item_id, shadow["currency"], shadow["low_value"], shadow["fair_value"],
             shadow["high_value"], shadow["confidence"],
             Jsonb({"source": "approved_shadow_valuation",
                    "shadow_result_id": str(shadow["id"]),
                    "identity_version_id": str(shadow["identity_version_id"]),
                    "approved_by": str(user.id),
                    "shadow_evidence": shadow["evidence"],
                    "previous_snapshots_preserved": True}),
             f"{shadow['model_version']}:human-approved"),
        )
        valuation_id = (await inserted.fetchone())["id"]
        return {"valuation_id": valuation_id, "shadow_result_id": shadow["id"],
                "fair_value": shadow["fair_value"], "currency": shadow["currency"],
                "history_preserved": True, "already_published": False}


@router.post("/items/{item_id}/research", status_code=status.HTTP_202_ACCEPTED)
async def research_identity(item_id: UUID, payload: ResearchIdentityRequest, request: Request,
                            user: AuthenticatedUser = Depends(current_user)) -> dict:
    async with request.app.state.database.connection() as connection, connection.transaction():
        item_row = await _item_identity(connection, item_id, user.id, lock=True)
        if item_row is None:
            raise HTTPException(status_code=404, detail="collection item not found")
        item = dict(item_row)
        current = _identity(item)
        blockers = identity_review_reasons(current)
        fixture_visuals = {
            "844be4e2-74db-4985-9279-0f717021b915": SCOTTIE_BARNES_FIXTURE_VISUAL,
            "7ada1e53-bf79-4093-90c8-930af92fc35e": BRANDON_MILLER_FIXTURE_VISUAL,
            "b5755992-64d2-42c6-a4e8-1baa30f642a4": JAREN_JACKSON_FIXTURE_VISUAL,
            "e5e00406-92e2-4476-8bdc-bff0ee1a09bd": LUKA_DONCIC_FIXTURE_VISUAL,
            "70325c16-cbd5-4c36-b20b-68e288235a74": JAREN_JACKSON_88_FIXTURE_VISUAL,
            "722c6f33-0895-494f-a6f1-211661445662": LEBRON_JAMES_FIXTURE_VISUAL,
            "0f5dac8e-019d-4b3a-98b8-84989af38a16": ACE_BAILEY_FIXTURE_VISUAL,
            "3dfc7e4b-8466-44c2-89a4-4c0966f3375f": ANDREW_NEMBHARD_FIXTURE_VISUAL,
            "9405c5c3-f4ed-43f4-9dd2-2ce4f992956c": STEPHEN_CURRY_FIXTURE_VISUAL,
        }
        fixture = fixture_visuals.get(str(item_id))
        if not blockers and fixture is None:
            raise HTTPException(status_code=409, detail="identity does not require research")
        if not blockers:
            blockers = ["stored_identity_conflicts_with_card_image_and_checklist"]
        visual = dict(payload.visual_observations)
        if fixture:
            visual = {**fixture, **visual}
        visual.setdefault("serial_number", current.get("serial_number"))
        visual.setdefault("serial_total", current.get("serial_total"))
        candidates = generate_candidates(current, visual)
        case_result = await connection.execute(
            """INSERT INTO identity_research_case
               (collection_item_id, status, trigger_reasons, source_version, attempt_count)
               VALUES (%s, %s, %s, %s, 1)
               ON CONFLICT (collection_item_id) WHERE status IN
                 ('identity_research_required','candidate_identity_ready','awaiting_confirmation')
               DO UPDATE SET status = EXCLUDED.status, trigger_reasons = EXCLUDED.trigger_reasons,
                 source_version = EXCLUDED.source_version,
                 attempt_count = identity_research_case.attempt_count + 1,
                 last_error = NULL, updated_at = now()
               RETURNING id""",
            (item_id, ResearchStatus.CANDIDATE_READY if candidates else ResearchStatus.REQUIRED,
             Jsonb(blockers), CHECKLIST_VERSION),
        )
        case_id = (await case_result.fetchone())["id"]
        await connection.execute(
            "DELETE FROM identity_research_candidate WHERE research_case_id = %s", (case_id,)
        )
        for rank, candidate in enumerate(candidates, 1):
            await connection.execute(
                """INSERT INTO identity_research_candidate
                   (research_case_id, rank, identity, field_provenance, visual_reasons,
                    checklist_evidence, market_evidence, confidence, validation_reasons, is_valid)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (case_id, rank, Jsonb(candidate["identity"]), Jsonb(candidate["field_provenance"]),
                 Jsonb(candidate["visual_reasons"]), Jsonb(candidate["checklist_evidence"]),
                 Jsonb(candidate["market_evidence"]),
                 candidate["confidence"], Jsonb(candidate["validation_reasons"]), candidate["is_valid"]),
            )
        await connection.execute(
            """UPDATE valuation_snapshot SET identity_compatibility = 'incompatible',
                     identity_compatibility_reasons = %s
               WHERE collection_item_id = %s AND identity_compatibility = 'unknown'""",
            (Jsonb(["superseded_by_identity_research", *blockers]), item_id),
        )
        if candidates:
            await connection.execute(
                "UPDATE identity_research_case SET status = %s, updated_at = now() WHERE id = %s",
                (ResearchStatus.AWAITING_CONFIRMATION, case_id),
            )
        return {"research_case_id": case_id, "status": ResearchStatus.AWAITING_CONFIRMATION,
                "candidate_count": len(candidates), "candidates": candidates}


@router.post("/items/{item_id}/confirm", status_code=status.HTTP_201_CREATED)
async def confirm_identity(item_id: UUID, payload: ConfirmIdentityRequest, request: Request,
                           user: AuthenticatedUser = Depends(current_user)) -> dict:
    async with request.app.state.database.connection() as connection, connection.transaction():
        item_row = await _item_identity(connection, item_id, user.id, lock=True)
        if item_row is None:
            raise HTTPException(status_code=404, detail="collection item not found")
        item = dict(item_row)
        identity = payload.identity.model_dump(mode="json")
        evidence = await _evidence_rows(connection, item)
        market_identity = MarketIdentity.model_validate({**identity, "print_run": identity["serial_total"]})
        old_binding = await connection.execute(
            "SELECT identity_fingerprint FROM market_identity_binding WHERE collection_item_id = %s",
            (item_id,),
        )
        old_row = await old_binding.fetchone()
        old_fingerprint = old_row["identity_fingerprint"] if old_row else None
        latest_result = await connection.execute(
            "SELECT id, version FROM card_identity_version WHERE collection_item_id = %s ORDER BY version DESC LIMIT 1",
            (item_id,),
        )
        latest = await latest_result.fetchone()
        inserted = await connection.execute(
            """
            INSERT INTO card_identity_version (
                collection_item_id, version, identity, field_provenance, conflicts,
                review_status, source, confirmed_at, confirmed_by,
                confirmation_reason, supersedes_id, identity_fingerprint
            ) VALUES (%s, %s, %s, %s, '[]'::jsonb, 'confirmed', 'identity_review',
                      now(), %s, %s, %s, %s) RETURNING id, version
            """,
            (item_id, (latest["version"] if latest else 0) + 1, Jsonb(identity),
             Jsonb(payload.field_provenance), user.id, payload.reason,
             latest["id"] if latest else None, market_identity.fingerprint),
        )
        version = await inserted.fetchone()
        prior_valuations_marked_incompatible = 0
        if old_fingerprint and old_fingerprint != market_identity.fingerprint:
            # Identity confirmation preserves immutable valuation history, but a
            # price calculated for the superseded card identity must not remain
            # the item's current usable value.  Keep the old snapshots for audit
            # and mark their compatibility explicitly while the queued job
            # calculates a replacement for the confirmed identity.
            invalidated = await connection.execute(
                """UPDATE valuation_snapshot
                   SET identity_compatibility = 'incompatible',
                       identity_compatibility_reasons = %s
                   WHERE collection_item_id = %s
                     AND identity_compatibility <> 'incompatible'""",
                (
                    Jsonb(
                        [
                            "superseded_by_confirmed_identity",
                            f"confirmed_identity_version:{version['id']}",
                        ]
                    ),
                    item_id,
                ),
            )
            prior_valuations_marked_incompatible = invalidated.rowcount
        await connection.execute(
            """UPDATE identity_research_case SET status = %s, updated_at = now()
               WHERE collection_item_id = %s AND status IN
                 ('identity_research_required','candidate_identity_ready','awaiting_confirmation')""",
            (ResearchStatus.CONFIRMED_NO_EXACT_SALES, item_id),
        )
        await connection.execute(
            """INSERT INTO card_identity_change_audit
               (collection_item_id, identity_version_id, actor_id, reason, before_state, after_state)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (item_id, version["id"], user.id, payload.reason, Jsonb(_identity(item)), Jsonb(identity)),
        )
        # A confirmed front/back identity supersedes every still-open automated
        # market-title suggestion. Leaving those drafts open can later revert a
        # reviewed card from a weaker analog (for example an insert name parsed
        # as a card number).
        await connection.execute(
            """UPDATE card_identity_correction
               SET status = 'superseded', updated_at = now()
               WHERE collection_item_id = %s AND status = 'open'""",
            (item_id,),
        )
        rebind_payload = CollectionItemCreate(
            owner_id=user.id,
            card=CardIdentityInput(
                player_name=payload.identity.player_name,
                brand_name=payload.identity.brand_name,
                season_start=payload.identity.season_start,
                season_end=payload.identity.season_end,
                product_name=payload.identity.product_name,
                set_name=payload.identity.set_name,
                card_number=payload.identity.card_number,
                insert_name=payload.identity.insert_name,
                parallel_name=payload.identity.parallel_name,
                is_rookie=payload.identity.is_rookie,
                is_autograph=payload.identity.is_autograph,
                autograph_type=payload.identity.autograph_type,
                is_memorabilia=payload.identity.is_memorabilia,
                memorabilia_types=payload.identity.memorabilia_types,
                print_run=payload.identity.serial_total,
            ),
            serial_number=payload.identity.serial_number,
            serial_total=payload.identity.serial_total,
            is_numbered=payload.identity.serial_total is not None,
            grade_company=payload.identity.grade_company,
            grade_value=payload.identity.grade_value,
        )
        portfolio = PortfolioRepository()
        player_id = await portfolio._get_or_create_player(connection, rebind_payload)
        brand_id = await portfolio._get_or_create_brand(connection, rebind_payload)
        card_set_id = await portfolio._get_or_create_set(connection, brand_id, rebind_payload)
        definition_id = await portfolio._get_or_create_definition(
            connection, player_id, card_set_id, rebind_payload
        )
        await connection.execute(
            """
            UPDATE card_definition
            SET is_rookie = %s, is_autograph = %s, autograph_type = %s,
                is_memorabilia = %s, memorabilia_types = %s,
                memorabilia_usage = CASE WHEN %s THEN
                    CASE WHEN memorabilia_usage = 'none' THEN 'unknown' ELSE memorabilia_usage END
                    ELSE 'none' END,
                is_patch = CASE WHEN %s THEN is_patch ELSE false END,
                print_run = %s, updated_at = now()
            WHERE id = %s
            """,
            (
                payload.identity.is_rookie,
                payload.identity.is_autograph,
                payload.identity.autograph_type,
                payload.identity.is_memorabilia,
                payload.identity.memorabilia_types,
                payload.identity.is_memorabilia,
                payload.identity.is_memorabilia,
                payload.identity.serial_total,
                definition_id,
            ),
        )
        await connection.execute(
            """
            UPDATE collection_item
            SET card_definition_id = %s, serial_number = %s, serial_total = %s,
                is_numbered = %s, grade_company = %s, grade_value = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                definition_id,
                payload.identity.serial_number,
                payload.identity.serial_total,
                payload.identity.serial_total is not None,
                payload.identity.grade_company,
                payload.identity.grade_value,
                item_id,
            ),
        )
        await MarketDataRepository().ensure_identity(connection, market_identity, item_id)
        accepted = rejected = 0
        for sale in evidence:
            decision, reasons = structured_evidence_decision(
                sale.get("identity_classification") or {}, sale["evidence_level"]
            )
            accepted += decision == "accepted"
            rejected += decision == "rejected"
            await connection.execute(
                """INSERT INTO market_evidence_identity_decision
                   (collection_item_id, identity_version_id, market_evidence_id, decision, reason_codes, classification)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (item_id, version["id"], sale["id"], decision, Jsonb(reasons),
                 Jsonb(sale.get("identity_classification") or {})),
            )
            if decision == "accepted":
                await connection.execute(
                    "UPDATE market_evidence SET identity_fingerprint = %s WHERE id = %s",
                    (market_identity.fingerprint, sale["id"]),
                )
        await MarketDataRepository().adopt_reviewed_evidence(
            connection, market_identity.fingerprint, version["id"]
        )
        await connection.execute(
            """INSERT INTO market_identity_rebind_audit
               (collection_item_id, identity_version_id, old_fingerprint, new_fingerprint,
                actor_id, accepted_count, rejected_count, duplicate_count, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)""",
            (item_id, version["id"], old_fingerprint, market_identity.fingerprint,
             user.id, accepted, rejected, Jsonb({"published_valuation_unchanged": True})),
        )
        candidate_result = await connection.execute(
            """SELECT c.market_evidence FROM identity_research_candidate c
               JOIN identity_research_case r ON r.id = c.research_case_id
               WHERE r.collection_item_id = %s AND c.identity = %s AND c.is_valid
               ORDER BY c.created_at DESC LIMIT 1""",
            (item_id, Jsonb(identity)),
        )
        candidate = await candidate_result.fetchone()
        candidate_sales = (candidate or {}).get("market_evidence", [])
        shadow_sales = []
        now = datetime.now(timezone.utc)
        for sale in candidate_sales:
            occurred = datetime.fromisoformat(sale["occurred_at"].replace("Z", "+00:00"))
            shadow_sales.append(ShadowSale(
                id=sale["id"], provider_code=sale["provider_code"],
                price=Decimal(sale["price_base"]), shipping=Decimal("0"),
                age_days=max(0, (now - occurred).days), tier=ComparableTier.T0,
                similarity=Decimal("1"), source_reliability=Decimal("0.85"),
                evidence_level=sale["evidence_level"],
            ))
        shadow = ShadowValuationEstimator().estimate(shadow_sales, identity)
        await connection.execute(
            """INSERT INTO valuation_shadow_result (
                   collection_item_id, identity_version_id, model_version, status,
                   currency, low_value, fair_value, high_value, confidence,
                   evidence_count, review_reasons, evidence)
               SELECT %s, %s, 'robust-shadow-1.2-research', %s, u.base_currency,
                      %s, %s, %s, %s, %s, %s, %s
               FROM collection_item ci JOIN app_user u ON u.id = ci.owner_id WHERE ci.id = %s""",
            (item_id, version["id"], shadow.status, shadow.low, shadow.fair, shadow.high,
             shadow.confidence, len(shadow.used_ids), Jsonb(list(shadow.review_reasons)),
             Jsonb({"used_ids": list(shadow.used_ids), "rejected": list(shadow.rejected),
                    "accepted_count": accepted, "rejected_count": rejected,
                    "research_evidence": candidate_sales, "published_value_unchanged": True}), item_id),
        )
        final_status = (ResearchStatus.SHADOW_ESTIMATED if shadow.fair is not None
                        else ResearchStatus.CONFIRMED_NO_EXACT_SALES)
        await connection.execute(
            "UPDATE identity_research_case SET status = %s, updated_at = now() WHERE collection_item_id = %s",
            (final_status, item_id),
        )
        valuation_job = await connection.execute(
            """INSERT INTO valuation_job (collection_item_id, status, result_summary)
               VALUES (%s, 'queued', %s)
               ON CONFLICT (collection_item_id)
                   WHERE status IN ('queued','identifying','searching','calculating')
               DO UPDATE SET updated_at = now()
               RETURNING id""",
            (item_id, Jsonb({
                "reason": "confirmed_identity_revaluation",
                "identity_version_id": str(version["id"]),
                "published_value_unchanged": True,
            })),
        )
        valuation_job_id = (await valuation_job.fetchone())["id"]
        return {"identity_version_id": version["id"], "version": version["version"],
                "identity_fingerprint": market_identity.fingerprint, "accepted_count": accepted,
                "rejected_count": rejected, "valuation_job_id": valuation_job_id,
                "published_valuation_unchanged": True,
                "prior_valuations_marked_incompatible": prior_valuations_marked_incompatible}
