from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from card_platform.knowledge import ENTITY_TYPES, KnowledgeResolver, normalize_knowledge_text
from card_platform.auth import AuthenticatedUser, require_admin
from card_platform.knowledge_batch import apply_manifest
from card_platform.knowledge_batch_catalog import catalog_manifest, catalog_metadata
from card_platform.knowledge_manifest import load_manifest, validate_manifest
from card_platform.knowledge_repository import (
    KnowledgeConflictError,
    KnowledgePermissionError,
    KnowledgeRepository,
)
from card_platform.knowledge_seed import CURATED_RELATIONS, builtin_seed
from card_platform.marketplace_aliases import INITIAL_MARKETPLACE_ALIAS_CATALOG
from card_platform.marketplace_alias_discovery import AliasDiscoveryError, build_alias_proposal
from card_platform.knowledge_browser_api import router as browser_router
from card_platform.knowledge_atlas_api import router as atlas_router

async def _knowledge_connection(request: Request):
    async with request.app.state.database.connection() as connection:
        request.state.connection = connection
        yield


async def _knowledge_session(
    request: Request, user: AuthenticatedUser = Depends(require_admin),
) -> AuthenticatedUser:
    request.state.knowledge_user = user
    return user


router = APIRouter(
    prefix="/v1/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(_knowledge_session), Depends(_knowledge_connection)],
)
repository = KnowledgeRepository()


class EntityCreate(BaseModel):
    entity_type: str
    canonical_name: str = Field(min_length=1, max_length=200)
    parent_id: UUID | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1, ge=0, le=1)


class AliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    language: str = Field(default="und", max_length=12)
    alias_type: Literal["canonical", "synonym", "translation", "misspelling", "seller_term"] = (
        "synonym"
    )
    source: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1, ge=0, le=1)


class AliasUpdate(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=200)
    language: str | None = Field(default=None, max_length=12)
    alias_type: Literal["canonical", "synonym", "translation", "misspelling", "seller_term"] | None = None
    source: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class ReviewPayload(BaseModel):
    decision: Literal["approved", "rejected"]


class PublishPayload(BaseModel):
    expected_revision: int = Field(gt=0)
    release_notes: str = Field(default="", max_length=2000)


class RollbackPayload(BaseModel):
    target_version_id: UUID
    expected_revision: int = Field(gt=0)


class ValidatePayload(BaseModel):
    identity: dict[str, Any]


class ObservationCreate(BaseModel):
    observed_text: str = Field(min_length=1, max_length=500)
    context: dict[str, Any] = Field(default_factory=dict)


class AliasDiscoverySample(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=2000)


class AliasDiscoveryCreate(BaseModel):
    entity_id: UUID
    observed_alias: str = Field(min_length=1, max_length=200)
    applicability: dict[str, Any]
    samples: list[AliasDiscoverySample] = Field(min_length=2, max_length=20)


class RelationCreate(BaseModel):
    subject_entity_id: UUID
    relation_type: Literal[
        "conflicts_with", "excludes", "requires", "belongs_to", "equivalent_to",
        "manufactured_by", "licensed_by", "product_family_of", "available_in"
    ]
    object_entity_id: UUID
    reason: str = Field(default="", max_length=1000)
    confidence: float = Field(default=1, ge=0, le=1)


class ObservationReview(BaseModel):
    proposed_entity_id: UUID
    proposed_alias: str = Field(min_length=1, max_length=200)
    replay_result: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    decision: Literal["validated", "rejected"]


class BatchApply(BaseModel):
    dry_run: bool = True


async def _actor(
    request: Request,
    allowed: set[str],
    actor_id: UUID | None,
) -> tuple[dict[str, Any], UUID]:
    actor_id = _session_actor(request, actor_id)
    workspace = await repository.workspace(request.state.connection)
    try:
        await repository.require_role(request.state.connection, workspace["id"], actor_id, allowed)
    except KnowledgePermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    return workspace, actor_id


