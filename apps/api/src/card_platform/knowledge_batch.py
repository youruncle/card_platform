from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.knowledge import normalize_knowledge_text
from card_platform.knowledge_manifest import ManifestReport


async def apply_manifest(
    connection: AsyncConnection,
    workspace_id: UUID,
    actor_id: UUID,
    manifest: dict[str, Any],
    report: ManifestReport,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    database_conflicts = await _database_conflicts(connection, workspace_id, manifest)
    if database_conflicts:
        report = ManifestReport(
            checksum=report.checksum,
            summary=report.summary,
            conflicts=report.conflicts + tuple(database_conflicts),
            replay=report.replay,
            publishable=False,
        )
    batch = await connection.execute(
        """
        INSERT INTO knowledge_import_batch (
            workspace_id, batch_key, manifest_version, status, dry_run, summary,
            conflict_report, replay_result, manifest_checksum, created_by, applied_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  CASE WHEN %s THEN NULL ELSE now() END)
        ON CONFLICT (workspace_id, batch_key, manifest_checksum, dry_run)
        DO UPDATE SET summary = EXCLUDED.summary, conflict_report = EXCLUDED.conflict_report,
                      replay_result = EXCLUDED.replay_result
        RETURNING *
        """,
        (
            workspace_id,
            manifest["batch_key"],
            manifest["manifest_version"],
            "validated" if report.publishable else "failed",
            dry_run,
            Jsonb(report.summary),
            Jsonb(list(report.conflicts)),
            Jsonb(report.replay),
            report.checksum,
            actor_id,
            dry_run,
        ),
    )
    batch_row = dict(await batch.fetchone())
    if dry_run or not report.publishable:
        return batch_row

    source_ids: dict[str, UUID] = {}
    for source in manifest["sources"]:
        result = await connection.execute(
            """
            INSERT INTO knowledge_source (
                workspace_id, source_key, title, url, publisher, source_type,
                retrieved_at, checksum, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, source_key) DO UPDATE SET
                title = EXCLUDED.title, url = EXCLUDED.url, publisher = EXCLUDED.publisher,
                source_type = EXCLUDED.source_type, retrieved_at = EXCLUDED.retrieved_at,
                checksum = EXCLUDED.checksum, metadata = EXCLUDED.metadata
            RETURNING id
            """,
            (
                workspace_id,
                source["key"],
                source["title"],
                source["url"],
                source["publisher"],
                source["source_type"],
                source["retrieved_at"],
                source.get("checksum"),
                Jsonb(source.get("metadata", {})),
            ),
        )
        source_ids[source["key"]] = (await result.fetchone())["id"]

    entity_ids: dict[str, UUID] = {}
    for entity in manifest["entities"]:
        source_key = entity.get("source_key")
        source = {
            "source_key": source_key,
            "source_url": next(
                (row["url"] for row in manifest["sources"] if row["key"] == source_key), None
            ),
            "batch_key": manifest["batch_key"],
        }
        result = await connection.execute(
            """
            INSERT INTO knowledge_entity (
                workspace_id, entity_type, canonical_name, normalized_name, attributes,
                source, confidence, review_status, active, created_by, reviewed_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      CASE WHEN %s = 'approved' THEN %s ELSE NULL END)
            ON CONFLICT (workspace_id, entity_type, normalized_name) DO UPDATE SET
                attributes = CASE WHEN %s THEN EXCLUDED.attributes
                                  ELSE knowledge_entity.attributes || EXCLUDED.attributes END,
                source = EXCLUDED.source, confidence = EXCLUDED.confidence,
                review_status = EXCLUDED.review_status, active = EXCLUDED.active,
                reviewed_by = EXCLUDED.reviewed_by, updated_at = now()
            RETURNING id
            """,
            (
                workspace_id,
                entity["entity_type"],
                entity["canonical_name"],
                normalize_knowledge_text(entity["canonical_name"]),
                Jsonb(entity.get("attributes", {})),
                Jsonb(source),
                entity.get("confidence", 1),
                entity.get("review_status", "draft"),
                entity.get("review_status", "draft") == "approved",
                actor_id,
                entity.get("review_status", "draft"),
                actor_id,
                entity.get("replace_attributes", False),
            ),
        )
        entity_id = (await result.fetchone())["id"]
        entity_ids[entity["key"]] = entity_id
        if source_key:
            await _evidence(
                connection, workspace_id, "entity", entity_id, source_ids[source_key],
                entity.get("claim", f"定义 {entity['canonical_name']} 的领域身份"), actor_id
            )
        for alias in entity.get("aliases", []):
            alias_result = await connection.execute(
                """
                INSERT INTO knowledge_alias (
                    workspace_id, entity_id, alias, normalized_alias, language, alias_type,
                    source, confidence, review_status, active, created_by, reviewed_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          CASE WHEN %s = 'approved' THEN %s ELSE NULL END)
                ON CONFLICT (workspace_id, entity_id, normalized_alias) DO UPDATE SET
                    alias = EXCLUDED.alias, language = EXCLUDED.language,
                    alias_type = EXCLUDED.alias_type, source = EXCLUDED.source,
                    confidence = EXCLUDED.confidence, review_status = EXCLUDED.review_status,
                    active = EXCLUDED.active, reviewed_by = EXCLUDED.reviewed_by, updated_at = now()
                RETURNING id
                """,
                (
                    workspace_id, entity_id, alias["value"],
                    normalize_knowledge_text(alias["value"]), alias.get("language", "und"),
                    alias.get("alias_type", "synonym"), Jsonb(source),
                    alias.get("confidence", entity.get("confidence", 1)),
                    alias.get("review_status", entity.get("review_status", "draft")),
                    alias.get("review_status", entity.get("review_status", "draft")) == "approved",
                    actor_id,
                    alias.get("review_status", entity.get("review_status", "draft")), actor_id,
                ),
            )
            alias_id = (await alias_result.fetchone())["id"]
            if source_key:
                await _evidence(
                    connection, workspace_id, "alias", alias_id, source_ids[source_key],
                    alias.get("claim", f"别名 {alias['value']} 的适用上下文"), actor_id
                )

    for relation in manifest.get("relations", []):
        result = await connection.execute(
            """
            INSERT INTO knowledge_relation (
                workspace_id, subject_entity_id, relation_type, object_entity_id, reason,
                source, confidence, review_status, created_by, reviewed_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                      CASE WHEN %s = 'approved' THEN %s ELSE NULL END)
            ON CONFLICT (workspace_id, subject_entity_id, relation_type, object_entity_id)
            DO UPDATE SET reason = EXCLUDED.reason, source = EXCLUDED.source,
                          confidence = EXCLUDED.confidence,
                          review_status = EXCLUDED.review_status, active = true,
                          reviewed_by = EXCLUDED.reviewed_by, updated_at = now()
            RETURNING id
            """,
            (
                workspace_id, entity_ids[relation["subject"]], relation["relation_type"],
                entity_ids[relation["object"]], relation.get("reason"),
                Jsonb({"source_key": relation.get("source_key"), "batch_key": manifest["batch_key"]}),
                relation.get("confidence", 1), relation.get("review_status", "draft"), actor_id,
                relation.get("review_status", "draft"), actor_id,
            ),
        )
        relation_id = (await result.fetchone())["id"]
        if source_key := relation.get("source_key"):
            await _evidence(
                connection, workspace_id, "relation", relation_id, source_ids[source_key],
                relation.get("reason", relation["relation_type"]), actor_id
            )

    for relation in manifest.get("retire_relations", []):
        await connection.execute(
            """
            UPDATE knowledge_relation AS r
            SET active = false, updated_at = now()
            FROM knowledge_entity AS previous
            WHERE r.workspace_id = %s
              AND r.subject_entity_id = %s
              AND r.relation_type = %s
              AND r.object_entity_id = previous.id
              AND previous.workspace_id = %s
              AND previous.entity_type = %s
              AND previous.normalized_name = %s
            """,
            (
                workspace_id, entity_ids[relation["subject"]], relation["relation_type"],
                workspace_id, relation.get("object_type", "manufacturer"),
                normalize_knowledge_text(relation["object_name"]),
            ),
        )

    for reassignment in manifest.get("reassign_coverage", []):
        await connection.execute(
            """
            UPDATE knowledge_coverage
            SET manufacturer_entity_id = %s, updated_at = now()
            WHERE workspace_id = %s
              AND product_entity_id = %s
              AND manufacturer_entity_id = %s
            """,
            (
                entity_ids[reassignment["manufacturer"]], workspace_id,
                entity_ids[reassignment["product"]],
                entity_ids[reassignment["previous_manufacturer"]],
            ),
        )

    for reassignment in manifest.get("reassign_product_coverage", []):
        # Remove exact duplicate coverage rows before moving the remaining evidence to
        # the canonical product. This preserves the canonical row's source while
        # preventing a uniqueness collision on product/season/insert/parallel scope.
        await connection.execute(
            """
            DELETE FROM knowledge_coverage AS previous
            USING knowledge_coverage AS canonical
            WHERE previous.workspace_id = %s
              AND canonical.workspace_id = previous.workspace_id
              AND previous.product_entity_id = %s
              AND canonical.product_entity_id = %s
              AND canonical.manufacturer_entity_id = previous.manufacturer_entity_id
              AND canonical.season_start IS NOT DISTINCT FROM previous.season_start
              AND canonical.season_end IS NOT DISTINCT FROM previous.season_end
              AND canonical.insert_entity_id IS NOT DISTINCT FROM previous.insert_entity_id
              AND canonical.parallel_entity_id IS NOT DISTINCT FROM previous.parallel_entity_id
            """,
            (workspace_id, entity_ids[reassignment["previous_product"]], entity_ids[reassignment["product"]]),
        )
        await connection.execute(
            """
            UPDATE knowledge_coverage
            SET product_entity_id = %s, updated_at = now()
            WHERE workspace_id = %s AND product_entity_id = %s
            """,
            (entity_ids[reassignment["product"]], workspace_id, entity_ids[reassignment["previous_product"]]),
        )

    for retirement in manifest.get("retire_entities", []):
        entity_id = entity_ids[retirement["entity"]]
        await connection.execute(
            """
            UPDATE knowledge_entity
            SET active = false, updated_at = now()
            WHERE workspace_id = %s AND id = %s
            """,
            (workspace_id, entity_id),
        )
        await _evidence(
            connection, workspace_id, "entity", entity_id,
            source_ids[retirement["source_key"]], retirement["reason"], actor_id,
        )

    for retirement in manifest.get("retire_coverage", []):
        await connection.execute(
            """
            DELETE FROM knowledge_coverage AS coverage
            USING knowledge_entity AS manufacturer, knowledge_entity AS product
            WHERE coverage.workspace_id = %s
              AND manufacturer.id = coverage.manufacturer_entity_id
              AND product.id = coverage.product_entity_id
              AND manufacturer.workspace_id = %s
              AND product.workspace_id = %s
              AND manufacturer.entity_type = 'manufacturer'
              AND product.entity_type = 'product'
              AND manufacturer.normalized_name = %s
              AND product.normalized_name = %s
              AND coverage.season_start IS NOT DISTINCT FROM %s
              AND coverage.season_end IS NOT DISTINCT FROM %s
              AND coverage.insert_entity_id IS NULL
              AND coverage.parallel_entity_id IS NULL
            """,
            (
                workspace_id, workspace_id, workspace_id,
                normalize_knowledge_text(retirement["manufacturer_name"]),
                normalize_knowledge_text(retirement["product_name"]),
                retirement.get("season_start"), retirement.get("season_end"),
            ),
        )

    for applicability in manifest.get("applicability", []):
        source_key = applicability.get("source_key")
        await connection.execute(
            """
            INSERT INTO knowledge_applicability (
                workspace_id, entity_id, product_entity_id, season_start, season_end,
                league, applicability_status, attributes, review_status, source_id,
                created_by, reviewed_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      CASE WHEN %s = 'approved' THEN %s ELSE NULL END)
            ON CONFLICT (workspace_id, entity_id, product_entity_id, season_start, season_end, league)
            DO UPDATE SET applicability_status = EXCLUDED.applicability_status,
                          attributes = EXCLUDED.attributes, review_status = EXCLUDED.review_status,
                          source_id = EXCLUDED.source_id, reviewed_by = EXCLUDED.reviewed_by,
                          updated_at = now()
            """,
            (
                workspace_id, entity_ids[applicability["entity"]],
                entity_ids[applicability["product"]], applicability.get("season_start"),
                applicability.get("season_end"), applicability.get("league", "NBA"),
                applicability.get("status", "verified"), Jsonb(applicability.get("attributes", {})),
                applicability.get("review_status", "draft"), source_ids.get(source_key), actor_id,
                applicability.get("review_status", "draft"), actor_id,
            ),
        )

    for row in manifest["coverage"]:
        await connection.execute(
            """
            INSERT INTO knowledge_coverage (
                workspace_id, manufacturer_entity_id, product_entity_id, season_start,
                season_end, insert_entity_id, parallel_entity_id, coverage_status,
                source_id, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (
                workspace_id, manufacturer_entity_id, product_entity_id, season_start,
                season_end, insert_entity_id, parallel_entity_id
            ) DO UPDATE SET coverage_status = EXCLUDED.coverage_status,
                            source_id = EXCLUDED.source_id, notes = EXCLUDED.notes,
                            updated_at = now()
            """,
            (
                workspace_id, entity_ids.get(row.get("manufacturer")),
                entity_ids.get(row.get("product")), row.get("season_start"),
                row.get("season_end"), entity_ids.get(row.get("insert")),
                entity_ids.get(row.get("parallel")), row["status"],
                source_ids.get(row.get("source_key")), row.get("notes"),
            ),
        )

    await connection.execute(
        """
        UPDATE knowledge_import_batch SET status = 'applied', applied_at = now()
        WHERE id = %s
        """,
        (batch_row["id"],),
    )
    await connection.execute(
        "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
        (workspace_id,),
    )
    batch_row["status"] = "applied"
    return batch_row


async def _database_conflicts(
    connection: AsyncConnection,
    workspace_id: UUID,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for entity in manifest["entities"]:
        for alias in entity.get("aliases", []):
            result = await connection.execute(
                """
                SELECT owner.entity_type, owner.canonical_name
                FROM knowledge_alias existing
                JOIN knowledge_entity owner ON owner.id = existing.entity_id
                WHERE existing.workspace_id = %s AND existing.active AND owner.active
                  AND owner.entity_type = %s
                  AND existing.normalized_alias = %s
                  AND owner.normalized_name <> %s
                """,
                (
                    workspace_id,
                    entity["entity_type"],
                    normalize_knowledge_text(alias["value"]),
                    normalize_knowledge_text(entity["canonical_name"]),
                ),
            )
            if row := await result.fetchone():
                conflicts.append(
                    {
                        "code": "database_alias_conflict",
                        "alias": alias["value"],
                        "incoming_entity": entity["canonical_name"],
                        "existing_entity": row["canonical_name"],
                    }
                )
    return conflicts


async def _evidence(
    connection: AsyncConnection,
    workspace_id: UUID,
    object_type: str,
    object_id: UUID,
    source_id: UUID,
    claim: str,
    actor_id: UUID,
) -> None:
    await connection.execute(
        """
        INSERT INTO knowledge_evidence (
            workspace_id, object_type, object_id, source_id, claim,
            evidence_level, verified_at, verified_by
        ) VALUES (%s, %s, %s, %s, %s, 'primary', now(), %s)
        ON CONFLICT (object_type, object_id, source_id, claim) DO NOTHING
        """,
        (workspace_id, object_type, object_id, source_id, claim, actor_id),
    )
