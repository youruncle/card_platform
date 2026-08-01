from datetime import datetime, timezone
from collections import Counter
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.codex_source_labeler import (
    NON_BASKETBALL_PATTERN,
    build_source_assisted_truth,
    label_against_truth,
)
from card_platform.ingestion.cardhobby import LOT_PATTERN
from card_platform.market_data.models import EvidenceRecord, MarketIdentity
from card_platform.market_data.repository import MarketDataRepository
from card_platform.recognition_benchmark import (
    CardHobbyBenchmarkCollector,
    IDENTITY_FIELDS,
    _known_truth_value,
    _normalized_identity_value,
    audit_prediction_against_source_title,
    calculate_benchmark_metrics,
    calculate_feedback_report,
    calculate_version_metrics,
    selected_candidate_identity,
    truth_is_scorable,
)
from card_platform.recognition_benchmark_predictor import RECOGNITION_PIPELINE_VERSION
router = APIRouter(prefix="/v1/recognition-benchmarks", tags=["recognition-benchmarks"])
market_data_repository = MarketDataRepository()


async def get_connection(request: Request):
    async with request.app.state.database.connection() as connection:
        yield connection


class BenchmarkCollectRequest(BaseModel):
    name: str = Field(default="卡淘篮球单卡盲测", min_length=1, max_length=120)
    sample_size: int = Field(default=50, ge=10, le=300)
    page_count: int = Field(default=8, ge=2, le=30)
    seed: int = 20260729
    max_per_seller: int = Field(default=4, ge=1, le=20)


class BenchmarkLabelRequest(BaseModel):
    result: Literal[
        "top1_correct",
        "top2_correct",
        "top3_correct",
        "none_correct",
        "insufficient_image",
        "source_error",
    ]
    corrected_identity: dict | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AssistedLabelRequest(BaseModel):
    result: Literal[
        "top1_correct",
        "top2_correct",
        "top3_correct",
        "none_correct",
        "insufficient_image",
        "source_error",
    ]
    identity: dict | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    labeler: str = Field(default="codex", min_length=1, max_length=80)


class BenchmarkPredictRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    retry_errors: bool = False
    replay_all: bool = False


class BenchmarkTopUpRequest(BaseModel):
    additional_count: int = Field(default=5, ge=1, le=50)
    page_count: int = Field(default=20, ge=2, le=30)
    seed: int = 20260731
    max_per_seller: int = Field(default=2, ge=1, le=20)