def _session_actor(request: Request, claimed_actor: UUID | None) -> UUID:
    user = getattr(request.state, "knowledge_user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if claimed_actor is not None and claimed_actor != user.id:
        raise HTTPException(status_code=403, detail="操作用户必须与当前登录用户一致")
    return user.id


@router.get("/batches/catalog")
async def batch_catalog() -> list[dict[str, Any]]:
    """List the safe, manifest-backed import batches available to an administrator."""
    return catalog_metadata()


@router.post("/batches/{batch_key}")
async def apply_catalog_batch(
    batch_key: str,
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    """Apply an allow-listed manifest without allowing arbitrary filesystem paths."""
    manifest = catalog_manifest(batch_key)
    if manifest is None:
        raise HTTPException(status_code=404, detail="未找到知识导入批次")
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.get("/workspace")
async def workspace(request: Request) -> dict[str, Any]:
    value = await repository.workspace(request.state.connection)
    count_result = await request.state.connection.execute(
        "SELECT count(*) AS count FROM knowledge_member WHERE workspace_id = %s",
        (value["id"],),
    )
    value["member_count"] = (await count_result.fetchone())["count"]
    value["runtime_version"] = request.app.state.knowledge_runtime.resolver.version
    value["runtime_version_id"] = request.app.state.knowledge_runtime.version_id
    return value


@router.post("/bootstrap")
async def bootstrap(
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, str]:
    x_knowledge_actor = _session_actor(request, x_knowledge_actor)
    async with request.state.connection.transaction():
        workspace = await repository.workspace(request.state.connection)
        await request.state.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('card-platform-knowledge-bootstrap'))"
        )
        count_result = await request.state.connection.execute(
            "SELECT count(*) AS count FROM knowledge_member WHERE workspace_id = %s",
            (workspace["id"],),
        )
        if (await count_result.fetchone())["count"]:
            raise HTTPException(status_code=409, detail="知识库权限已经初始化")
        user_result = await request.state.connection.execute(
            "SELECT id FROM app_user WHERE id = %s", (x_knowledge_actor,)
        )
        if await user_result.fetchone() is None:
            raise HTTPException(status_code=404, detail="操作用户不存在")
        await request.state.connection.execute(
            "INSERT INTO knowledge_member (workspace_id, user_id, role) VALUES (%s, %s, 'admin')",
            (workspace["id"], x_knowledge_actor),
        )
        await repository.audit(
            request.state.connection,
            workspace["id"],
            x_knowledge_actor,
            "bootstrap_admin",
            "workspace",
            workspace["id"],
            None,
            {"user_id": str(x_knowledge_actor), "role": "admin"},
            "knowledge-bootstrap-v1",
        )
        return {"role": "admin", "user_id": str(x_knowledge_actor)}


@router.get("/entities")
async def entities(
    request: Request,
    entity_type: str | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> list[dict[str, Any]]:
    workspace = await repository.workspace(request.state.connection)
    return await repository.list_entities(
        request.state.connection, workspace["id"], entity_type=entity_type, query=q
    )


@router.post("/entities", status_code=status.HTTP_201_CREATED)
async def create_entity(
    payload: EntityCreate,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    if payload.entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"不支持的实体类型：{payload.entity_type}")
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"editor"}, x_knowledge_actor)
        try:
            return await repository.create_entity(
                request.state.connection,
                workspace,
                actor_id,
                payload.model_dump(),
                idempotency_key,
            )
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise HTTPException(status_code=409, detail="同类型规范名已存在") from error
            raise


@router.post("/entities/{entity_id}/aliases", status_code=status.HTTP_201_CREATED)
async def add_alias(
    entity_id: UUID,
    payload: AliasCreate,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"editor"}, x_knowledge_actor)
        try:
            return await repository.add_alias(
                request.state.connection,
                workspace,
                actor_id,
                entity_id,
                payload.model_dump(),
                idempotency_key,
            )
        except KnowledgeConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error


@router.patch("/aliases/{alias_id}")
async def update_alias(
    alias_id: UUID, payload: AliasUpdate, request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"editor"}, x_knowledge_actor)
        try:
            return await repository.update_alias(
                request.state.connection, workspace, actor_id, alias_id,
                payload.model_dump(exclude_none=True), idempotency_key,
            )
        except KnowledgeConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="别名不存在") from error


