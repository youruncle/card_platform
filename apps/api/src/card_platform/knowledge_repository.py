from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.knowledge import normalize_knowledge_text, snapshot_checksum


def _json_safe(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str))


class KnowledgeConflictError(Exception):
    pass


class KnowledgePermissionError(Exception):
    pass


class KnowledgeRepository:
    async def workspace(self, connection: AsyncConnection) -> dict[str, Any]:
        result = await connection.execute(
            """
            SELECT kw.*, kv.version_number AS published_version
            FROM knowledge_workspace kw
            LEFT JOIN knowledge_version kv ON kv.id = kw.published_version_id
            WHERE kw.name = 'Card Platform'
            """
        )
        row = await result.fetchone()
        if row is None:
            raise RuntimeError("knowledge workspace is not initialized")
        return dict(row)

    async def require_role(
        self, connection: AsyncConnection, workspace_id: UUID, actor_id: UUID, allowed: set[str]
    ) -> str:
        result = await connection.execute(
            "SELECT role FROM knowledge_member WHERE workspace_id = %s AND user_id = %s",
            (workspace_id, actor_id),
        )
        row = await result.fetchone()
        if row is None or row["role"] not in allowed | {"admin"}:
            raise KnowledgePermissionError("当前用户没有执行此操作的知识库权限")
        return row["role"]

    async def list_entities(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        *,
        entity_type: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        result = await connection.execute(
            """
            SELECT e.*, COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'id', a.id, 'alias', a.alias, 'language', a.language,
                        'alias_type', a.alias_type, 'review_status', a.review_status,
                        'source', a.source, 'confidence', a.confidence
                    ) ORDER BY a.alias
                ) FILTER (WHERE a.id IS NOT NULL), '[]'::jsonb
            ) AS aliases
            FROM knowledge_entity e
            LEFT JOIN knowledge_alias a ON a.entity_id = e.id AND a.active
            WHERE e.workspace_id = %s AND e.active
              AND (%s::text IS NULL OR e.entity_type = %s::text)
              AND (%s::text IS NULL OR e.normalized_name LIKE '%%' || %s::text || '%%'
                   OR EXISTS (
                       SELECT 1 FROM knowledge_alias qa
                       WHERE qa.entity_id = e.id AND qa.active
                         AND qa.normalized_alias LIKE '%%' || %s::text || '%%'
                   ))
            GROUP BY e.id
            ORDER BY e.entity_type, e.canonical_name
            """,
            (
                workspace_id,
                entity_type,
                entity_type,
                query,
                normalize_knowledge_text(query),
                normalize_knowledge_text(query),
            ),
        )
        return [dict(row) for row in await result.fetchall()]

    async def create_entity(
        self,
        connection: AsyncConnection,
        workspace: dict[str, Any],
        actor_id: UUID,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> dict[str, Any]:
        if request_id:
            duplicate = await connection.execute(
                """
                SELECT after_value FROM knowledge_audit_log
                WHERE workspace_id = %s AND request_id = %s
                """,
                (workspace["id"], request_id),
            )
            if row := await duplicate.fetchone():
                return dict(row["after_value"])
        normalized = normalize_knowledge_text(payload["canonical_name"])
        result = await connection.execute(
            """
            INSERT INTO knowledge_entity (
                workspace_id, entity_type, canonical_name, normalized_name,
                parent_id, attributes, source, confidence, review_status, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s)
            RETURNING *
            """,
            (
                workspace["id"],
                payload["entity_type"],
                payload["canonical_name"].strip(),
                normalized,
                payload.get("parent_id"),
                Jsonb(payload.get("attributes") or {}),
                Jsonb(payload.get("source") or {}),
                payload.get("confidence", 1),
                actor_id,
            ),
        )
        entity = dict(await result.fetchone())
        await connection.execute(
            """
            UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now()
            WHERE id = %s
            """,
            (workspace["id"],),
        )
        await self.audit(
            connection, workspace["id"], actor_id, "create", "entity", entity["id"], None, entity, request_id
        )
        return entity

    async def add_alias(
        self,
        connection: AsyncConnection,
        workspace: dict[str, Any],
        actor_id: UUID,
        entity_id: UUID,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> dict[str, Any]:
        normalized = normalize_knowledge_text(payload["alias"])
        conflict = await connection.execute(
            """
            SELECT e.canonical_name
            FROM knowledge_alias a JOIN knowledge_entity e ON e.id = a.entity_id
            WHERE a.workspace_id = %s AND a.normalized_alias = %s AND a.active
              AND a.entity_id <> %s
            """,
            (workspace["id"], normalized, entity_id),
        )
        if row := await conflict.fetchone():
            raise KnowledgeConflictError(f"该别名已属于实体“{row['canonical_name']}”")
        result = await connection.execute(
            """
            INSERT INTO knowledge_alias (
                workspace_id, entity_id, alias, normalized_alias, language,
                alias_type, source, confidence, review_status, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s)
            RETURNING *
            """,
            (
                workspace["id"],
                entity_id,
                payload["alias"].strip(),
                normalized,
                payload.get("language", "und"),
                payload.get("alias_type", "synonym"),
                Jsonb(payload.get("source") or {}),
                payload.get("confidence", 1),
                actor_id,
            ),
        )
        alias = dict(await result.fetchone())
        await connection.execute(
            "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (workspace["id"],),
        )
        await self.audit(
            connection, workspace["id"], actor_id, "create", "alias", alias["id"], None, alias, request_id
        )
        return alias

    async def update_alias(
        self,
        connection: AsyncConnection,
        workspace: dict[str, Any],
        actor_id: UUID,
        alias_id: UUID,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> dict[str, Any]:
        previous_result = await connection.execute(
            "SELECT * FROM knowledge_alias WHERE id = %s AND workspace_id = %s FOR UPDATE",
            (alias_id, workspace["id"]),
        )
        previous_row = await previous_result.fetchone()
        if previous_row is None:
            raise KeyError("alias")
        previous = dict(previous_row)
        alias = str(payload.get("alias") or previous["alias"]).strip()
        normalized = normalize_knowledge_text(alias)
        conflict = await connection.execute(
            """
            SELECT e.canonical_name FROM knowledge_alias a
            JOIN knowledge_entity e ON e.id = a.entity_id
            WHERE a.workspace_id = %s AND a.normalized_alias = %s AND a.active
              AND a.id <> %s
            """,
            (workspace["id"], normalized, alias_id),
        )
        if row := await conflict.fetchone():
            raise KnowledgeConflictError(f"该别名已属于实体“{row['canonical_name']}”")
        result = await connection.execute(
            """
            UPDATE knowledge_alias
            SET alias = %s, normalized_alias = %s, language = %s, alias_type = %s,
                source = %s, confidence = %s,
                review_status = CASE WHEN %s THEN 'draft' ELSE review_status END,
                updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (
                alias, normalized, payload.get("language", previous["language"]),
                payload.get("alias_type", previous["alias_type"]),
                Jsonb(payload.get("source", previous["source"])),
                payload.get("confidence", previous["confidence"]),
                alias != previous["alias"] or payload.get("alias_type") != previous["alias_type"],
                alias_id,
            ),
        )
        updated = dict(await result.fetchone())
        await connection.execute(
            "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (workspace["id"],),
        )
        await self.audit(connection, workspace["id"], actor_id, "update", "alias", alias_id, previous, updated, request_id)
        return updated

    async def deactivate_alias(
        self, connection: AsyncConnection, workspace: dict[str, Any], actor_id: UUID,
        alias_id: UUID, request_id: str | None,
    ) -> dict[str, Any]:
        previous_result = await connection.execute(
            "SELECT * FROM knowledge_alias WHERE id = %s AND workspace_id = %s FOR UPDATE",
            (alias_id, workspace["id"]),
        )
        previous_row = await previous_result.fetchone()
        if previous_row is None:
            raise KeyError("alias")
        previous = dict(previous_row)
        result = await connection.execute(
            "UPDATE knowledge_alias SET active = false, updated_at = now() WHERE id = %s RETURNING *",
            (alias_id,),
        )
        updated = dict(await result.fetchone())
        await connection.execute(
            "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (workspace["id"],),
        )
        await self.audit(connection, workspace["id"], actor_id, "deactivate", "alias", alias_id, previous, updated, request_id)
        return updated

    async def review(
        self,
        connection: AsyncConnection,
        workspace: dict[str, Any],
        actor_id: UUID,
        object_type: str,
        object_id: UUID,
        decision: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        table = {"entity": "knowledge_entity", "alias": "knowledge_alias", "relation": "knowledge_relation"}[
            object_type
        ]
        previous_result = await connection.execute(
            f"SELECT * FROM {table} WHERE id = %s AND workspace_id = %s FOR UPDATE",
            (object_id, workspace["id"]),
        )
        previous = await previous_result.fetchone()
        if previous is None:
            raise KeyError(object_type)
        result = await connection.execute(
            f"""
            UPDATE {table}
            SET review_status = %s, reviewed_by = %s, updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (decision, actor_id, object_id),
        )
        updated = dict(await result.fetchone())
        await connection.execute(
            "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (workspace["id"],),
        )
        await self.audit(
            connection,
            workspace["id"],
            actor_id,
            f"review:{decision}",
            object_type,
            object_id,
            dict(previous),
            updated,
            request_id,
        )
        return updated

    async def build_snapshot(
        self, connection: AsyncConnection, workspace_id: UUID, version: int | None = None
    ) -> dict[str, Any]:
        entities_result = await connection.execute(
            """
            SELECT e.id, e.entity_type, e.canonical_name, e.parent_id, e.attributes,
                   COALESCE(array_agg(a.alias ORDER BY a.alias)
                       FILTER (WHERE a.id IS NOT NULL), '{}') AS aliases
            FROM knowledge_entity e
            LEFT JOIN knowledge_alias a ON a.entity_id = e.id
                AND a.active AND a.review_status = 'approved'
            WHERE e.workspace_id = %s AND e.active AND e.review_status = 'approved'
            GROUP BY e.id ORDER BY e.entity_type, e.normalized_name
            """,
            (workspace_id,),
        )
        relations_result = await connection.execute(
            """
            SELECT subject_entity_id, relation_type, object_entity_id, reason
            FROM knowledge_relation
            WHERE workspace_id = %s AND active AND review_status = 'approved'
            ORDER BY subject_entity_id, relation_type, object_entity_id
            """,
            (workspace_id,),
        )
        return {
            "version": version,
            "entities": [dict(row) for row in await entities_result.fetchall()],
            "relations": [dict(row) for row in await relations_result.fetchall()],
        }

    async def publish(
        self,
        connection: AsyncConnection,
        workspace: dict[str, Any],
        actor_id: UUID,
        expected_revision: int,
        release_notes: str,
        request_id: str | None,
        *,
        official_source_keys: list[str] | None = None,
        official_artifacts: list[dict] | None = None,
    ) -> dict[str, Any]:
        await connection.execute("SELECT pg_advisory_xact_lock(hashtext('card-platform-knowledge-publish'))")
        if request_id:
            duplicate = await connection.execute(
                """
                SELECT kv.* FROM knowledge_audit_log al
                JOIN knowledge_version kv ON kv.id = al.object_id
                WHERE al.workspace_id = %s AND al.request_id = %s AND al.action = 'publish'
                """,
                (workspace["id"], request_id),
            )
            if row := await duplicate.fetchone():
                return dict(row)
        locked_result = await connection.execute(
            "SELECT * FROM knowledge_workspace WHERE id = %s FOR UPDATE", (workspace["id"],)
        )
        locked = dict(await locked_result.fetchone())
        if locked["revision"] != expected_revision:
            raise KnowledgeConflictError(
                f"知识库已被其他人修改（当前 revision={locked['revision']}），请刷新后重试"
            )
        invalid_result = await connection.execute(
            """
            SELECT
              (SELECT count(*) FROM knowledge_entity
               WHERE workspace_id = %s AND active AND review_status <> 'approved')
              + (SELECT count(*) FROM knowledge_alias
                 WHERE workspace_id = %s AND active AND review_status <> 'approved')
              + (SELECT count(*) FROM knowledge_relation
                 WHERE workspace_id = %s AND active AND review_status <> 'approved') AS count
            """,
            (workspace["id"], workspace["id"], workspace["id"]),
        )
        invalid_count = (await invalid_result.fetchone())["count"]
        if invalid_count and not official_source_keys:
            raise KnowledgeConflictError(f"仍有 {invalid_count} 条未审核内容，不能发布")
        version_result = await connection.execute(
            "SELECT COALESCE(max(version_number), 0) + 1 AS next FROM knowledge_version WHERE workspace_id = %s",
            (workspace["id"],),
        )
        version_number = (await version_result.fetchone())["next"]
        snapshot = await self.build_snapshot(connection, workspace["id"], version_number)
        if official_source_keys:
            # Official automation may replace only its own facts. Keep the prior
            # published facts for all other sources, including entities whose
            # workspace versions are currently being edited as drafts.
            source_result = await connection.execute(
                """SELECT e.id FROM knowledge_entity e
                   JOIN knowledge_source s ON s.workspace_id=e.workspace_id
                     AND s.source_key=e.source->>'source_key'
                   WHERE e.workspace_id=%s AND s.source_key=ANY(%s)
                     AND s.source_type IN ('official_checklist', 'official_product', 'official_odds')""",
                (workspace["id"], official_source_keys),
            )
            official_ids = {str(row["id"]) for row in await source_result.fetchall()}
            previous_result = await connection.execute(
                "SELECT snapshot FROM knowledge_version WHERE id=%s",
                (locked["published_version_id"],),
            )
            previous_row = await previous_result.fetchone()
            previous = previous_row["snapshot"] if previous_row else {"entities": [], "relations": []}
            snapshot["entities"] = [
                entity for entity in previous["entities"] if str(entity["id"]) not in official_ids
            ] + [entity for entity in snapshot["entities"] if str(entity["id"]) in official_ids]
            active_ids = {str(entity["id"]) for entity in snapshot["entities"]}
            snapshot["relations"] = [relation for relation in previous["relations"]
                if str(relation["subject_entity_id"]) in active_ids
                and str(relation["object_entity_id"]) in active_ids]
        # Immutable official fact attachments are part of the same version/checksum.
        if official_artifacts is not None:
            snapshot["official_artifacts"] = official_artifacts
        elif locked["published_version_id"]:
            previous_artifacts = await connection.execute(
                "SELECT snapshot->'official_artifacts' AS artifacts FROM knowledge_version WHERE id=%s",
                (locked["published_version_id"],),
            )
            snapshot["official_artifacts"] = (await previous_artifacts.fetchone())["artifacts"] or []
        snapshot = _json_safe(snapshot)
        checksum = snapshot_checksum(snapshot)
        inserted = await connection.execute(
            """
            INSERT INTO knowledge_version (
                workspace_id, version_number, parent_version_id, snapshot, change_summary,
                checksum, release_notes, published_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                workspace["id"],
                version_number,
                locked["published_version_id"],
                Jsonb(snapshot),
                Jsonb({"entity_count": len(snapshot["entities"]), "relation_count": len(snapshot["relations"])}),
                checksum,
                release_notes,
                actor_id,
            ),
        )
        version = dict(await inserted.fetchone())
        await connection.execute(
            """
            UPDATE knowledge_workspace
            SET published_version_id = %s, revision = revision + 1, updated_at = now()
            WHERE id = %s
            """,
            (version["id"], workspace["id"]),
        )
        await self._record_impact(connection, version["id"], snapshot)
        await self.audit(
            connection, workspace["id"], actor_id, "publish", "version", version["id"], None, version, request_id
        )
        return version

    async def rollback(
        self,
        connection: AsyncConnection,
        workspace: dict[str, Any],
        actor_id: UUID,
        target_version_id: UUID,
        expected_revision: int,
        request_id: str | None,
    ) -> dict[str, Any]:
        await connection.execute("SELECT pg_advisory_xact_lock(hashtext('card-platform-knowledge-publish'))")
        if request_id:
            duplicate = await connection.execute(
                """
                SELECT kv.* FROM knowledge_audit_log al
                JOIN knowledge_version kv ON kv.id = al.object_id
                WHERE al.workspace_id = %s AND al.request_id = %s AND al.action = 'rollback'
                """,
                (workspace["id"], request_id),
            )
            if row := await duplicate.fetchone():
                return dict(row)
        current_result = await connection.execute(
            "SELECT * FROM knowledge_workspace WHERE id = %s FOR UPDATE", (workspace["id"],)
        )
        current = dict(await current_result.fetchone())
        if current["revision"] != expected_revision:
            raise KnowledgeConflictError("知识库 revision 已变化，请刷新后重试")
        target_result = await connection.execute(
            "SELECT * FROM knowledge_version WHERE id = %s AND workspace_id = %s",
            (target_version_id, workspace["id"]),
        )
        target = await target_result.fetchone()
        if target is None:
            raise KeyError("version")
        await connection.execute(
            """
            UPDATE knowledge_version SET status = 'rolled_back', rolled_back_at = now()
            WHERE id = %s AND id <> %s
            """,
            (current["published_version_id"], target_version_id),
        )
        await connection.execute(
            """
            UPDATE knowledge_workspace
            SET published_version_id = %s, revision = revision + 1, updated_at = now()
            WHERE id = %s
            """,
            (target_version_id, workspace["id"]),
        )
        await self._record_impact(connection, target_version_id, target["snapshot"])
        await self.audit(
            connection,
            workspace["id"],
            actor_id,
            "rollback",
            "version",
            target_version_id,
            {"version_id": str(current["published_version_id"])},
            {"version_id": str(target_version_id)},
            request_id,
        )
        return dict(target)

    async def _record_impact(
        self, connection: AsyncConnection, version_id: UUID, snapshot: dict[str, Any]
    ) -> None:
        terms = [
            alias
            for entity in snapshot.get("entities", [])
            for alias in (entity["canonical_name"], *entity.get("aliases", []))
            if len(normalize_knowledge_text(alias)) >= 2
        ]
        if not terms:
            return
        patterns = [f"%{normalize_knowledge_text(term)}%" for term in terms]
        await connection.execute(
            """
            INSERT INTO knowledge_impact (version_id, object_type, object_id, reason)
            SELECT DISTINCT %s, 'market_comp', mc.id,
                   jsonb_build_object('listing_title', mc.listing_title)
            FROM market_comp mc
            WHERE lower(COALESCE(mc.listing_title, '')) LIKE ANY(%s)
            ON CONFLICT DO NOTHING
            """,
            (version_id, patterns),
        )
        await connection.execute(
            """
            INSERT INTO knowledge_impact (version_id, object_type, object_id, reason)
            SELECT DISTINCT %s, 'collection_item', ci.id,
                   jsonb_build_object('card_definition_id', cd.id)
            FROM collection_item ci
            JOIN card_definition cd ON cd.id = ci.card_definition_id
            WHERE lower(COALESCE(cd.insert_name, '') || ' ' || COALESCE(cd.parallel_name, '')
                        || ' ' || COALESCE(cd.card_number, '')) LIKE ANY(%s)
            ON CONFLICT DO NOTHING
            """,
            (version_id, patterns),
        )

    async def audit(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        actor_id: UUID,
        action: str,
        object_type: str,
        object_id: UUID | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        request_id: str | None,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO knowledge_audit_log (
                workspace_id, actor_id, action, object_type, object_id,
                request_id, before_value, after_value
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                workspace_id,
                actor_id,
                action,
                object_type,
                object_id,
                request_id,
                Jsonb(_json_safe(before)) if before is not None else None,
                Jsonb(_json_safe(after)) if after is not None else None,
            ),
        )
