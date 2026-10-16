from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from card_platform.market_data.models import (
    EvidenceRecord,
    Freshness,
    MarketDataResult,
    MarketIdentity,
)

from card_platform.market_data.retry import MAX_REFRESH_ATTEMPTS, TRANSIENT_ERROR, retry_delay_seconds


class MarketDataRepository:
    async def store_public_gallery_observations(self, connection, pages) -> int:
        """Enrich existing sales only; a rejected page price never creates one."""
        from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url, public_gallery_for_primary
        changed = 0
        seen = set()
        for page in [p for p in pages if p.get('stage') == 'public_item_source'][:2]:
            source = canonical_public_item_url(page.get('query'))
            if not source or source in seen:
                continue
            seen.add(source)
            examples = page.get('rejected_examples') or []
            example = next((e for e in examples[:5]
                if canonical_public_item_url((e.get('source_metadata') or {}).get('source_url')) == source), None)
            if example is None:
                continue
            cursor = await connection.execute(
                "SELECT id, source_url, raw_payload FROM market_evidence "
                "WHERE provider_code='cardhobby' AND external_sale_id=%s FOR UPDATE",
                ('item:' + source.rsplit('/', 1)[-1],))
            row = await cursor.fetchone()
            if row is None:
                continue
            raw = row.get('raw_payload') or {}
            gallery = public_gallery_for_primary(example, row['source_url'], raw.get('image_url'))
            if not gallery:
                continue
            try:
                old_time = datetime.fromisoformat((raw.get('source_gallery') or {})['retrieved_at'])
                if old_time >= datetime.fromisoformat(gallery['retrieved_at']):
                    continue
            except (KeyError, TypeError, ValueError):
                pass
            result = await connection.execute(
                "UPDATE market_evidence SET raw_payload=jsonb_set(raw_payload, '{source_gallery}', %s, true) "
                "WHERE id=%s AND raw_payload->'source_gallery' IS DISTINCT FROM %s",
                (Jsonb(gallery), row['id'], Jsonb(gallery)))
            changed += result.rowcount
        return changed

    async def evidence_for_public_image_leads(self, connection, leads) -> list[EvidenceRecord]:
        """Join bounded public URL/image pairs; another identity grants no authority."""
        from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url
        from urllib.parse import urlsplit
        allowed = set()
        for lead in leads[:12]:
            source = canonical_public_item_url(lead.get('source_url'))
            image = lead.get('image_url')
            try:
                image_parts = urlsplit(image) if isinstance(image, str) else None
            except ValueError:
                continue
            if (lead.get('provider') == 'cardhobby' and lead.get('discovery_only') is True
                    and source and image_parts is not None
                    and image_parts.scheme == 'https'
                    and image_parts.netloc == 'images.cardhobby.com'):
                allowed.add((source, image))
        if not allowed:
            return []
        ids = sorted({'item:' + source.rsplit('/', 1)[-1] for source, _ in allowed})
        result = await connection.execute(
            "SELECT * FROM market_evidence WHERE provider_code = 'cardhobby' "
            "AND external_sale_id IN (" + ','.join('%s' for _ in ids) + ") ORDER BY occurred_at DESC",
            tuple(ids))
        found = {}
        for row in await result.fetchall():
            reliability = (row.get('identity_classification') or {}).get('source_reliability')
            found[row['external_sale_id']] = EvidenceRecord.model_validate({**row, 'match_confidence': 0,
                'identity_classification': {'source_reliability': reliability} if reliability is not None else {}})
        from card_platform.public_source_catalog import evidence_for_source_ids
        for item in await evidence_for_source_ids(connection, ids):
            previous = found.get(item.external_sale_id)
            if previous is None or item.fetched_at > previous.fetched_at:
                found[item.external_sale_id] = item
        # Select the newest public facts before checking the image binding. A
        # newer observation of another image invalidates an old indexed pair.
        return [item for item in found.values()
                if (canonical_public_item_url(item.source_url), item.raw_payload.get('image_url')) in allowed]

    async def ensure_identity(
        self,
        connection: AsyncConnection,
        identity: MarketIdentity,
        collection_item_id: UUID | None = None,
    ) -> str:
        fingerprint = identity.fingerprint
        await connection.execute(
            """
            INSERT INTO market_identity_cache (
                identity_fingerprint, normalized_identity, last_requested_at
            ) VALUES (%s, %s, now())
            ON CONFLICT (identity_fingerprint) DO UPDATE SET
                normalized_identity = EXCLUDED.normalized_identity,
                popularity_score = market_identity_cache.popularity_score + 1,
                last_requested_at = now(),
                updated_at = now()
            """,
            (fingerprint, Jsonb(identity.normalized())),
        )
        if collection_item_id is not None:
            await connection.execute(
                """
                INSERT INTO market_identity_binding (
                    collection_item_id, identity_fingerprint
                ) VALUES (%s, %s)
                ON CONFLICT (collection_item_id) DO UPDATE SET
                    identity_fingerprint = EXCLUDED.identity_fingerprint,
                    updated_at = now()
                """,
                (collection_item_id, fingerprint),
            )
        return fingerprint

    async def cached(
        self, connection: AsyncConnection, fingerprint: str
    ) -> MarketDataResult:
        cache_result = await connection.execute(
            """
            SELECT c.freshness, c.data_as_of, c.next_refresh_at, c.evidence_count,
                   c.last_error, latest.status AS refresh_status,
                   latest.error_message AS refresh_error,
                   latest.result_summary AS refresh_summary
            FROM market_identity_cache c
            LEFT JOIN LATERAL (
                SELECT status, error_message, result_summary
                FROM market_refresh_job
                WHERE identity_fingerprint = c.identity_fingerprint
                ORDER BY created_at DESC LIMIT 1
            ) latest ON true
            WHERE c.identity_fingerprint = %s
            """,
            (fingerprint,),
        )
        cache = await cache_result.fetchone()
        if cache is None:
            return MarketDataResult(
                identity_fingerprint=fingerprint,
                freshness=Freshness.INSUFFICIENT,
            )
        evidence_result = await connection.execute(
            """
            SELECT provider_code, external_sale_id, raw_title, sale_price,
                   shipping_price, currency, occurred_at, source_url, evidence_level,
                   grade_company, grade_value, match_confidence,
                   CASE WHEN evidence.identity_fingerprint = scope.identity_fingerprint
                        THEN identity_classification
                        ELSE jsonb_strip_nulls(jsonb_build_object(
                            'source_reliability', identity_classification->'source_reliability'))
                        END AS identity_classification,
                   raw_payload, evidence_version, fetched_at
            FROM market_evidence evidence
            JOIN market_evidence_discovery_scope scope ON scope.market_evidence_id = evidence.id
            WHERE scope.identity_fingerprint = %s
            ORDER BY occurred_at DESC, first_seen_at DESC
            """,
            (fingerprint,),
        )
        rows = await evidence_result.fetchall()
        evidence = [EvidenceRecord.model_validate(row) for row in rows]
        now = datetime.now(timezone.utc)
        stored = Freshness(cache["freshness"])
        if stored == Freshness.SOURCE_LIMITED:
            freshness = stored
        elif not evidence:
            freshness = Freshness.INSUFFICIENT
        elif cache["next_refresh_at"] and cache["next_refresh_at"] <= now:
            freshness = Freshness.STALE
        elif stored == Freshness.FRESH:
            freshness = Freshness.CACHED
        else:
            freshness = stored
        diagnostics = list((cache["refresh_summary"] or {}).get("providers") or [])
        notes = []
        if cache["refresh_error"] or cache["last_error"]:
            notes.append(
                "市场来源刷新异常：" + str(cache["refresh_error"] or cache["last_error"])[:300]
            )
        for diagnostic in diagnostics:
            provider = diagnostic.get("provider") or "unknown"
            status = diagnostic.get("status") or "unknown"
            count = diagnostic.get("evidence_count", 0)
            notes.append(f"来源诊断 {provider}: {status}, 抓取候选 {count}")
            notes.extend(str(note) for note in diagnostic.get("notes", []) if note)
        return MarketDataResult(
            identity_fingerprint=fingerprint,
            freshness=freshness,
            evidence=evidence,
            data_as_of=cache["data_as_of"],
            next_refresh_at=cache["next_refresh_at"],
            notes=notes,
            provider_diagnostics=diagnostics,
        )

    async def historical_evidence_for_card_definition(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        card_definition_id: UUID,
    ) -> list[EvidenceRecord]:
        """Read prior durable sales for the same card definition.

        Identity normalization can legitimately change a fingerprint (for
        example, when an OCR product heading is reclassified as an insert).
        The sale itself remains an immutable source fact.  Return those prior
        rows for the valuation worker to reclassify against the *current*
        identity, without moving or duplicating evidence ownership.
        """
        result = await connection.execute(
            """
            SELECT DISTINCT ON (evidence.provider_code, evidence.external_sale_id)
                   evidence.provider_code, evidence.external_sale_id,
                   evidence.raw_title, evidence.sale_price,
                   evidence.shipping_price, evidence.currency,
                   evidence.occurred_at, evidence.source_url,
                   evidence.evidence_level, evidence.grade_company,
                   evidence.grade_value, evidence.match_confidence,
                   evidence.identity_classification, evidence.raw_payload,
                   evidence.evidence_version, evidence.fetched_at
            FROM market_comp comparable
            JOIN market_evidence evidence
              ON evidence.id = comparable.market_evidence_id
            WHERE comparable.card_definition_id = %s
              AND evidence.identity_fingerprint <> %s
            ORDER BY evidence.provider_code, evidence.external_sale_id,
                     evidence.occurred_at DESC
            """,
            (card_definition_id, fingerprint),
        )
        return [EvidenceRecord.model_validate(row) for row in await result.fetchall()]

    async def reviewed_evidence_for_identity(
        self,
        connection: AsyncConnection,
        collection_item_id: UUID,
        identity_version_id: UUID,
    ) -> list[EvidenceRecord]:
        """Read current manual-review sources as candidates, without adopting them.

        A repair can replace both fingerprint and definition. The explicit
        item/version decision is then the only surviving discovery link. Its
        visual authority is still checked separately against current assets;
        no review flags or old admission decisions are added to these rows.
        """
        result = await connection.execute(
            """
            SELECT evidence.provider_code, evidence.external_sale_id,
                   evidence.raw_title, evidence.sale_price,
                   evidence.shipping_price, evidence.currency,
                   evidence.occurred_at, evidence.source_url,
                   evidence.evidence_level, evidence.grade_company,
                   evidence.grade_value, evidence.match_confidence,
                   evidence.identity_classification, evidence.raw_payload,
                   evidence.evidence_version, evidence.fetched_at
            FROM collection_item item
            JOIN card_identity_version version
              ON version.collection_item_id = item.id
            JOIN market_evidence_identity_decision decision
              ON decision.collection_item_id = item.id
             AND decision.identity_version_id = version.id
            JOIN market_evidence evidence
              ON evidence.id = decision.market_evidence_id
            WHERE item.id = %s AND version.id = %s
              AND decision.decision = 'accepted'
              AND decision.classification->'visual_review'->>'source'
                  = 'codex_paired_image_review'
              AND version.id = (
                  SELECT current_version.id
                  FROM card_identity_version current_version
                  WHERE current_version.collection_item_id = item.id
                    AND current_version.review_status = 'confirmed'
                    AND NOT EXISTS (
                        SELECT 1 FROM card_identity_version revocation
                        WHERE revocation.supersedes_id = current_version.id
                          AND revocation.review_status = 'rejected'
                    )
                  ORDER BY current_version.version DESC LIMIT 1
              )
            ORDER BY evidence.occurred_at DESC
            """,
            (collection_item_id, identity_version_id),
        )
        return [EvidenceRecord.model_validate(row) for row in await result.fetchall()]

    async def adopt_reviewed_evidence(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        identity_version_id: UUID,
    ) -> int:
        """Attach evidence explicitly accepted for this identity version.

        Evidence is globally unique per provider and can predate an identity
        correction.  The review decision is the authoritative, auditable link;
        without this adoption step a corrected identity can remain empty even
        though a reviewer accepted the existing public-sale URL.
        """
        result = await connection.execute(
            """
            UPDATE market_evidence evidence
            SET identity_fingerprint = %s,
                identity_classification = evidence.identity_classification || jsonb_build_object(
                    'reviewed_identity_accepted', NOT (decision.classification ? 'visual_review'),
                    'reviewed_visual_accepted', decision.classification ? 'visual_review',
                    'reviewed_identity_scope', CASE WHEN decision.classification ? 'visual_review' THEN 'visual' ELSE 'identity' END,
                    'reviewed_identity_version_id', decision.identity_version_id::text,
                    'reviewed_collection_item_id', decision.collection_item_id::text
                ),
                last_seen_at = now()
            FROM market_evidence_identity_decision decision
            WHERE decision.identity_version_id = %s
              AND decision.market_evidence_id = evidence.id
              AND decision.decision = 'accepted'
              AND (
                  evidence.identity_fingerprint <> %s
                  OR evidence.identity_classification->>'reviewed_identity_scope' IS DISTINCT FROM
                      CASE WHEN decision.classification ? 'visual_review' THEN 'visual' ELSE 'identity' END
                  OR (evidence.identity_classification->>'reviewed_identity_accepted')::boolean
                      IS DISTINCT FROM (NOT (decision.classification ? 'visual_review'))
                  OR (evidence.identity_classification->>'reviewed_visual_accepted')::boolean
                      IS DISTINCT FROM (decision.classification ? 'visual_review')
                  OR evidence.identity_classification->>'reviewed_identity_version_id'
                      IS DISTINCT FROM decision.identity_version_id::text
                  OR evidence.identity_classification->>'reviewed_collection_item_id'
                      IS DISTINCT FROM decision.collection_item_id::text
              )
            RETURNING evidence.id
            """,
            (fingerprint, identity_version_id, fingerprint),
        )
        adopted = len(await result.fetchall())
        await connection.execute(
            """
            UPDATE market_identity_cache cache
            SET evidence_count = (
                    SELECT count(*) FROM market_evidence evidence
                    WHERE evidence.identity_fingerprint = cache.identity_fingerprint
                ),
                freshness = CASE WHEN EXISTS (
                    SELECT 1 FROM market_evidence evidence
                    WHERE evidence.identity_fingerprint = cache.identity_fingerprint
                ) THEN 'cached' ELSE cache.freshness END,
                data_as_of = COALESCE((
                    SELECT max(occurred_at) FROM market_evidence evidence
                    WHERE evidence.identity_fingerprint = cache.identity_fingerprint
                ), cache.data_as_of),
                updated_at = now()
            WHERE cache.identity_fingerprint = %s
            """,
            (fingerprint,),
        )
        return adopted

    async def adopt_legacy_evidence(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        card_definition_id: UUID,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO market_evidence (
                identity_fingerprint, provider_code, external_sale_id, raw_title,
                sale_price, shipping_price, currency, occurred_at, source_url,
                evidence_level, grade_company, grade_value, match_confidence,
                identity_classification, raw_payload, evidence_version,
                fetched_at, first_seen_at, last_seen_at
            )
            SELECT
                %s, provider_code, external_sale_id, raw_title,
                sale_price, shipping_price, currency, occurred_at, source_url,
                evidence_level, grade_company, grade_value, match_confidence,
                identity_classification, raw_payload, evidence_version,
                fetched_at, first_seen_at, last_seen_at
            FROM market_evidence
            WHERE identity_fingerprint = 'legacy:' || %s::text
            ON CONFLICT (provider_code, external_sale_id)
            DO NOTHING
            """,
            (fingerprint, card_definition_id),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache c
            SET evidence_count = (
                    SELECT count(*) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                freshness = CASE WHEN EXISTS (
                    SELECT 1 FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ) THEN 'cached' ELSE freshness END,
                data_as_of = (
                    SELECT max(occurred_at) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                next_refresh_at = COALESCE(next_refresh_at, now()),
                updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (fingerprint,),
        )

    async def enqueue_refresh(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        *,
        requested_by: UUID | None = None,
        reason: str,
    ) -> tuple[UUID, bool]:
        result = await connection.execute(
            """
            INSERT INTO market_refresh_job (identity_fingerprint, requested_by, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (identity_fingerprint)
                WHERE status IN ('queued', 'running')
            DO UPDATE SET updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            (fingerprint, requested_by, reason),
        )
        row = await result.fetchone()
        if not row["created"]:
            await connection.execute(
                """
                UPDATE provider_state
                SET coalesced_job_count = coalesced_job_count + 1, updated_at = now()
                WHERE provider_code = 'cardhobby'
                """
            )
        return row["id"], row["created"]

    async def enqueue_revaluation_after_concurrent_refresh(
        self,
        connection: AsyncConnection,
        valuation_job_id: UUID,
    ) -> UUID | None:
        """Retry once when fresh evidence arrived while valuation was running.

        A refresh normally queues a valuation when it finishes.  If a valuation
        for the same card is still active, the active-job guard intentionally
        suppresses that insert.  Once the active valuation ends with insufficient
        evidence, this closes the race without creating a refresh/valuation loop:
        the next job starts after the refresh's finish time and cannot match it.
        """
        result = await connection.execute(
            """
            INSERT INTO valuation_job (collection_item_id, status, result_summary)
            SELECT current.collection_item_id, 'queued', jsonb_build_object(
                'reason', 'market_refresh_completed_during_valuation',
                'source_valuation_job_id', current.id::text
            )
            FROM valuation_job current
            WHERE current.id = %s
              AND current.status = 'insufficient_evidence'
              -- A refresh-race compensation job is the one permitted retry.
              -- Do not let that retry queue another refresh compensation when
              -- sparse evidence remains below the normal cache threshold.
              AND COALESCE(
                    current.result_summary->>'source_valuation_job_id', ''
                  ) = ''
              AND EXISTS (
                  SELECT 1
                  FROM market_identity_binding binding
                  JOIN market_refresh_job refresh
                    ON refresh.identity_fingerprint = binding.identity_fingerprint
                  WHERE binding.collection_item_id = current.collection_item_id
                    AND refresh.status IN (
                        'succeeded', 'source_limited', 'insufficient'
                    )
                    AND refresh.finished_at >= current.started_at
                    AND refresh.finished_at <= current.finished_at
                    AND COALESCE(
                        (refresh.result_summary->>'fetched_candidate_count')::integer,
                        0
                    ) > 0
              )
              AND NOT EXISTS (
                  SELECT 1 FROM valuation_job active
                  WHERE active.collection_item_id = current.collection_item_id
                    AND active.status IN (
                        'queued', 'identifying', 'searching', 'calculating'
                    )
              )
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (valuation_job_id,),
        )
        row = await result.fetchone()
        return row["id"] if row else None

    async def claim_refresh(self, connection: AsyncConnection) -> dict | None:
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = CASE WHEN attempt_count >= 3 THEN 'failed' ELSE 'queued' END,
                error_message = CASE
                    WHEN attempt_count >= 3
                    THEN COALESCE(error_message, 'refresh worker lease expired')
                    ELSE error_message
                END,
                finished_at = CASE WHEN attempt_count >= 3 THEN now() ELSE NULL END,
                not_before = CASE
                    WHEN attempt_count >= 3 THEN not_before
                    ELSE now() + interval '5 seconds'
                END,
                updated_at = now()
            WHERE status = 'running'
              AND started_at < now() - interval '15 minutes'
            """
        )
        result = await connection.execute(
            """
            SELECT j.id, j.identity_fingerprint, c.normalized_identity, c.data_as_of
            FROM market_refresh_job j
            JOIN market_identity_cache c USING (identity_fingerprint)
            WHERE j.status = 'queued' AND j.not_before <= now()
            ORDER BY j.created_at
            FOR UPDATE OF j SKIP LOCKED
            LIMIT 1
            """
        )
        row = await result.fetchone()
        if row is None:
            return None
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = 'running', attempt_count = attempt_count + 1,
                started_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (row["id"],),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache
            SET last_refresh_started_at = now(), updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (row["identity_fingerprint"],),
        )
        return dict(row)

    async def claim_refresh_by_id(self, connection: AsyncConnection, job_id: UUID) -> dict | None:
        """Claim one explicitly selected queued refresh.

        Callers that need ownership of a newly enqueued refresh must call this
        in the same transaction as :meth:`enqueue_refresh`.  The row lock and
        status transition make the job invisible to general workers as queued
        once that transaction commits.  An already-running job is never stolen;
        the caller may wait for it and enqueue a new refresh afterwards.
        """
        result = await connection.execute(
            """
            SELECT j.id, j.identity_fingerprint, j.status,
                   c.normalized_identity, c.data_as_of
            FROM market_refresh_job j
            JOIN market_identity_cache c USING (identity_fingerprint)
            WHERE j.id = %s
            FOR UPDATE OF j
            """,
            (job_id,),
        )
        row = await result.fetchone()
        if row is None or row["status"] != "queued":
            return None
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = 'running', attempt_count = attempt_count + 1,
                started_at = now(), finished_at = NULL, error_message = NULL,
                updated_at = now()
            WHERE id = %s
            """,
            (job_id,),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache
            SET last_refresh_started_at = now(), updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (row["identity_fingerprint"],),
        )
        claimed = dict(row)
        claimed.pop("status", None)
        return claimed

    async def defer_refresh_for_capacity(self, connection: AsyncConnection, job_id: UUID) -> None:
        """Let an unstarted local request wait without counting a source failure.

        The five-second not_before floor avoids a hot queue loop. Original
        created_at is preserved, so deferred old jobs retain claim priority
        over newly queued work once eligible.
        """
        await connection.execute(
            """UPDATE market_refresh_job
               SET status = 'queued', started_at = NULL, finished_at = NULL,
                   attempt_count = GREATEST(attempt_count - 1, 0),
                   not_before = now() + interval '5 seconds',
                   error_message = NULL,
                   result_summary = COALESCE(result_summary, '{}'::jsonb) ||
                       jsonb_build_object(
                           'deferred_reason', 'local_provider_capacity',
                           'capacity_deferred_count',
                           COALESCE((result_summary->>'capacity_deferred_count')::integer, 0) + 1
                       ),
                   updated_at = now()
               WHERE id = %s AND status = 'running'""",
            (job_id,),
        )

    async def store_evidence(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        evidence: list[EvidenceRecord],
    ) -> int:
        for item in evidence:
            # Preserve the previous discovery before the canonical pointer can
            # move. Membership is a search lead, never an accepted identity.
            await connection.execute(
                """INSERT INTO market_evidence_discovery(identity_fingerprint, market_evidence_id)
                   SELECT identity_fingerprint, id FROM market_evidence
                   WHERE provider_code = %s AND external_sale_id = %s
                   ON CONFLICT DO NOTHING""",
                (item.provider_code, item.external_sale_id),
            )
            await connection.execute(
                """
                INSERT INTO market_evidence (
                    identity_fingerprint, provider_code, external_sale_id, raw_title,
                    sale_price, shipping_price, currency, occurred_at, source_url,
                    evidence_level, grade_company, grade_value, match_confidence,
                    identity_classification, raw_payload, evidence_version, fetched_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (provider_code, external_sale_id)
                DO UPDATE SET
                    -- A sale is globally unique per provider, but its first
                    -- search may have attached it to an OCR-misidentified
                    -- card.  A later authoritative refresh must move the
                    -- evidence to the corrected identity; otherwise identity
                    -- correction leaves the new cache permanently empty.
                    identity_fingerprint = CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM market_evidence_identity_decision decision
                            WHERE decision.market_evidence_id = market_evidence.id
                              AND decision.decision = 'accepted'
                        ) THEN market_evidence.identity_fingerprint
                        ELSE EXCLUDED.identity_fingerprint
                    END,
                    raw_title = EXCLUDED.raw_title,
                    sale_price = EXCLUDED.sale_price,
                    shipping_price = EXCLUDED.shipping_price,
                    currency = EXCLUDED.currency,
                    occurred_at = EXCLUDED.occurred_at,
                    source_url = EXCLUDED.source_url,
                    evidence_level = EXCLUDED.evidence_level,
                    grade_company = EXCLUDED.grade_company,
                    grade_value = EXCLUDED.grade_value,
                    match_confidence = EXCLUDED.match_confidence,
                    identity_classification = CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM market_evidence_identity_decision decision
                            WHERE decision.market_evidence_id = market_evidence.id
                              AND decision.decision = 'accepted'
                        ) THEN market_evidence.identity_classification
                        ELSE EXCLUDED.identity_classification
                    END,
                    raw_payload = CASE
                        -- A valid gallery is an independent historical page
                        -- observation. API-only refreshes must not erase it,
                        -- but changed source/primary images invalidate its link.
                        WHEN market_evidence.provider_code = 'cardhobby'
                          AND lower(rtrim(market_evidence.source_url, '/')) = lower(rtrim(EXCLUDED.source_url, '/'))
                          AND market_evidence.raw_payload->>'image_url' = EXCLUDED.raw_payload->>'image_url'
                          AND market_evidence.raw_payload->'source_gallery'->>'primary_image_url' = EXCLUDED.raw_payload->>'image_url'
                          AND market_evidence.raw_payload->'source_gallery'->>'contract' = 'public-source-gallery-v1'
                          AND market_evidence.raw_payload->'source_gallery'->>'discovery_only' = 'true'
                          AND COALESCE(EXCLUDED.raw_payload->'source_gallery', '{}'::jsonb) IN ('{}'::jsonb, 'null'::jsonb)
                        THEN EXCLUDED.raw_payload || jsonb_build_object('source_gallery', market_evidence.raw_payload->'source_gallery')
                        ELSE EXCLUDED.raw_payload
                    END,
                    evidence_version = EXCLUDED.evidence_version,
                    fetched_at = EXCLUDED.fetched_at,
                    last_seen_at = now()
                """,
                (
                    fingerprint,
                    item.provider_code,
                    item.external_sale_id,
                    item.raw_title,
                    item.sale_price,
                    item.shipping_price,
                    item.currency,
                    item.occurred_at,
                    item.source_url,
                    item.evidence_level,
                    item.grade_company,
                    item.grade_value,
                    item.match_confidence,
                    Jsonb(item.identity_classification),
                    Jsonb(item.raw_payload),
                    item.evidence_version,
                    item.fetched_at,
                ),
            )
            # Even an accepted canonical owner must not hide this sale from
            # another identity's bounded discovery. No decision is copied.
            await connection.execute(
                """INSERT INTO market_evidence_discovery(identity_fingerprint, market_evidence_id)
                   SELECT %s, id FROM market_evidence
                   WHERE provider_code = %s AND external_sale_id = %s
                   ON CONFLICT (identity_fingerprint, market_evidence_id)
                   DO UPDATE SET last_seen_at = now()""",
                (fingerprint, item.provider_code, item.external_sale_id),
            )
        return len(evidence)

    async def reconcile_external_sale_identity(
        self,
        connection: AsyncConnection,
        *,
        provider_code: str,
        external_sale_id: str,
        authoritative_fingerprint: str,
        replaceable_evidence_levels: list[str] | None = None,
    ) -> list[str]:
        """Remove the same sale from obsolete identities after authoritative review."""
        level_clause = ""
        params: list[Any] = [
            provider_code,
            external_sale_id,
            authoritative_fingerprint,
        ]
        if replaceable_evidence_levels:
            level_clause = "AND evidence_level = ANY(%s)"
            params.append(replaceable_evidence_levels)
        result = await connection.execute(
            f"""
            DELETE FROM market_evidence
            WHERE provider_code = %s
              AND external_sale_id = %s
              AND identity_fingerprint <> %s
              {level_clause}
            RETURNING identity_fingerprint
            """,
            tuple(params),
        )
        removed = sorted({row["identity_fingerprint"] for row in await result.fetchall()})
        if removed:
            await connection.execute(
                """
                UPDATE market_identity_cache c
                SET evidence_count = (
                        SELECT count(*) FROM market_evidence e
                        WHERE e.identity_fingerprint = c.identity_fingerprint
                    ),
                    data_as_of = (
                        SELECT max(occurred_at) FROM market_evidence e
                        WHERE e.identity_fingerprint = c.identity_fingerprint
                    ),
                    freshness = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM market_evidence e
                            WHERE e.identity_fingerprint = c.identity_fingerprint
                        ) THEN c.freshness
                        ELSE 'insufficient'
                    END,
                    updated_at = now()
                WHERE c.identity_fingerprint = ANY(%s)
                """,
                (removed,),
            )
        return removed

    async def finish_refresh(
        self,
        connection: AsyncConnection,
        job_id: UUID,
        fingerprint: str,
        freshness: Freshness,
        *,
        ttl_seconds: int,
        error: str | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> None:
        status = {
            Freshness.SOURCE_LIMITED: "source_limited",
            Freshness.INSUFFICIENT: "insufficient",
        }.get(freshness, "succeeded")
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = %s, error_message = %s, result_summary = %s,
                finished_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (status, error, Jsonb(result_summary or {}), job_id),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache c
            SET freshness = %s,
                evidence_count = (
                    SELECT count(*) FROM market_evidence e
                    JOIN market_evidence_discovery_scope scope ON scope.market_evidence_id = e.id
                    WHERE scope.identity_fingerprint = c.identity_fingerprint
                ),
                last_success_at = CASE
                    WHEN %s::text IS NULL THEN now()
                    ELSE last_success_at
                END,
                data_as_of = COALESCE((
                    SELECT max(occurred_at) FROM market_evidence e
                    JOIN market_evidence_discovery_scope scope ON scope.market_evidence_id = e.id
                    WHERE scope.identity_fingerprint = c.identity_fingerprint
                ), data_as_of),
                next_refresh_at = now() + make_interval(secs => %s),
                last_error = %s,
                updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (freshness.value, error, ttl_seconds, error, fingerprint),
        )
        # Never create a valuation -> refresh -> valuation loop for rare cards.
        # A healthy provider can still return verified evidence while another
        # provider is rate-limited, so SOURCE_LIMITED is also eligible. The
        # evidence EXISTS guard below prevents empty refreshes from looping.
        if freshness == Freshness.SOURCE_LIMITED and await self.schedule_transient_retry(
            connection, job_id, error,
        ):
            return
        # One or two retained source-backed sales are still valuation input.
        # The evidence EXISTS guard prevents empty refresh loops; service.get
        # respects the persisted next_refresh_at deadline.
        if freshness in {Freshness.FRESH, Freshness.SOURCE_LIMITED, Freshness.INSUFFICIENT}:
            await connection.execute(
                """
                INSERT INTO valuation_job (collection_item_id, status)
                SELECT b.collection_item_id, 'queued'
                FROM market_identity_binding b
                WHERE b.identity_fingerprint = %s
                  AND EXISTS (
                      SELECT 1 FROM market_evidence evidence
                      JOIN market_evidence_discovery_scope scope ON scope.market_evidence_id = evidence.id
                      WHERE scope.identity_fingerprint = b.identity_fingerprint
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM valuation_job active
                      WHERE active.collection_item_id = b.collection_item_id
                        AND active.status IN (
                            'queued', 'identifying', 'searching', 'calculating'
                        )
                  )
                  AND COALESCE((
                      SELECT (
                          latest.status = 'insufficient_evidence'
                          OR (
                              latest.status = 'succeeded'
                              AND latest.result_summary->>'shadow_only' = 'true'
                              AND latest.result_summary->>'shadow_status' = 'insufficient'
                          )
                      )
                      FROM valuation_job latest
                      WHERE latest.collection_item_id = b.collection_item_id
                      ORDER BY latest.created_at DESC LIMIT 1
                  ), true)
                ON CONFLICT DO NOTHING
                """,
                (fingerprint,),
            )

    async def schedule_transient_retry(
        self, connection: AsyncConnection, job_id: UUID, error: str | None,
    ) -> bool:
        """Reuse the durable job and its attempt counter, respecting source backoff."""
        if not error or not TRANSIENT_ERROR.search(error):
            return False
        result = await connection.execute(
            "SELECT attempt_count FROM market_refresh_job WHERE id = %s FOR UPDATE",
            (job_id,),
        )
        row = await result.fetchone()
        if row is None:
            return False
        attempt_count = row["attempt_count"]
        delay = retry_delay_seconds(error, attempt_count)
        if delay is None:
            await connection.execute(
                """UPDATE market_refresh_job SET result_summary = result_summary || %s
                   WHERE id = %s""",
                (Jsonb({"retry_policy": {
                    "attempt_count": attempt_count, "max_attempts": MAX_REFRESH_ATTEMPTS,
                    "scheduled": False,
                    "reason": "retry_limit_reached" if attempt_count >= MAX_REFRESH_ATTEMPTS
                              else "not_a_retryable_transport_error",
                }}), job_id),
            )
            return False
        try:
            async with connection.transaction():
                result = await connection.execute(
                    """UPDATE market_refresh_job job
                       SET status = 'queued', started_at = NULL,
                           not_before = GREATEST(
                               now() + make_interval(secs => %s),
                               (SELECT circuit_until FROM provider_state WHERE provider_code='cardhobby'),
                               (SELECT retry_after_until FROM provider_state WHERE provider_code='cardhobby')
                           ),
                           result_summary = result_summary || %s, updated_at = now()
                       WHERE job.id = %s AND job.status IN ('failed', 'source_limited')
                         AND job.attempt_count < %s
                         AND EXISTS (SELECT 1 FROM market_identity_binding binding
                                     WHERE binding.identity_fingerprint=job.identity_fingerprint)
                         AND NOT EXISTS (
                             SELECT 1 FROM market_refresh_job active
                             WHERE active.identity_fingerprint=job.identity_fingerprint
                               AND active.id <> job.id AND active.status IN ('queued','running')
                         )
                       RETURNING job.id""",
                    (delay, Jsonb({"retry_policy": {
                        "attempt_count": attempt_count, "max_attempts": MAX_REFRESH_ATTEMPTS,
                        "scheduled": True, "minimum_delay_seconds": delay,
                        "reason": "transient_source_failure",
                    }}), job_id, MAX_REFRESH_ATTEMPTS),
                )
                return await result.fetchone() is not None
        except UniqueViolation:
            # A concurrent refresh already won the active-identity slot.
            # Its job owns the retry; never overwrite it or abort this result.
            return False

    async def fail_refresh(
        self,
        connection: AsyncConnection,
        job_id: UUID,
        fingerprint: str,
        error: str,
    ) -> None:
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = 'failed', error_message = %s,
                finished_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (error, job_id),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache
            SET last_error = %s, updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (error, fingerprint),
        )

        await self.schedule_transient_retry(connection, job_id, error)

    async def provider_status(self, connection: AsyncConnection) -> list[dict]:
        result = await connection.execute(
            """
            SELECT provider_code, circuit_state, concurrent_limit, min_interval_ms,
                   error_budget, active_requests, consecutive_failures, request_count,
                   success_count, http_403_count, http_429_count, challenge_count,
                   cache_hit_count, coalesced_job_count, total_duration_ms,
                   last_request_at, last_success_at, circuit_opened_at, circuit_until,
                   retry_after_until, last_error, updated_at
            FROM provider_state ORDER BY provider_code
            """
        )
        return [dict(row) for row in await result.fetchall()]