@router.delete("/aliases/{alias_id}")
async def delete_alias(
    alias_id: UUID, request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Soft-delete so published snapshots and audit evidence remain reproducible."""
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"editor"}, x_knowledge_actor)
        try:
            return await repository.deactivate_alias(
                request.state.connection, workspace, actor_id, alias_id, idempotency_key
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="别名不存在") from error


@router.post("/aliases/initialize")
async def initialize_marketplace_aliases(
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, int]:
    """Create auditable, unpublished seller-term drafts from the shared catalog.

    Public marketplace wording improves recall but does not prove identity, so
    candidates require review and an explicit version publish before runtime use.
    """
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"editor"}, x_knowledge_actor)
        created_entities = created_aliases = existing_aliases = 0
        for candidate in INITIAL_MARKETPLACE_ALIAS_CATALOG:
            entity_result = await request.state.connection.execute(
                """
                INSERT INTO knowledge_entity (
                    workspace_id, entity_type, canonical_name, normalized_name,
                    source, confidence, review_status, created_by
                ) VALUES (%s, %s, %s, %s, %s, 0.85, 'draft', %s)
                ON CONFLICT (workspace_id, entity_type, normalized_name) DO UPDATE
                    SET active = true, updated_at = now()
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    workspace["id"], candidate.entity_type, candidate.canonical_name,
                    normalize_knowledge_text(candidate.canonical_name),
                    Jsonb({"kind": "marketplace_alias_catalog", "source_url": candidate.evidence_url(), "note": candidate.note}),
                    actor_id,
                ),
            )
            entity = dict(await entity_result.fetchone())
            created_entities += int(entity["inserted"])
            for alias in candidate.aliases[1:]:
                result = await request.state.connection.execute(
                    """
                    INSERT INTO knowledge_alias (
                        workspace_id, entity_id, alias, normalized_alias, language, alias_type,
                        source, confidence, review_status, created_by
                    ) VALUES (%s, %s, %s, %s, %s, 'seller_term', %s, 0.80, 'draft', %s)
                    ON CONFLICT (workspace_id, entity_id, normalized_alias) DO NOTHING
                    RETURNING id
                    """,
                    (
                        workspace["id"], entity["id"], alias, normalize_knowledge_text(alias),
                        "zh" if any("\u4e00" <= char <= "\u9fff" for char in alias) else "en",
                        Jsonb({"kind": "marketplace_alias_catalog", "source_url": candidate.evidence_url(), "canonical_name": candidate.canonical_name, "note": candidate.note, "verification": "public_cardhobby_query_required"}),
                        actor_id,
                    ),
                )
                if await result.fetchone():
                    created_aliases += 1
                else:
                    existing_aliases += 1
        await request.state.connection.execute(
            "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (workspace["id"],),
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id, "initialize_marketplace_aliases",
            "workspace", workspace["id"], None,
            {"created_entities": created_entities, "created_aliases": created_aliases, "existing_aliases": existing_aliases}, None,
        )
        return {"created_entities": created_entities, "created_aliases": created_aliases, "existing_aliases": existing_aliases}