@router.post("/{batch_id}/codex-auto-label")
async def codex_auto_label_batch(
    batch_id: UUID,
    ready_only: bool = False,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    """Create auditable machine truth after each blind prediction is frozen.

    ``ready_only`` supports campaign pipelining: it labels only newly predicted
    samples and never waits for the rest of the batch.  The default remains the
    final, whole-batch pass that calculates and stores batch metrics.
    """
    result = await connection.execute(
        """
        SELECT id, source_item_id, prediction, sealed_reference,
               assisted_label_result
        FROM recognition_benchmark_sample
        WHERE batch_id = %s
        ORDER BY created_at, id
        """,
        (batch_id,),
    )
    rows = list(await result.fetchall())
    if not rows:
        raise HTTPException(status_code=404, detail="benchmark not found or empty")
    if not ready_only and any(row["prediction"] is None for row in rows):
        raise HTTPException(
            status_code=409,
            detail="all samples must finish blind prediction before Codex auto-labeling",
        )
    target_rows = [
        row
        for row in rows
        if row["prediction"] is not None
        and (not ready_only or row["assisted_label_result"] is None)
    ]

    label_counts: Counter[str] = Counter()
    field_errors: Counter[str] = Counter()
    confidence_total = 0.0
    for row in target_rows:
        title = str((row["sealed_reference"] or {}).get("title") or "")
        if LOT_PATTERN.search(title) or NON_BASKETBALL_PATTERN.search(title):
            label = "source_error"
            truth = None
            evidence = [
                "卡淘标题显示非篮球、套卡、多卡或未拆产品，不适合作为篮球单卡真值",
                f"卡淘标题：{title[:240]}",
            ]
            confidence = 0.99
        else:
            truth, evidence, confidence = build_source_assisted_truth(
                row["prediction"], title
            )
            label = (
                label_against_truth(row["prediction"], truth)
                if truth_is_scorable(truth)
                else "insufficient_image"
            )
        top1 = (((row["prediction"] or {}).get("candidates") or [{}])[0]).get(
            "identity"
        ) or {}
        if truth:
            for field in IDENTITY_FIELDS:
                if not _known_truth_value(truth.get(field)):
                    continue
                if _normalized_identity_value(
                    top1.get(field), field
                ) != _normalized_identity_value(truth.get(field), field):
                    field_errors[field] += 1
        await connection.execute(
            """
            UPDATE recognition_benchmark_sample
            SET assisted_label_result = %s,
                assisted_identity = %s,
                assisted_confidence = %s,
                assisted_evidence = %s,
                assisted_labeler = 'codex-cardhobby-image-text-v1',
                assisted_review_status = 'pending',
                assisted_at = clock_timestamp()
            WHERE id = %s
            """,
            (
                label,
                Jsonb(truth) if truth else None,
                confidence,
                Jsonb(evidence),
                row["id"],
            ),
        )
        label_counts[label] += 1
        confidence_total += confidence

    scored = sum(
        label_counts[key]
        for key in ("top1_correct", "top2_correct", "top3_correct", "none_correct")
    )
    top1 = label_counts["top1_correct"]
    top3 = top1 + label_counts["top2_correct"] + label_counts["top3_correct"]
    report = {
        "batch_id": str(batch_id),
        "sample_count": len(target_rows),
        "newly_labeled_count": len(target_rows),
        "label_counts": dict(label_counts),
        "scored_count": scored,
        "provisional_top1_accuracy": round(top1 / scored, 4) if scored else None,
        "provisional_top3_recall": round(top3 / scored, 4) if scored else None,
        "mean_assisted_confidence": (
            round(confidence_total / len(target_rows), 4) if target_rows else None
        ),
        "top_field_errors": [
            {"field": field, "errors": count}
            for field, count in field_errors.most_common()
        ],
        "labeler": "codex-cardhobby-image-text-v1",
        "truth_status": "machine_assisted_training_only",
        "incremental": ready_only,
    }
    if ready_only:
        return report
    await connection.execute(
        """
        UPDATE recognition_benchmark_batch
        SET metrics = metrics || %s, status = 'labeling', updated_at = now()
        WHERE id = %s
        """,
        (Jsonb({"codex_auto_label_report": report}), batch_id),
    )
    return report


def validate_rank_label(prediction: dict | None, label_result: str) -> None:
    required_rank = {
        "top1_correct": 1,
        "top2_correct": 2,
        "top3_correct": 3,
    }.get(label_result)
    if required_rank is None:
        return
    candidates = (prediction or {}).get("candidates") or []
    if len(candidates) < required_rank:
        raise ValueError(f"预测结果不存在 Top-{required_rank} 候选")


def validate_label_correction(
    label_result: str,
    corrected_identity: dict | None,
) -> None:
    if corrected_identity is None:
        if label_result == "none_correct":
            raise ValueError(
                "现有候选都不对时必须补充完整的正确身份；"
                "若来源无法确认，请选择“来源答案可疑”"
            )
        return
    if not truth_is_scorable(corrected_identity):
        raise ValueError(
            "人工修正的正确身份不足以评分：必须包含球员、产品，"
            "并至少包含赛季、卡号、特卡、平行、总限编或评级公司中的两项"
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def collect_benchmark(
    payload: BenchmarkCollectRequest,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    batch_id = await CardHobbyBenchmarkCollector(get_settings()).collect(
        connection,
        name=payload.name,
        sample_size=payload.sample_size,
        page_count=payload.page_count,
        seed=payload.seed,
        max_per_seller=payload.max_per_seller,
    )
    return {"id": batch_id}


@router.get("")
async def list_benchmarks(
    connection: AsyncConnection = Depends(get_connection),
) -> list[dict]:
    rows = await connection.execute(
        """
        SELECT b.*,
               count(s.id) AS sample_count,
               count(s.id) FILTER (WHERE s.label_status = 'labeled') AS labeled_count
        FROM recognition_benchmark_batch b
        LEFT JOIN recognition_benchmark_sample s ON s.batch_id = b.id
        GROUP BY b.id
        ORDER BY b.created_at DESC
        """
    )
    return list(await rows.fetchall())


@router.post("/{batch_id}/top-up")
async def top_up_benchmark(
    batch_id: UUID,
    payload: BenchmarkTopUpRequest,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    try:
        added = await CardHobbyBenchmarkCollector(get_settings()).top_up(
            connection,
            batch_id=batch_id,
            additional_count=payload.additional_count,
            page_count=payload.page_count,
            seed=payload.seed,
            max_per_seller=payload.max_per_seller,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"batch_id": batch_id, "added_count": added}


@router.get("/{batch_id}/samples")
async def list_samples(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> list[dict]:
    rows = await connection.execute(
        """
        SELECT id, batch_id, source_code, source_item_id, image_urls,
               prediction, prediction_model, prediction_error, predicted_at,
               prediction_pipeline_version,
               label_status, label_result, corrected_identity, label_notes,
               label_pipeline_version,
               assisted_label_result, assisted_identity, assisted_confidence,
               assisted_evidence, assisted_labeler, assisted_review_status,
               assisted_at,
               CASE WHEN prediction IS NOT NULL THEN source_url ELSE NULL END AS source_url,
               CASE WHEN prediction IS NOT NULL THEN final_price ELSE NULL END AS final_price,
               CASE WHEN prediction IS NOT NULL THEN currency ELSE NULL END AS currency,
               CASE WHEN prediction IS NOT NULL THEN sealed_reference ELSE NULL END
                   AS revealed_reference,
               created_at
        FROM recognition_benchmark_sample
        WHERE batch_id = %s
        ORDER BY created_at, id
        """,
        (batch_id,),
    )
    samples = list(await rows.fetchall())
    for sample in samples:
        reference = sample.get("revealed_reference") or {}
        sample["source_audit"] = (
            audit_prediction_against_source_title(
                sample.get("prediction"), reference.get("title")
            )
            if sample.get("prediction")
            else None
        )
    return samples


@router.post("/{batch_id}/samples/{sample_id}/assisted-label")
async def save_assisted_label(
    batch_id: UUID,
    sample_id: UUID,
    payload: AssistedLabelRequest,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    sample_result = await connection.execute(
        """
        SELECT prediction
        FROM recognition_benchmark_sample
        WHERE id = %s AND batch_id = %s
        """,
        (sample_id, batch_id),
    )
    sample = await sample_result.fetchone()
    if sample is None:
        raise HTTPException(status_code=404, detail="benchmark sample not found")
    try:
        validate_rank_label(sample["prediction"], payload.result)
        validate_label_correction(payload.result, payload.identity)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    result = await connection.execute(
        """
        UPDATE recognition_benchmark_sample
        SET assisted_label_result = %s,
            assisted_identity = %s,
            assisted_confidence = %s,
            assisted_evidence = %s,
            assisted_labeler = %s,
            assisted_review_status = 'pending',
            assisted_at = now()
        WHERE id = %s AND batch_id = %s
        RETURNING id
        """,
        (
            payload.result,
            Jsonb(payload.identity) if payload.identity else None,
            payload.confidence,
            Jsonb(payload.evidence),
            payload.labeler,
            sample_id,
            batch_id,
        ),
    )
    await result.fetchone()
    return {"id": sample_id, "review_status": "pending"}


@router.post("/{batch_id}/samples/{sample_id}/assisted-label/accept")
async def accept_assisted_label(
    batch_id: UUID,
    sample_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    sample_result = await connection.execute(
        """
        SELECT prediction, assisted_label_result, assisted_identity
        FROM recognition_benchmark_sample
        WHERE id = %s AND batch_id = %s
        FOR UPDATE
        """,
        (sample_id, batch_id),
    )
    sample = await sample_result.fetchone()
    if sample is None:
        raise HTTPException(status_code=404, detail="benchmark sample not found")
    label_result = sample["assisted_label_result"]
    if not label_result:
        raise HTTPException(status_code=409, detail="assisted label not found")
    try:
        validate_rank_label(sample["prediction"], label_result)
        validate_label_correction(label_result, sample["assisted_identity"])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    selected_identity = sample["assisted_identity"]
    if selected_identity is None and label_result in {
        "top1_correct",
        "top2_correct",
        "top3_correct",
    }:
        selected_identity = selected_candidate_identity(
            {"prediction": sample["prediction"], "label_result": label_result}
        )
    await connection.execute(
        """
        UPDATE recognition_benchmark_sample
        SET label_status = 'labeled',
            label_result = assisted_label_result,
            corrected_identity = %s,
            label_notes = '人工确认 Codex 辅助标注',
            label_pipeline_version = prediction_pipeline_version,
            labeled_at = now(),
            assisted_review_status = 'accepted'
        WHERE id = %s AND batch_id = %s
        """,
        (
            Jsonb(selected_identity) if selected_identity else None,
            sample_id,
            batch_id,
        ),
    )
    metrics = await refresh_metrics(connection, batch_id)
    return {"id": sample_id, "metrics": metrics}


@router.get("/{batch_id}/sampling-audit")
async def get_sampling_audit(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    batch_result = await connection.execute(
        """
        SELECT sampling_config
        FROM recognition_benchmark_batch
        WHERE id = %s
        """,
        (batch_id,),
    )
    batch = await batch_result.fetchone()
    if batch is None:
        raise HTTPException(status_code=404, detail="benchmark not found")
    result = await connection.execute(
        """
        WITH seller_counts AS (
            SELECT source_snapshot -> 'search' ->> 'SellMemberID' AS seller_id,
                   count(*) AS sample_count
            FROM recognition_benchmark_sample
            WHERE batch_id = %s
            GROUP BY 1
        )
        SELECT count(*) AS sample_count,
               count(DISTINCT source_item_id) AS unique_item_count,
               count(DISTINCT source_snapshot -> 'search' ->> 'Title')
                   AS unique_title_count,
               count(DISTINCT source_snapshot -> 'search' ->> 'SellMemberID')
                   AS unique_seller_count,
               min(final_price) AS min_final_price,
               max(final_price) AS max_final_price,
               count(*) FILTER (WHERE final_price <= 20) AS at_or_below_20_count,
               count(*) FILTER (
                   WHERE source_snapshot -> 'detail' ->> 'ThirdCategoryName'
                         <> 'Trading Card Singles'
               ) AS non_single_card_count,
               COALESCE((SELECT max(sample_count) FROM seller_counts), 0)
                   AS max_samples_per_seller
        FROM recognition_benchmark_sample
        WHERE batch_id = %s
        """,
        (batch_id, batch_id),
    )
    audit = dict(await result.fetchone())
    config = batch["sampling_config"] or {}
    expected_count = int(config.get("sample_size") or 0)
    seller_limit = int(config.get("max_per_seller") or 0)
    audit["sampling_config"] = config
    audit["conforms"] = bool(
        audit["sample_count"] == expected_count
        and audit["unique_item_count"] == audit["sample_count"]
        and audit["unique_title_count"] == audit["sample_count"]
        and audit["at_or_below_20_count"] == 0
        and audit["non_single_card_count"] == 0
        and (
            seller_limit == 0
            or audit["max_samples_per_seller"] <= seller_limit
        )
    )
    return audit


@router.post("/{batch_id}/predict", status_code=status.HTTP_202_ACCEPTED)
async def predict_samples(
    batch_id: UUID,
    payload: BenchmarkPredictRequest,
    request: Request,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    runtime = getattr(request.app.state, "knowledge_runtime", None)
    version = getattr(runtime, "version_id", None)
    try:
        result = await connection.execute(
            """
            INSERT INTO recognition_prediction_job (
                batch_id, requested_limit, retry_errors, knowledge_version,
                pipeline_version, replay_all
            )
            SELECT id, %s, %s, %s, %s, %s
            FROM recognition_benchmark_batch
            WHERE id = %s
            RETURNING *
            """,
            (
                payload.limit,
                payload.retry_errors,
                version,
                RECOGNITION_PIPELINE_VERSION,
                payload.replay_all,
                batch_id,
            ),
        )
        job = await result.fetchone()
    except Exception as error:
        if "recognition_prediction_job_one_active_per_batch" in str(error):
            raise HTTPException(
                status_code=409, detail="该批次已有预测任务正在运行"
            ) from error
        raise
    if job is None:
        raise HTTPException(status_code=404, detail="benchmark not found")
    # The client polls this job immediately. Make it visible before returning so
    # the first GET cannot race the request-scoped transaction and falsely 404.
    await connection.commit()
    return dict(job)


@router.get("/{batch_id}/samples/{sample_id}/predictions")
async def prediction_history(
    batch_id: UUID,
    sample_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> list[dict]:
    result = await connection.execute(
        """
        SELECT p.*
        FROM recognition_benchmark_prediction p
        JOIN recognition_benchmark_sample s ON s.id = p.sample_id
        WHERE p.sample_id = %s AND s.batch_id = %s
        ORDER BY p.predicted_at DESC, p.id DESC
        """,
        (sample_id, batch_id),
    )
    return list(await result.fetchall())


@router.get("/{batch_id}/prediction-jobs/{job_id}")
async def get_prediction_job(
    batch_id: UUID,
    job_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    result = await connection.execute(
        """
        SELECT *
        FROM recognition_prediction_job
        WHERE id = %s AND batch_id = %s
        """,
        (job_id, batch_id),
    )
    job = await result.fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="prediction job not found")
    return dict(job)


@router.post("/{batch_id}/samples/{sample_id}/label")
async def label_sample(
    batch_id: UUID,
    sample_id: UUID,
    payload: BenchmarkLabelRequest,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    sample_result = await connection.execute(
        """
        SELECT prediction
        FROM recognition_benchmark_sample
        WHERE id = %s AND batch_id = %s
        """,
        (sample_id, batch_id),
    )
    sample = await sample_result.fetchone()
    if sample is None:
        raise HTTPException(status_code=404, detail="benchmark sample not found")
    try:
        validate_rank_label(sample["prediction"], payload.result)
        validate_label_correction(payload.result, payload.corrected_identity)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    selected_identity = payload.corrected_identity
    if selected_identity is None and payload.result in {
        "top1_correct",
        "top2_correct",
        "top3_correct",
    }:
        selected_identity = selected_candidate_identity(
            {
                "prediction": sample["prediction"],
                "label_result": payload.result,
            }
        )
    row = await connection.execute(
        """
        UPDATE recognition_benchmark_sample
        SET label_status = 'labeled', label_result = %s,
            corrected_identity = %s, label_notes = %s,
            label_pipeline_version = prediction_pipeline_version,
            assisted_review_status = CASE
                WHEN assisted_label_result IS NULL THEN assisted_review_status
                ELSE 'overridden'
            END,
            labeled_at = now()
        WHERE id = %s AND batch_id = %s
        RETURNING id
        """,
        (
            payload.result,
            Jsonb(selected_identity) if selected_identity else None,
            payload.notes,
            sample_id,
            batch_id,
        ),
    )
    await row.fetchone()
    metrics = await refresh_metrics(connection, batch_id)
    return {"id": sample_id, "metrics": metrics}


@router.get("/{batch_id}/metrics")
async def get_metrics(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    return await refresh_metrics(connection, batch_id)


@router.get("/{batch_id}/feedback-report")
async def get_feedback_report(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    result = await connection.execute(
        """
        SELECT prediction, label_result, corrected_identity
        FROM recognition_benchmark_sample
        WHERE batch_id = %s AND label_status = 'labeled'
        """,
        (batch_id,),
    )
    return calculate_feedback_report(list(await result.fetchall()))


@router.get("/{batch_id}/version-metrics")
async def get_version_metrics(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    result = await connection.execute(
        """
        SELECT DISTINCT ON (
                   p.sample_id, p.pipeline_version, p.knowledge_version
               )
               p.sample_id, p.pipeline_version, p.prediction,
               p.knowledge_version, kv.version_number AS knowledge_version_number,
               p.model, p.prediction_error,
               s.corrected_identity, s.label_result
        FROM recognition_benchmark_prediction p
        JOIN recognition_benchmark_sample s ON s.id = p.sample_id
        LEFT JOIN knowledge_version kv ON kv.id = p.knowledge_version
        WHERE s.batch_id = %s
        ORDER BY p.sample_id, p.pipeline_version, p.knowledge_version,
                 p.predicted_at DESC, p.id DESC
        """,
        (batch_id,),
    )
    return calculate_version_metrics(list(await result.fetchall()))


@router.post("/{batch_id}/promote-market-evidence")
async def promote_market_evidence(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    """Reuse human-confirmed benchmark sales as exact-identity valuation evidence."""
    result = await connection.execute(
        """
        SELECT s.id, s.source_item_id, s.source_url, s.occurred_at,
               s.final_price, s.currency, s.sealed_reference, s.source_snapshot,
               s.prediction, s.label_result, s.corrected_identity,
               e.attributes AS knowledge_identity
        FROM recognition_benchmark_sample s
        LEFT JOIN LATERAL (
            SELECT attributes
            FROM knowledge_entity
            WHERE entity_type = 'card'
              AND active
              AND review_status = 'approved'
              AND attributes->>'benchmark_sample_id' = s.id::text
            ORDER BY confidence DESC, updated_at DESC
            LIMIT 1
        ) e ON true
        WHERE s.batch_id = %s
          AND s.label_status = 'labeled'
          AND s.label_result NOT IN ('insufficient_image', 'source_error')
          AND s.final_price IS NOT NULL
          AND s.occurred_at IS NOT NULL
        """,
        (batch_id,),
    )
    rows = list(await result.fetchall())
    promoted = 0
    skipped = 0
    fingerprints: set[str] = set()
    for row in rows:
        identity = (
            row["corrected_identity"]
            or row["knowledge_identity"]
            or selected_candidate_identity(row)
        )
        if not identity or not truth_is_scorable(identity):
            skipped += 1
            continue
        market_identity = MarketIdentity(
            player_name=identity["player_name"],
            season_start=identity.get("season_start"),
            season_end=identity.get("season_end"),
            brand_name=identity.get("brand_name"),
            product_name=identity.get("product_name"),
            set_name=identity.get("set_name"),
            card_number=identity.get("card_number"),
            insert_name=identity.get("insert_name"),
            parallel_name=identity.get("parallel_name"),
            print_run=identity.get("print_run"),
            serial_total=identity.get("serial_total"),
            is_rookie=bool(identity.get("is_rookie")),
            is_autograph=bool(identity.get("is_autograph")),
            is_patch=bool(identity.get("is_patch")),
            grade_company=identity.get("grade_company"),
            grade_value=identity.get("grade_value"),
        )
        fingerprint = await market_data_repository.ensure_identity(
            connection, market_identity
        )
        reference = row["sealed_reference"] or {}
        snapshot = row["source_snapshot"] or {}
        raw_title = (
            reference.get("title")
            or (snapshot.get("search") or {}).get("Title")
            or row["source_item_id"]
        )
        evidence = EvidenceRecord(
            provider_code="cardhobby",
            external_sale_id=row["source_item_id"],
            raw_title=raw_title,
            sale_price=Decimal(row["final_price"]),
            shipping_price=Decimal("0"),
            currency=str(row["currency"]).strip(),
            occurred_at=row["occurred_at"],
            source_url=row["source_url"],
            evidence_level="human_verified_exact_identity",
            grade_company=identity.get("grade_company"),
            grade_value=identity.get("grade_value"),
            match_confidence=Decimal("1"),
            identity_classification={
                "classification": "exact_identity",
                "benchmark_batch_id": str(batch_id),
                "benchmark_sample_id": str(row["id"]),
                "label_result": row["label_result"],
                "identity_source": (
                    "human_correction"
                    if row["corrected_identity"]
                    else "human_verified_knowledge"
                    if row["knowledge_identity"]
                    else "human_selected_candidate"
                ),
            },
            raw_payload={
                "source_snapshot": snapshot,
                "sealed_reference": reference,
            },
            evidence_version=1,
            fetched_at=datetime.now(timezone.utc),
        )
        await market_data_repository.reconcile_external_sale_identity(
            connection,
            provider_code="cardhobby",
            external_sale_id=row["source_item_id"],
            authoritative_fingerprint=fingerprint,
        )
        await market_data_repository.store_evidence(
            connection, fingerprint, [evidence]
        )
        fingerprints.add(fingerprint)
        promoted += 1
    if fingerprints:
        await connection.execute(
            """
            UPDATE market_identity_cache c
            SET evidence_count = (
                    SELECT count(*) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                freshness = 'cached',
                data_as_of = (
                    SELECT max(occurred_at) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                updated_at = now()
            WHERE identity_fingerprint = ANY(%s)
            """,
            (list(fingerprints),),
        )
    return {
        "eligible_count": len(rows),
        "promoted_count": promoted,
        "skipped_count": skipped,
        "identity_count": len(fingerprints),
    }


@router.post("/{batch_id}/promote-assisted-market-evidence")
async def promote_assisted_market_evidence(
    batch_id: UUID,
    min_confidence: float = 0.95,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    """Store high-confidence Codex labels as lower-weight, replaceable evidence."""
    result = await connection.execute(
        """
        SELECT id, source_item_id, source_url, occurred_at, final_price, currency,
               sealed_reference, source_snapshot, prediction,
               assisted_label_result, assisted_identity, assisted_confidence,
               assisted_evidence, assisted_labeler
        FROM recognition_benchmark_sample
        WHERE batch_id = %s
          AND assisted_label_result NOT IN ('insufficient_image', 'source_error')
          AND assisted_confidence >= %s
          AND final_price IS NOT NULL
          AND occurred_at IS NOT NULL
        """,
        (batch_id, min_confidence),
    )
    rows = list(await result.fetchall())
    promoted = 0
    skipped = 0
    fingerprints: set[str] = set()
    for row in rows:
        identity = row["assisted_identity"]
        if identity is None and row["assisted_label_result"] in {
            "top1_correct",
            "top2_correct",
            "top3_correct",
        }:
            identity = selected_candidate_identity(
                {
                    "prediction": row["prediction"],
                    "label_result": row["assisted_label_result"],
                }
            )
        if not identity or not truth_is_scorable(identity):
            skipped += 1
            continue
        market_identity = MarketIdentity(
            player_name=identity["player_name"],
            season_start=identity.get("season_start"),
            season_end=identity.get("season_end"),
            brand_name=identity.get("brand_name"),
            product_name=identity.get("product_name"),
            set_name=identity.get("set_name"),
            card_number=identity.get("card_number"),
            insert_name=identity.get("insert_name"),
            parallel_name=identity.get("parallel_name"),
            print_run=identity.get("print_run"),
            serial_total=identity.get("serial_total"),
            is_rookie=bool(identity.get("is_rookie")),
            is_autograph=bool(identity.get("is_autograph")),
            is_patch=bool(identity.get("is_patch")),
            grade_company=identity.get("grade_company"),
            grade_value=identity.get("grade_value"),
        )
        fingerprint = await market_data_repository.ensure_identity(
            connection, market_identity
        )
        human_result = await connection.execute(
            """
            SELECT 1
            FROM market_evidence
            WHERE provider_code = 'cardhobby'
              AND external_sale_id = %s
              AND evidence_level = 'human_verified_exact_identity'
            LIMIT 1
            """,
            (row["source_item_id"],),
        )
        if await human_result.fetchone():
            skipped += 1
            continue
        await market_data_repository.reconcile_external_sale_identity(
            connection,
            provider_code="cardhobby",
            external_sale_id=row["source_item_id"],
            authoritative_fingerprint=fingerprint,
            replaceable_evidence_levels=["codex_assisted_exact_identity"],
        )
        snapshot = row["source_snapshot"] or {}
        reference = row["sealed_reference"] or {}
        raw_title = (
            reference.get("title")
            or (snapshot.get("search") or {}).get("Title")
            or row["source_item_id"]
        )
        await market_data_repository.store_evidence(
            connection,
            fingerprint,
            [
                EvidenceRecord(
                    provider_code="cardhobby",
                    external_sale_id=row["source_item_id"],
                    raw_title=raw_title,
                    sale_price=Decimal(row["final_price"]),
                    shipping_price=Decimal("0"),
                    currency=str(row["currency"]).strip(),
                    occurred_at=row["occurred_at"],
                    source_url=row["source_url"],
                    evidence_level="codex_assisted_exact_identity",
                    grade_company=identity.get("grade_company"),
                    grade_value=identity.get("grade_value"),
                    match_confidence=Decimal(str(row["assisted_confidence"])),
                    identity_classification={
                        "classification": "machine_assisted_exact_identity",
                        "benchmark_batch_id": str(batch_id),
                        "benchmark_sample_id": str(row["id"]),
                        "label_result": row["assisted_label_result"],
                        "labeler": row["assisted_labeler"],
                        "evidence": row["assisted_evidence"] or [],
                        "replaceable_by_human_review": True,
                    },
                    raw_payload={
                        "source_snapshot": snapshot,
                        "sealed_reference": reference,
                    },
                    evidence_version=1,
                    fetched_at=datetime.now(timezone.utc),
                )
            ],
        )
        fingerprints.add(fingerprint)
        promoted += 1
    if fingerprints:
        await connection.execute(
            """
            UPDATE market_identity_cache c
            SET evidence_count = (
                    SELECT count(*) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                freshness = 'cached',
                data_as_of = (
                    SELECT max(occurred_at) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                updated_at = now()
            WHERE identity_fingerprint = ANY(%s)
            """,
            (list(fingerprints),),
        )
    return {
        "eligible_count": len(rows),
        "promoted_count": promoted,
        "skipped_count": skipped,
        "identity_count": len(fingerprints),
        "evidence_level": "codex_assisted_exact_identity",
        "min_confidence": min_confidence,
    }


@router.post(
    "/{batch_id}/knowledge-proposals",
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_proposals(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> dict:
    result = await connection.execute(
        """
        SELECT id, prediction, label_result, corrected_identity,
               source_url, sealed_reference,
               prediction_model, prediction_pipeline_version,
               prediction_knowledge_version
        FROM recognition_benchmark_sample
        WHERE batch_id = %s
          AND label_result IN ('top2_correct', 'top3_correct', 'none_correct')
        """,
        (batch_id,),
    )
    rows = list(await result.fetchall())
    created = 0
    for row in rows:
        identity = row["corrected_identity"] or selected_candidate_identity(row)
        if not identity:
            continue
        required = ("player_name", "season_start", "product_name")
        missing = [field for field in required if not identity.get(field)]
        if not any(identity.get(field) for field in ("card_number", "insert_name")):
            missing.append("card_number_or_insert_name")
        proposal = await connection.execute(
            """
            INSERT INTO recognition_knowledge_proposal (
                batch_id, sample_id, proposed_identity, evidence,
                missing_fields, status
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (sample_id) DO UPDATE
            SET proposed_identity = EXCLUDED.proposed_identity,
                evidence = EXCLUDED.evidence,
                missing_fields = EXCLUDED.missing_fields,
                status = EXCLUDED.status,
                updated_at = now()
            RETURNING id
            """,
            (
                batch_id,
                row["id"],
                Jsonb(identity),
                Jsonb(
                    {
                        "source_url": row["source_url"],
                        "revealed_reference": row["sealed_reference"],
                        "human_label_result": row["label_result"],
                        "identity_source": (
                            "manual_correction"
                            if row["corrected_identity"]
                            else "selected_prediction_candidate"
                        ),
                        "prediction_model": row["prediction_model"],
                        "pipeline_version": row["prediction_pipeline_version"],
                        "knowledge_version": (
                            str(row["prediction_knowledge_version"])
                            if row["prediction_knowledge_version"]
                            else None
                        ),
                    }
                ),
                Jsonb(missing),
                "needs_review" if missing else "pending",
            ),
        )
        if await proposal.fetchone():
            created += 1
    return {"proposal_count": created}


@router.get("/{batch_id}/knowledge-proposals")
async def list_knowledge_proposals(
    batch_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> list[dict]:
    result = await connection.execute(
        """
        SELECT *
        FROM recognition_knowledge_proposal
        WHERE batch_id = %s
        ORDER BY created_at
        """,
        (batch_id,),
    )
    return list(await result.fetchall())


async def refresh_metrics(connection: AsyncConnection, batch_id: UUID) -> dict:
    result = await connection.execute(
        """
        SELECT label_result
        FROM recognition_benchmark_sample
        WHERE batch_id = %s
        """,
        (batch_id,),
    )
    rows = list(await result.fetchall())
    if not rows:
        batch = await connection.execute(
            "SELECT id FROM recognition_benchmark_batch WHERE id = %s",
            (batch_id,),
        )
        if await batch.fetchone() is None:
            raise HTTPException(status_code=404, detail="benchmark not found")
    metrics = calculate_benchmark_metrics(rows)
    await connection.execute(
        """
        UPDATE recognition_benchmark_batch
        SET metrics = %s, updated_at = now(),
            status = CASE
                WHEN %s > 0 AND %s = %s THEN 'completed'
                WHEN %s > 0 THEN 'labeling'
                ELSE status
            END,
            completed_at = CASE
                WHEN %s > 0 AND %s = %s THEN now()
                ELSE completed_at
            END
        WHERE id = %s
        """,
        (
            Jsonb(metrics),
            metrics["sample_count"],
            metrics["labeled_count"],
            metrics["sample_count"],
            metrics["labeled_count"],
            metrics["sample_count"],
            metrics["labeled_count"],
            metrics["sample_count"],
            batch_id,
        ),
    )
    return metrics