@router.post("/aliases/discover", status_code=status.HTTP_201_CREATED)
async def discover_marketplace_alias(
    payload: AliasDiscoveryCreate,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Collect a scoped, source-backed alias candidate without activating it."""
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"editor"}, x_knowledge_actor)
        entity_result = await request.state.connection.execute(
            """
            SELECT id, entity_type, canonical_name FROM knowledge_entity
            WHERE id = %s AND workspace_id = %s AND active
            """,
            (payload.entity_id, workspace["id"]),
        )
        entity = await entity_result.fetchone()
        if entity is None:
            raise HTTPException(status_code=404, detail="目标知识实体不存在")
        try:
            proposal = build_alias_proposal(
                observed_alias=payload.observed_alias,
                canonical_name=entity["canonical_name"],
                entity_type=entity["entity_type"],
                applicability=payload.applicability,
                samples=[sample.model_dump() for sample in payload.samples],
            )
        except AliasDiscoveryError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        context = {
            "kind": "cardhobby_public_alias_discovery",
            "canonical_name": proposal.canonical_name,
            "entity_type": proposal.entity_type,
            "applicability": proposal.applicability,
            "source_samples": [asdict(sample) for sample in proposal.samples],
            "sample_count": len(proposal.samples),
            "requires_human_review": True,
        }
        result = await request.state.connection.execute(
            """
            INSERT INTO knowledge_observation (
                workspace_id, observed_text, normalized_text, context,
                occurrence_count, proposed_entity_id, proposed_alias,
                proposal_source, confidence, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'rule', %s, 'proposed')
            ON CONFLICT (workspace_id, normalized_text) DO UPDATE
            SET occurrence_count = GREATEST(
                    knowledge_observation.occurrence_count, EXCLUDED.occurrence_count
                ),
                context = EXCLUDED.context,
                proposed_entity_id = EXCLUDED.proposed_entity_id,
                proposed_alias = EXCLUDED.proposed_alias,
                proposal_source = 'rule', confidence = EXCLUDED.confidence,
                status = CASE WHEN knowledge_observation.status = 'promoted'
                              THEN knowledge_observation.status ELSE 'proposed' END,
                last_seen_at = now(), updated_at = now()
            RETURNING *
            """,
            (
                workspace["id"], proposal.observed_alias,
                normalize_knowledge_text(proposal.observed_alias), Jsonb(context),
                len(proposal.samples), payload.entity_id, proposal.observed_alias,
                proposal.confidence,
            ),
        )
        observation = dict(await result.fetchone())
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "discover_marketplace_alias", "observation", observation["id"],
            None, observation, idempotency_key,
        )
        return observation


@router.get("/relations")
async def relations(request: Request) -> list[dict[str, Any]]:
    workspace = await repository.workspace(request.state.connection)
    result = await request.state.connection.execute(
        """
        SELECT r.*, s.canonical_name AS subject_name, o.canonical_name AS object_name
        FROM knowledge_relation r
        JOIN knowledge_entity s ON s.id = r.subject_entity_id
        JOIN knowledge_entity o ON o.id = r.object_entity_id
        WHERE r.workspace_id = %s AND r.active
        ORDER BY r.updated_at DESC
        """,
        (workspace["id"],),
    )
    return [dict(row) for row in await result.fetchall()]


@router.post("/relations", status_code=status.HTTP_201_CREATED)
async def create_relation(
    payload: RelationCreate,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    if payload.subject_entity_id == payload.object_entity_id:
        raise HTTPException(status_code=422, detail="关系两端不能是同一实体")
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"editor"}, x_knowledge_actor)
        result = await request.state.connection.execute(
            """
            INSERT INTO knowledge_relation (
                workspace_id, subject_entity_id, relation_type, object_entity_id,
                reason, confidence, review_status, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s)
            RETURNING *
            """,
            (
                workspace["id"],
                payload.subject_entity_id,
                payload.relation_type,
                payload.object_entity_id,
                payload.reason,
                payload.confidence,
                actor_id,
            ),
        )
        relation = dict(await result.fetchone())
        await request.state.connection.execute(
            "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (workspace["id"],),
        )
        await repository.audit(
            request.state.connection,
            workspace["id"],
            actor_id,
            "create",
            "relation",
            relation["id"],
            None,
            relation,
            idempotency_key,
        )
        return relation


@router.post("/{object_type}/{object_id}/review")
async def review(
    object_type: Literal["entity", "alias", "relation"],
    object_id: UUID,
    payload: ReviewPayload,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"reviewer"}, x_knowledge_actor)
        try:
            return await repository.review(
                request.state.connection,
                workspace,
                actor_id,
                object_type,
                object_id,
                payload.decision,
                idempotency_key,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="知识对象不存在") from error


@router.post("/seed")
async def seed(
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, int]:
    raise HTTPException(
        status_code=410,
        detail="v2 内置词表只有模型整理来源，已在 v5 证据审计中停用，禁止重新导入",
    )
    # Kept temporarily as migration reference; unreachable by design.
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        entity_count = alias_count = 0
        for row in builtin_seed():
            result = await request.state.connection.execute(
                """
                INSERT INTO knowledge_entity (
                    workspace_id, entity_type, canonical_name, normalized_name,
                    source, confidence, review_status, created_by, reviewed_by
                ) VALUES (%s, %s, %s, %s, %s, 1, 'approved', %s, %s)
                ON CONFLICT (workspace_id, entity_type, normalized_name)
                DO UPDATE SET active = true, source = EXCLUDED.source,
                              confidence = EXCLUDED.confidence
                RETURNING id
                """,
                (
                    workspace["id"],
                    row["entity_type"],
                    row["canonical_name"],
                    normalize_knowledge_text(row["canonical_name"]),
                    Jsonb(
                        {
                            "kind": "llm_curated_bootstrap",
                            "curation": "model_proposed_human_requested_regression_validated",
                            "version": 2,
                        }
                    ),
                    actor_id,
                    actor_id,
                ),
            )
            entity_id = (await result.fetchone())["id"]
            entity_count += 1
            for alias in row["aliases"]:
                await request.state.connection.execute(
                    """
                    INSERT INTO knowledge_alias (
                        workspace_id, entity_id, alias, normalized_alias, language,
                        alias_type, source, confidence, review_status, created_by, reviewed_by
                    ) VALUES (%s, %s, %s, %s, %s, 'synonym', %s, 1, 'approved', %s, %s)
                    ON CONFLICT (workspace_id, entity_id, normalized_alias)
                    DO UPDATE SET active = true, review_status = 'approved',
                                  source = EXCLUDED.source,
                                  confidence = EXCLUDED.confidence
                    """,
                    (
                        workspace["id"],
                        entity_id,
                        alias,
                        normalize_knowledge_text(alias),
                        "zh" if any("\u4e00" <= char <= "\u9fff" for char in alias) else "en",
                        Jsonb(
                            {
                                "kind": "llm_curated_bootstrap",
                                "curation": (
                                    "model_proposed_human_requested_regression_validated"
                                ),
                                "version": 2,
                            }
                        ),
                        actor_id,
                        actor_id,
                    ),
                )
                alias_count += 1
        relation_count = 0
        for (
            subject_type,
            subject_name,
            relation_type,
            object_type,
            object_name,
            reason,
        ) in CURATED_RELATIONS:
            relation_result = await request.state.connection.execute(
                """
                INSERT INTO knowledge_relation (
                    workspace_id, subject_entity_id, relation_type, object_entity_id,
                    reason, source, confidence, review_status, created_by, reviewed_by
                )
                SELECT %s, subject.id, %s, object.id, %s, %s, 0.98,
                       'approved', %s, %s
                FROM knowledge_entity subject, knowledge_entity object
                WHERE subject.workspace_id = %s
                  AND subject.entity_type = %s AND subject.canonical_name = %s
                  AND object.workspace_id = %s
                  AND object.entity_type = %s AND object.canonical_name = %s
                ON CONFLICT (
                    workspace_id, subject_entity_id, relation_type, object_entity_id
                ) DO UPDATE SET active = true, review_status = 'approved'
                RETURNING id
                """,
                (
                    workspace["id"],
                    relation_type,
                    reason,
                    Jsonb(
                        {
                            "kind": "llm_curated_bootstrap",
                            "model_policy": "human_requested_and_regression_validated",
                            "version": 1,
                        }
                    ),
                    actor_id,
                    actor_id,
                    workspace["id"],
                    subject_type,
                    subject_name,
                    workspace["id"],
                    object_type,
                    object_name,
                ),
            )
            relation_count += len(await relation_result.fetchall())
        await request.state.connection.execute(
            "UPDATE knowledge_workspace SET revision = revision + 1, updated_at = now() WHERE id = %s",
            (workspace["id"],),
        )
        await repository.audit(
            request.state.connection,
            workspace["id"],
            actor_id,
            "seed",
            "workspace",
            workspace["id"],
            None,
            {
                "entities": entity_count,
                "aliases": alias_count,
                "relations": relation_count,
            },
            None,
        )
        return {
            "entities": entity_count,
            "aliases": alias_count,
            "relations": relation_count,
        }


@router.post("/batches/mvp-v3")
async def apply_mvp_v3(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="MVP v3 清单已被 v5 逐项证据审计取代，禁止重新应用",
    )
    # Kept temporarily as migration reference; unreachable by design.
    manifest_path = (
        Path(__file__).parents[4] / "data" / "knowledge" / "basketball_mvp_v3.json"
    )
    manifest = load_manifest(manifest_path)
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection,
            workspace["id"],
            actor_id,
            manifest,
            report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection,
            workspace["id"],
            actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply",
            "import_batch",
            batch["id"],
            None,
            batch,
            None,
        )
        return batch


@router.get("/batches")
async def batches(request: Request) -> list[dict[str, Any]]:
    workspace = await repository.workspace(request.state.connection)
    result = await request.state.connection.execute(
        """
        SELECT * FROM knowledge_import_batch
        WHERE workspace_id = %s ORDER BY created_at DESC
        """,
        (workspace["id"],),
    )
    return [dict(row) for row in await result.fetchall()]


@router.post("/batches/modern-core-v1")
async def apply_modern_core_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "modern_core_checklists_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.post("/batches/industry-taxonomy-topps-v1")
async def apply_industry_taxonomy_topps_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    """Apply the audited Topps/Fanatics industry-taxonomy correction."""
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "industry_taxonomy_topps_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.post("/batches/manufacturer-landscape-v1")
async def apply_manufacturer_landscape_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    """Apply verified manufacturers while retaining unverified history as inactive candidates."""
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "manufacturer_landscape_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.post("/batches/topps-basketball-2526-core-v1")
async def apply_topps_basketball_2526_core_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "topps_basketball_2526_core_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.post("/batches/topps-identity-normalization-v1")
async def apply_topps_identity_normalization_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "topps_identity_normalization_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.post("/batches/panini-high-end-2324-v1")
async def apply_panini_high_end_2324_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "panini_high_end_2324_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.post("/batches/historical-brand-era-v1")
async def apply_historical_brand_era_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "historical_brand_era_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.post("/batches/card-identity-one-one-2324-v1")
async def apply_card_identity_one_one_2324_v1(
    payload: BatchApply,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    manifest = load_manifest(
        Path(__file__).parents[4] / "data" / "knowledge" / "card_identity_one_one_2324_v1.json"
    )
    report = validate_manifest(manifest)
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"admin"}, x_knowledge_actor)
        batch = await apply_manifest(
            request.state.connection, workspace["id"], actor_id, manifest, report,
            dry_run=payload.dry_run,
        )
        await repository.audit(
            request.state.connection, workspace["id"], actor_id,
            "batch_dry_run" if payload.dry_run else "batch_apply", "import_batch",
            batch["id"], None, batch, None,
        )
        return batch


@router.get("/coverage")
async def coverage(
    request: Request,
    coverage_status: str | None = None,
) -> dict[str, Any]:
    workspace = await repository.workspace(request.state.connection)
    result = await request.state.connection.execute(
        """
        SELECT c.*, manufacturer.canonical_name AS manufacturer_name,
               product.canonical_name AS product_name,
               insert_entity.canonical_name AS insert_name,
               parallel.canonical_name AS parallel_name,
               source.title AS source_title, source.url AS source_url
        FROM knowledge_coverage c
        LEFT JOIN knowledge_entity manufacturer ON manufacturer.id = c.manufacturer_entity_id
        LEFT JOIN knowledge_entity product ON product.id = c.product_entity_id
        LEFT JOIN knowledge_entity insert_entity ON insert_entity.id = c.insert_entity_id
        LEFT JOIN knowledge_entity parallel ON parallel.id = c.parallel_entity_id
        LEFT JOIN knowledge_source source ON source.id = c.source_id
        WHERE c.workspace_id = %s
          AND (manufacturer.id IS NULL OR manufacturer.active)
          AND (product.id IS NULL OR product.active)
          AND (insert_entity.id IS NULL OR insert_entity.active)
          AND (parallel.id IS NULL OR parallel.active)
          AND (%s::text IS NULL OR c.coverage_status = %s::text)
        ORDER BY manufacturer_name, product_name, season_start NULLS LAST,
                 insert_name NULLS FIRST, parallel_name NULLS FIRST
        """,
        (workspace["id"], coverage_status, coverage_status),
    )
    rows = [dict(row) for row in await result.fetchall()]
    counts = coverage_counts(rows)
    return {"counts": counts, "rows": rows}


def coverage_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count coverage states without hiding rows that lack a source."""
    counts: dict[str, int] = {}
    for row in rows:
        status = row["coverage_status"]
        counts[status] = counts.get(status, 0) + 1
        if row["source_id"] is None:
            counts["source_missing"] = counts.get("source_missing", 0) + 1
    return counts


@router.post("/publish")
async def publish(
    payload: PublishPayload,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"publisher"}, x_knowledge_actor)
        try:
            version = await repository.publish(
                request.state.connection,
                workspace,
                actor_id,
                payload.expected_revision,
                payload.release_notes,
                idempotency_key,
            )
            await request.state.connection.execute(
                """
                UPDATE knowledge_import_batch
                SET status = 'published', published_version_id = %s
                WHERE workspace_id = %s AND status = 'applied' AND NOT dry_run
                """,
                (version["id"], workspace["id"]),
            )
            request.app.state.knowledge_runtime.activate(version["snapshot"], version["id"])
            return version
        except KnowledgeConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollback")
async def rollback(
    payload: RollbackPayload,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"publisher"}, x_knowledge_actor)
        try:
            version = await repository.rollback(
                request.state.connection,
                workspace,
                actor_id,
                payload.target_version_id,
                payload.expected_revision,
                idempotency_key,
            )
            request.app.state.knowledge_runtime.activate(version["snapshot"], version["id"])
            return version
        except KnowledgeConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="目标版本不存在") from error


@router.get("/versions")
async def versions(request: Request) -> list[dict[str, Any]]:
    workspace = await repository.workspace(request.state.connection)
    result = await request.state.connection.execute(
        """
        SELECT id, version_number, status, change_summary, checksum, release_notes,
               published_by, published_at, rolled_back_at
        FROM knowledge_version WHERE workspace_id = %s
        ORDER BY version_number DESC
        """,
        (workspace["id"],),
    )
    return [dict(row) for row in await result.fetchall()]


@router.get("/observations")
async def observations(request: Request) -> list[dict[str, Any]]:
    workspace = await repository.workspace(request.state.connection)
    result = await request.state.connection.execute(
        """
        SELECT * FROM knowledge_observation WHERE workspace_id = %s
        ORDER BY occurrence_count DESC, last_seen_at DESC
        """,
        (workspace["id"],),
    )
    return [dict(row) for row in await result.fetchall()]


@router.post("/observations")
async def observe(payload: ObservationCreate, request: Request) -> dict[str, Any]:
    workspace = await repository.workspace(request.state.connection)
    result = await request.state.connection.execute(
        """
        INSERT INTO knowledge_observation (
            workspace_id, observed_text, normalized_text, context
        ) VALUES (%s, %s, %s, %s)
        ON CONFLICT (workspace_id, normalized_text)
        DO UPDATE SET occurrence_count = knowledge_observation.occurrence_count + 1,
                      context = knowledge_observation.context || EXCLUDED.context,
                      last_seen_at = now(), updated_at = now()
        RETURNING *
        """,
        (
            workspace["id"],
            payload.observed_text,
            normalize_knowledge_text(payload.observed_text),
            Jsonb(payload.context),
        ),
    )
    await request.state.connection.commit()
    return dict(await result.fetchone())


@router.post("/observations/{observation_id}/review")
async def review_observation(
    observation_id: UUID,
    payload: ObservationReview,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, Any]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"reviewer"}, x_knowledge_actor)
        result = await request.state.connection.execute(
            """
            UPDATE knowledge_observation
            SET proposed_entity_id = %s, proposed_alias = %s, proposal_source = 'human',
                replay_result = %s, confidence = %s, status = %s,
                reviewed_by = %s, updated_at = now()
            WHERE id = %s AND workspace_id = %s
            RETURNING *
            """,
            (
                payload.proposed_entity_id,
                payload.proposed_alias,
                Jsonb(payload.replay_result),
                payload.confidence,
                payload.decision,
                actor_id,
                observation_id,
                workspace["id"],
            ),
        )
        row = await result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="候选观察不存在")
        return dict(row)


@router.post("/observations/{observation_id}/promote")
async def promote_observation(
    observation_id: UUID,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"reviewer"}, x_knowledge_actor)
        result = await request.state.connection.execute(
            """
            SELECT * FROM knowledge_observation
            WHERE id = %s AND workspace_id = %s FOR UPDATE
            """,
            (observation_id, workspace["id"]),
        )
        observation = await result.fetchone()
        if observation is None:
            raise HTTPException(status_code=404, detail="候选观察不存在")
        if (
            observation["status"] != "validated"
            or observation["confidence"] is None
            or observation["confidence"] < 0.9
            or not observation["replay_result"]
        ):
            raise HTTPException(
                status_code=409,
                detail="候选必须完成人工验证、历史回放且置信度不低于 0.90",
            )
        alias = await repository.add_alias(
            request.state.connection,
            workspace,
            actor_id,
            observation["proposed_entity_id"],
            {
                "alias": observation["proposed_alias"],
                "language": (
                    "zh"
                    if any(
                        "\u4e00" <= char <= "\u9fff"
                        for char in observation["proposed_alias"]
                    )
                    else "en"
                ),
                "alias_type": "seller_term",
                "source": {
                    "kind": "observation_promotion",
                    "observation_id": str(observation_id),
                    "replay_result": observation["replay_result"],
                },
                "confidence": float(observation["confidence"]),
            },
            idempotency_key,
        )
        await request.state.connection.execute(
            """
            UPDATE knowledge_observation
            SET status = 'promoted', updated_at = now()
            WHERE id = %s
            """,
            (observation_id,),
        )
        return alias


@router.post("/validate")
async def validate(payload: ValidatePayload, request: Request) -> dict[str, Any]:
    workspace = await repository.workspace(request.state.connection)
    snapshot = {}
    if workspace["published_version_id"]:
        result = await request.state.connection.execute(
            "SELECT snapshot FROM knowledge_version WHERE id = %s",
            (workspace["published_version_id"],),
        )
        snapshot = (await result.fetchone())["snapshot"]
    resolver = KnowledgeResolver(snapshot)
    normalized, corrections = resolver.normalize_identity(payload.identity)
    return {
        "valid": not resolver.validate_identity(normalized),
        "normalized": normalized,
        "corrections": corrections,
        "issues": [asdict(issue) for issue in resolver.validate_identity(normalized)],
        "query_plan": [asdict(stage) for stage in resolver.build_query_plan(normalized)],
        "knowledge_version": resolver.version,
    }


@router.get("/versions/{version_id}/impact")
async def impact(version_id: UUID, request: Request) -> list[dict[str, Any]]:
    result = await request.state.connection.execute(
        """
        SELECT object_type, object_id, reason, revaluation_status, created_at
        FROM knowledge_impact WHERE version_id = %s
        ORDER BY object_type, created_at DESC
        """,
        (version_id,),
    )
    return [dict(row) for row in await result.fetchall()]


@router.post("/versions/{version_id}/revalue")
async def revalue(
    version_id: UUID,
    request: Request,
    x_knowledge_actor: UUID | None = Header(default=None),
) -> dict[str, int]:
    async with request.state.connection.transaction():
        workspace, actor_id = await _actor(request, {"publisher"}, x_knowledge_actor)
        result = await request.state.connection.execute(
            """
            INSERT INTO valuation_job (collection_item_id, status)
            SELECT ki.object_id, 'queued'
            FROM knowledge_impact ki
            WHERE ki.version_id = %s AND ki.object_type = 'collection_item'
            ON CONFLICT (collection_item_id)
                WHERE status IN ('queued', 'identifying', 'searching', 'calculating')
            DO UPDATE SET updated_at = now()
            RETURNING collection_item_id
            """,
            (version_id,),
        )
        queued = len(await result.fetchall())
        await request.state.connection.execute(
            """
            UPDATE knowledge_impact SET revaluation_status = 'queued'
            WHERE version_id = %s AND object_type = 'collection_item'
            """,
            (version_id,),
        )
        await repository.audit(
            request.state.connection,
            workspace["id"],
            actor_id,
            "bulk_revalue",
            "version",
            version_id,
            None,
            {"queued": queued},
            None,
        )
        return {"queued": queued}


@router.get("/official-checklists/progress")
async def official_checklist_progress() -> dict[str, Any]:
    import json
    from card_platform.official_checklists import STATUS_PATH
    if not STATUS_PATH.exists():
        return {"engineering_percent": 0, "known_products_percent": 0,
                "summary": {"total": 0, "counts": {}, "discovery_complete": False,
                            "success_percent": None, "date_unknown": 0},
                "source_blockers": [], "products": []}
    return json.loads(STATUS_PATH.read_text())


# Keep read-only catalogue access under the existing knowledge permission boundary.
router.include_router(browser_router)

# Atlas actions use the authenticated session identity; legacy actor headers do not apply.
router.include_router(atlas_router)
