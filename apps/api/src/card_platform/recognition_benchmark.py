import asyncio
import json
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.config import Settings


CARDHOBBY_ORIGIN = "https://cardhobby.com.cn"
CARDHOBBY_SEARCH_URL = f"{CARDHOBBY_ORIGIN}/NewCommodity/SearchCommodity"
CARDHOBBY_DETAIL_URL = f"{CARDHOBBY_ORIGIN}/NewCommodity/GetNewCommodityById"
CARDHOBBY_SINGLE_CARD_CATEGORY = "10106fda-c5d8-4121-ab21-0428534eaf42"
CARDHOBBY_HEADERS = {
    "Accept": "application/json",
    "Referer": f"{CARDHOBBY_ORIGIN}/market/search",
    "User-Agent": "CardPlatform-Benchmark/1.0",
}
EXCLUDED_TITLE = re.compile(
    r"(?:\blot\b|打包|套卡|整套|一图多张|多卡|共\s*\d+\s*张|卡盒|卡包|未拆封|"
    r"\bbox\b|\bpack\b|\bcase\b|\bfootball\b|\bsoccer\b|\bfifa\b|"
    r"英超|欧冠|曼城|皇家马德里|巴塞罗那)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class CardHobbyBenchmarkCandidate:
    item_id: str
    title: str
    image_url: str
    final_price: Decimal
    occurred_at: datetime | None
    seller_id: str | None
    raw: dict[str, Any]


def parse_cardhobby_candidate(item: dict[str, Any]) -> CardHobbyBenchmarkCandidate | None:
    title = str(item.get("Title") or "").strip()
    image_url = str(item.get("TitImg") or "").strip()
    final_price = item.get("LowestPrice")
    if (
        not item.get("ID")
        or not title
        or EXCLUDED_TITLE.search(title)
        or not is_cardhobby_image_url(image_url)
    ):
        return None
    try:
        price = Decimal(str(final_price))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if price <= 20:
        return None
    occurred_at = None
    if item.get("EffectiveDate"):
        try:
            occurred_at = datetime.fromisoformat(str(item["EffectiveDate"]))
        except ValueError:
            pass
    return CardHobbyBenchmarkCandidate(
        item_id=str(item["ID"]),
        title=title,
        image_url=image_url,
        final_price=price,
        occurred_at=occurred_at,
        seller_id=str(item["SellMemberID"]) if item.get("SellMemberID") else None,
        raw=item,
    )


def is_cardhobby_image_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname == "images.cardhobby.com"


def build_cardhobby_search_params(page_index: int, page_size: int = 60) -> dict[str, str | int]:
    filters = [
        {"Key": "Status", "Value": -2},
        {"Key": "Sports", "Value": "Basketball"},
        {"Key": "ThirdCategoryId", "Value": CARDHOBBY_SINGLE_CARD_CATEGORY},
        {"Key": "MinPrice", "Value": "20"},
        {"Key": "MaxPrice", "Value": ""},
        {"Key": "IsValut", "Value": ""},
    ]
    return {
        "userId": "",
        "pageIndex": page_index,
        "pageSize": page_size,
        "searchKey": "",
        "searchJson": json.dumps(filters, ensure_ascii=False, separators=(",", ":")),
        "sort": "EffectiveTimeStamp",
        "sortType": "DESC",
    }


def choose_candidates(
    candidates: list[CardHobbyBenchmarkCandidate],
    *,
    sample_size: int,
    max_per_seller: int,
    seed: int,
) -> list[CardHobbyBenchmarkCandidate]:
    shuffled = list(candidates)
    random.Random(seed).shuffle(shuffled)
    selected: list[CardHobbyBenchmarkCandidate] = []
    seller_counts: dict[str, int] = {}
    seen_titles: set[str] = set()
    for candidate in shuffled:
        normalized_title = re.sub(r"\W+", " ", candidate.title.casefold()).strip()
        if normalized_title in seen_titles:
            continue
        seller_key = candidate.seller_id or "unknown"
        if seller_counts.get(seller_key, 0) >= max_per_seller:
            continue
        selected.append(candidate)
        seen_titles.add(normalized_title)
        seller_counts[seller_key] = seller_counts.get(seller_key, 0) + 1
        if len(selected) >= sample_size:
            break
    return selected


def calculate_benchmark_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [
        row for row in rows
        if row.get("label_result")
        in {"top1_correct", "top2_correct", "top3_correct", "none_correct"}
    ]
    top1 = sum(row["label_result"] == "top1_correct" for row in scored)
    top3 = sum(
        row["label_result"] in {"top1_correct", "top2_correct", "top3_correct"}
        for row in scored
    )
    denominator = len(scored)
    return {
        "sample_count": len(rows),
        "labeled_count": sum(bool(row.get("label_result")) for row in rows),
        "scored_count": denominator,
        "top1_correct": top1,
        "top3_correct": top3,
        "top1_accuracy": round(top1 / denominator, 4) if denominator else None,
        "top3_recall": round(top3 / denominator, 4) if denominator else None,
        "insufficient_image_count": sum(
            row.get("label_result") == "insufficient_image" for row in rows
        ),
        "source_error_count": sum(
            row.get("label_result") == "source_error" for row in rows
        ),
    }


IDENTITY_FIELDS = (
    "player_name",
    "season_start",
    "season_end",
    "brand_name",
    "product_name",
    "insert_name",
    "parallel_name",
    "card_number",
    "serial_number",
    "serial_total",
    "is_numbered",
    "grade_company",
    "grade_value",
    "is_rookie",
    "rookie_evidence",
    "is_autograph",
    "autograph_type",
    "has_inscription",
    "inscription_text",
    "is_memorabilia",
    "memorabilia_types",
    "memorabilia_usage",
    "memorabilia_player",
    "is_patch",
)

TITLE_PARALLEL_ALIASES = {
    "gold": ("gold", "金折", "金平行", "金闪"),
    "silver": ("silver", "银折", "银平行"),
    "red": ("red", "红折", "红平行"),
    "blue": ("blue", "蓝折", "蓝平行"),
    "green": ("green", "绿折", "绿平行"),
    "purple": ("purple", "紫折", "紫平行"),
    "pink": ("pink", "粉折", "粉平行", "粉hyper", "粉泡泡"),
    "orange": ("orange", "橙折", "橙平行"),
    "black": ("black", "黑折", "黑平行"),
}


def audit_prediction_against_source_title(
    prediction: dict | None, source_title: str | None
) -> dict[str, Any]:
    """Weak post-prediction conflict hints; never used as benchmark ground truth."""
    candidates = (prediction or {}).get("candidates") or []
    identity = (candidates[0].get("identity") or {}) if candidates else {}
    title = str(source_title or "")
    normalized = title.casefold()
    conflicts: list[dict[str, Any]] = []

    season_match = re.search(r"\b(19\d{2}|20\d{2})\s*[-–]\s*(\d{2,4})\b", title)
    if season_match:
        source_start = int(season_match.group(1))
        raw_end = season_match.group(2)
        source_end = (
            int(raw_end)
            if len(raw_end) == 4
            else (source_start // 100) * 100 + int(raw_end)
        )
        if source_end < source_start:
            source_end += 100
        predicted_start = identity.get("season_start")
        predicted_end = identity.get("season_end")
        if (
            predicted_start is not None
            and predicted_end is not None
            and (int(predicted_start), int(predicted_end))
            != (source_start, source_end)
        ):
            conflicts.append(
                {
                    "field": "season",
                    "prediction": f"{predicted_start}-{predicted_end}",
                    "source_hint": f"{source_start}-{source_end}",
                    "reason": "预测赛季与来源标题中的明确跨年赛季不同",
                }
            )

    title_totals = {
        int(number)
        for pair in re.findall(r"/\s*(\d+)(?!\d)|(?<!\d)(\d+)\s*编", title)
        for number in pair
        if number
    }
    predicted_total = identity.get("serial_total")
    if title_totals and predicted_total and int(predicted_total) not in title_totals:
        conflicts.append(
            {
                "field": "serial_total",
                "prediction": predicted_total,
                "source_hint": sorted(title_totals),
                "reason": "预测限编与来源标题中的明确分母不同",
            }
        )

    grade_match = re.search(r"\b(PSA|BGS|CGC|SGC)\s*(\d+(?:\.\d+)?)", title, re.I)
    if grade_match and identity.get("grade_company"):
        predicted_grade = (
            str(identity.get("grade_company")).upper(),
            str(identity.get("grade_value") or ""),
        )
        source_grade = (grade_match.group(1).upper(), grade_match.group(2))
        if predicted_grade != source_grade:
            conflicts.append(
                {
                    "field": "grade",
                    "prediction": " ".join(predicted_grade).strip(),
                    "source_hint": " ".join(source_grade),
                    "reason": "预测评级与来源标题中的明确评级不同",
                }
            )

    predicted_parallel = str(identity.get("parallel_name") or "").casefold()
    predicted_color = next(
        (
            color
            for color, aliases in TITLE_PARALLEL_ALIASES.items()
            if any(alias in predicted_parallel for alias in aliases)
        ),
        None,
    )
    title_colors = {
        color
        for color, aliases in TITLE_PARALLEL_ALIASES.items()
        if any(alias in normalized for alias in aliases)
    }
    if title_colors and predicted_color not in title_colors:
        conflicts.append(
            {
                "field": "parallel_name",
                "prediction": identity.get("parallel_name") or "未识别颜色平行",
                "source_hint": sorted(title_colors),
                "reason": (
                    "预测颜色平行与来源标题颜色关键词不同"
                    if predicted_color
                    else "预测未识别来源标题中的明确颜色平行"
                ),
            }
        )

    return {
        "status": "review" if conflicts else "no_explicit_conflict",
        "conflicts": conflicts,
        "disclaimer": "来源标题仅为弱提示，不作为真值或准确率评分依据",
    }


def _comparable_identity_value(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"[^a-z0-9]+", "", value.casefold())
    return value


def selected_candidate_identity(row: dict[str, Any]) -> dict[str, Any]:
    """Return the human-selected candidate identity for rank-based labels."""
    selected_rank = {
        "top1_correct": 1,
        "top2_correct": 2,
        "top3_correct": 3,
    }.get(row.get("label_result"))
    if selected_rank is None:
        return {}
    candidates = (row.get("prediction") or {}).get("candidates") or []
    for index, candidate in enumerate(candidates, start=1):
        candidate_rank = candidate.get("rank") or index
        if candidate_rank == selected_rank:
            return candidate.get("identity") or {}
    return {}


def _known_truth_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _normalized_identity_value(value: Any, field: str | None = None) -> Any:
    if isinstance(value, str):
        ascii_value = unicodedata.normalize("NFKD", value).encode(
            "ascii", "ignore"
        ).decode("ascii")
        if field == "product_name":
            packaging_tokens = {"panini", "topps", "nba", "basketball"}
            return "".join(
                token
                for token in re.findall(r"[a-z0-9]+", ascii_value.casefold())
                if token not in packaging_tokens
            )
        return _comparable_identity_value(ascii_value)
    if isinstance(value, (list, tuple, set)):
        return tuple(
            sorted(_normalized_identity_value(item, field) for item in value)
        )
    if isinstance(value, Decimal):
        return str(value.normalize())
    return value


def identity_matches_truth(
    candidate: dict[str, Any] | None,
    truth: dict[str, Any] | None,
) -> bool:
    """Match a candidate against every human-known structured identity field."""
    candidate = candidate or {}
    truth = truth or {}
    known_fields = [
        field
        for field in IDENTITY_FIELDS
        if _known_truth_value(truth.get(field))
    ]
    if not known_fields:
        return False
    return all(
        _normalized_identity_value(candidate.get(field), field)
        == _normalized_identity_value(truth.get(field), field)
        for field in known_fields
    )


def truth_is_scorable(truth: dict[str, Any] | None) -> bool:
    """Require enough anchors to score an exact-card prediction."""
    truth = truth or {}
    if not truth.get("player_name") or not truth.get("product_name"):
        return False
    anchors = sum(
        _known_truth_value(truth.get(field))
        for field in (
            "season_start",
            "card_number",
            "insert_name",
            "parallel_name",
            "serial_total",
            "grade_company",
        )
    )
    return anchors >= 2


def calculate_version_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare stored prediction versions against stable human truth."""
    minimum_acceptance_samples = 50
    target_top1 = 0.90
    target_top3 = 0.97
    versions: dict[tuple[str, str], dict[str, Any]] = {}
    truth_sample_ids = {
        str(row["sample_id"])
        for row in rows
        if row.get("corrected_identity")
    }
    unresolved_labeled_ids = {
        str(row["sample_id"])
        for row in rows
        if row.get("label_result") and not row.get("corrected_identity")
    }
    incomplete_truth_ids = {
        str(row["sample_id"])
        for row in rows
        if row.get("corrected_identity")
        and not truth_is_scorable(row.get("corrected_identity"))
    }
    for row in rows:
        version = str(row.get("pipeline_version") or "legacy-unknown")
        knowledge_version = str(row.get("knowledge_version") or "none")
        version_key = (version, knowledge_version)
        bucket = versions.setdefault(
            version_key,
            {
                "pipeline_version": version,
                "knowledge_version": (
                    knowledge_version if knowledge_version != "none" else None
                ),
                "knowledge_version_number": row.get("knowledge_version_number"),
                "model": row.get("model"),
                "history_sample_count": 0,
                "sample_count": 0,
                "prediction_error_count": 0,
                "_candidate_count_distribution": {},
                "top1_correct": 0,
                "top3_correct": 0,
                "_field_correct": {field: 0 for field in IDENTITY_FIELDS},
                "_field_total": {field: 0 for field in IDENTITY_FIELDS},
            },
        )
        prediction = row.get("prediction") or {}
        candidates = prediction.get("candidates") or []
        bucket["history_sample_count"] += 1
        candidate_count = min(len(candidates), 3)
        distribution = bucket["_candidate_count_distribution"]
        distribution[candidate_count] = distribution.get(candidate_count, 0) + 1
        if row.get("prediction_error") or not candidates:
            bucket["prediction_error_count"] += 1
        truth = row.get("corrected_identity") or {}
        if not truth_is_scorable(truth):
            continue
        bucket["sample_count"] += 1
        if candidates and identity_matches_truth(candidates[0].get("identity"), truth):
            bucket["top1_correct"] += 1
        if any(
            identity_matches_truth(candidate.get("identity"), truth)
            for candidate in candidates[:3]
        ):
            bucket["top3_correct"] += 1
        top1_identity = candidates[0].get("identity") if candidates else {}
        for field in IDENTITY_FIELDS:
            truth_value = truth.get(field)
            if not _known_truth_value(truth_value):
                continue
            bucket["_field_total"][field] += 1
            if _normalized_identity_value(top1_identity.get(field), field) == (
                _normalized_identity_value(truth_value, field)
            ):
                bucket["_field_correct"][field] += 1

    output_versions = []
    for version_key in sorted(versions):
        bucket = versions[version_key]
        denominator = bucket["sample_count"]
        history_denominator = bucket["history_sample_count"]
        distribution = bucket["_candidate_count_distribution"]
        field_metrics = []
        for field in IDENTITY_FIELDS:
            total = bucket["_field_total"][field]
            if not total:
                continue
            correct = bucket["_field_correct"][field]
            field_metrics.append(
                {
                    "field": field,
                    "correct": correct,
                    "total": total,
                    "accuracy": round(correct / total, 4),
                }
            )
        output_versions.append(
            {
                "pipeline_version": bucket["pipeline_version"],
                "knowledge_version": bucket["knowledge_version"],
                "knowledge_version_number": bucket["knowledge_version_number"],
                "model": bucket["model"],
                "history_sample_count": history_denominator,
                "sample_count": denominator,
                "prediction_error_count": bucket["prediction_error_count"],
                "candidate_count_distribution": {
                    str(count): distribution.get(count, 0)
                    for count in range(4)
                },
                "multi_candidate_rate": (
                    round(
                        sum(
                            total
                            for count, total in distribution.items()
                            if count >= 2
                        )
                        / history_denominator,
                        4,
                    )
                    if history_denominator
                    else None
                ),
                "three_candidate_rate": (
                    round(distribution.get(3, 0) / history_denominator, 4)
                    if history_denominator
                    else None
                ),
                "top1_correct": bucket["top1_correct"],
                "top3_correct": bucket["top3_correct"],
                "top1_accuracy": (
                    round(bucket["top1_correct"] / denominator, 4)
                    if denominator
                    else None
                ),
                "top3_recall": (
                    round(bucket["top3_correct"] / denominator, 4)
                    if denominator
                    else None
                ),
                "acceptance_evaluable": denominator >= minimum_acceptance_samples,
                "acceptance_status": (
                    "passed"
                    if denominator >= minimum_acceptance_samples
                    and bucket["top1_correct"] / denominator >= target_top1
                    and bucket["top3_correct"] / denominator >= target_top3
                    else "failed"
                    if denominator >= minimum_acceptance_samples
                    else "pending"
                ),
                "field_metrics": field_metrics,
            }
        )
    return {
        "minimum_acceptance_samples": minimum_acceptance_samples,
        "target_top1": target_top1,
        "target_top3": target_top3,
        "ground_truth_count": len(truth_sample_ids),
        "scorable_ground_truth_count": (
            len(truth_sample_ids) - len(incomplete_truth_ids)
        ),
        "incomplete_ground_truth_count": len(incomplete_truth_ids),
        "unresolved_labeled_count": len(unresolved_labeled_ids),
        "versions": output_versions,
    }


def calculate_feedback_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize actionable field errors from human benchmark corrections."""
    label_counts: dict[str, int] = {}
    field_error_counts = {field: 0 for field in IDENTITY_FIELDS}
    field_correction_counts = {field: 0 for field in IDENTITY_FIELDS}
    corrected_samples = 0
    for row in rows:
        label = row.get("label_result")
        if label:
            label_counts[label] = label_counts.get(label, 0) + 1
        correction = (
            row.get("corrected_identity")
            or selected_candidate_identity(row)
        )
        if not correction:
            continue
        corrected_samples += 1
        candidates = (row.get("prediction") or {}).get("candidates") or []
        top1 = (candidates[0].get("identity") or {}) if candidates else {}
        for field in IDENTITY_FIELDS:
            if field not in correction:
                continue
            field_correction_counts[field] += 1
            if _comparable_identity_value(top1.get(field)) != _comparable_identity_value(
                correction[field]
            ):
                field_error_counts[field] += 1
    field_errors = [
        {
            "field": field,
            "errors": field_error_counts[field],
            "corrections": field_correction_counts[field],
            "error_rate": round(
                field_error_counts[field] / field_correction_counts[field], 4
            )
            if field_correction_counts[field]
            else None,
            "accuracy": round(
                1 - field_error_counts[field] / field_correction_counts[field], 4
            )
            if field_correction_counts[field]
            else None,
        }
        for field in IDENTITY_FIELDS
        if field_correction_counts[field]
    ]
    field_errors.sort(key=lambda item: (-item["errors"], item["field"]))
    return {
        "label_counts": label_counts,
        "corrected_sample_count": corrected_samples,
        "field_errors": field_errors,
        "knowledge_queue_count": sum(
            row.get("label_result") in {"top2_correct", "top3_correct", "none_correct"}
            and bool(
                row.get("corrected_identity")
                or selected_candidate_identity(row)
            )
            for row in rows
        ),
    }


class CardHobbyBenchmarkCollector:
    def __init__(self, settings: Settings) -> None:
        self.timeout = settings.provider_timeout_seconds
        self.min_interval = max(settings.cardhobby_min_interval_ms / 1000, 0.2)

    async def collect(
        self,
        connection: AsyncConnection,
        *,
        name: str,
        sample_size: int,
        page_count: int,
        seed: int,
        max_per_seller: int,
    ) -> UUID:
        # Collection is a long transaction. Serialize equal benchmark names so
        # client retries cannot create several invisible, overlapping batches
        # before the first request commits.
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"recognition-benchmark:{name}",),
        )
        existing_result = await connection.execute(
            """
            SELECT id
            FROM recognition_benchmark_batch
            WHERE source_code = 'cardhobby' AND name = %s AND status <> 'failed'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (name,),
        )
        existing = await existing_result.fetchone()
        if existing is not None:
            return existing["id"]
        batch_row = await connection.execute(
            """
            INSERT INTO recognition_benchmark_batch (
                name, source_code, status, sampling_config
            ) VALUES (%s, 'cardhobby', 'collecting', %s)
            RETURNING id
            """,
            (
                name,
                Jsonb(
                    {
                        "sport": "Basketball",
                        "status": "sold",
                        "category": "single_card",
                        "min_final_price": 20,
                        "sample_size": sample_size,
                        "page_count": page_count,
                        "seed": seed,
                        "max_per_seller": max_per_seller,
                    }
                ),
            ),
        )
        batch_id = (await batch_row.fetchone())["id"]
        try:
            candidates = await self._fetch_candidates(page_count=page_count, seed=seed)
            prior_rows = await connection.execute(
                """
                SELECT DISTINCT source_item_id
                FROM recognition_benchmark_sample
                WHERE source_code = 'cardhobby' AND batch_id <> %s
                """,
                (batch_id,),
            )
            prior_item_ids = {
                str(row["source_item_id"])
                for row in await prior_rows.fetchall()
            }
            candidates = [
                candidate
                for candidate in candidates
                if str(candidate.item_id) not in prior_item_ids
            ]
            selected = choose_candidates(
                candidates,
                sample_size=sample_size,
                max_per_seller=max_per_seller,
                seed=seed,
            )
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers=CARDHOBBY_HEADERS,
            ) as client:
                for candidate in selected:
                    try:
                        detail = await self._fetch_detail(client, candidate.item_id)
                    except (httpx.HTTPError, RuntimeError, ValueError):
                        continue
                    images = [
                        str(image["ImageUrl"])
                        for image in detail.get("NewCommunityImages", [])
                        if is_cardhobby_image_url(str(image.get("ImageUrl") or ""))
                    ][:4]
                    if not images:
                        continue
                    await connection.execute(
                        """
                        INSERT INTO recognition_benchmark_sample (
                            batch_id, source_code, source_item_id, source_url,
                            image_urls, occurred_at, final_price, currency,
                            sealed_reference, source_snapshot
                        ) VALUES (
                            %s, 'cardhobby', %s, %s, %s, %s, %s, 'CNY', %s, %s
                        )
                        ON CONFLICT (batch_id, source_code, source_item_id) DO NOTHING
                        """,
                        (
                            batch_id,
                            candidate.item_id,
                            f"{CARDHOBBY_ORIGIN}/market/item/{candidate.item_id}",
                            Jsonb(images),
                            candidate.occurred_at,
                            candidate.final_price,
                            Jsonb(
                                {
                                    "title": detail.get("Title") or candidate.title,
                                    "property_json": detail.get("PropertyJson"),
                                    "solr_title": detail.get("SolrTitle"),
                                }
                            ),
                            Jsonb(
                                {
                                    "search": candidate.raw,
                                    "detail": {
                                        key: detail.get(key)
                                        for key in (
                                            "Id", "ThirdCategoryId", "ThirdCategoryName",
                                            "ByWay", "Price", "LastOnTime",
                                        )
                                    },
                                }
                            ),
                        ),
                    )
                    await asyncio.sleep(self.min_interval)
            count_row = await connection.execute(
                "SELECT count(*) AS count FROM recognition_benchmark_sample WHERE batch_id = %s",
                (batch_id,),
            )
            count = (await count_row.fetchone())["count"]
            await connection.execute(
                """
                UPDATE recognition_benchmark_batch
                SET status = %s, updated_at = now()
                WHERE id = %s
                """,
                ("ready" if count else "failed", batch_id),
            )
            return batch_id
        except Exception:
            await connection.execute(
                """
                UPDATE recognition_benchmark_batch
                SET status = 'failed', updated_at = now()
                WHERE id = %s
                """,
                (batch_id,),
            )
            raise

    async def top_up(
        self,
        connection: AsyncConnection,
        *,
        batch_id: UUID,
        additional_count: int,
        page_count: int,
        seed: int,
        max_per_seller: int,
    ) -> int:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"recognition-benchmark-top-up:{batch_id}",),
        )
        batch_result = await connection.execute(
            """
            SELECT source_code, sampling_config,
                   (SELECT count(*) FROM recognition_benchmark_sample
                    WHERE batch_id = %s) AS current_count
            FROM recognition_benchmark_batch
            WHERE id = %s
            """,
            (batch_id, batch_id),
        )
        batch = await batch_result.fetchone()
        if batch is None:
            raise ValueError("benchmark not found")
        if batch["source_code"] != "cardhobby":
            raise ValueError("only CardHobby benchmarks can be topped up")
        target_count = int((batch["sampling_config"] or {}).get("sample_size") or 0)
        if target_count:
            additional_count = min(
                additional_count,
                max(target_count - int(batch["current_count"]), 0),
            )
        if additional_count == 0:
            return 0

        candidates = await self._fetch_candidates(page_count=page_count, seed=seed)
        prior_result = await connection.execute(
            """
            SELECT DISTINCT source_item_id
            FROM recognition_benchmark_sample
            WHERE source_code = 'cardhobby'
            """
        )
        prior_ids = {
            str(row["source_item_id"])
            for row in await prior_result.fetchall()
        }
        selected = choose_candidates(
            [
                candidate
                for candidate in candidates
                if str(candidate.item_id) not in prior_ids
            ],
            sample_size=additional_count,
            max_per_seller=max_per_seller,
            seed=seed,
        )
        added = 0
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=CARDHOBBY_HEADERS,
        ) as client:
            for candidate in selected:
                try:
                    detail = await self._fetch_detail(client, candidate.item_id)
                except (httpx.HTTPError, RuntimeError, ValueError):
                    continue
                title = str(detail.get("Title") or candidate.title)
                if EXCLUDED_TITLE.search(title):
                    continue
                images = [
                    str(image["ImageUrl"])
                    for image in detail.get("NewCommunityImages", [])
                    if is_cardhobby_image_url(str(image.get("ImageUrl") or ""))
                ][:4]
                if not images:
                    continue
                inserted = await connection.execute(
                    """
                    INSERT INTO recognition_benchmark_sample (
                        batch_id, source_code, source_item_id, source_url,
                        image_urls, occurred_at, final_price, currency,
                        sealed_reference, source_snapshot
                    ) VALUES (
                        %s, 'cardhobby', %s, %s, %s, %s, %s, 'CNY', %s, %s
                    )
                    ON CONFLICT (batch_id, source_code, source_item_id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        batch_id,
                        candidate.item_id,
                        f"{CARDHOBBY_ORIGIN}/market/item/{candidate.item_id}",
                        Jsonb(images),
                        candidate.occurred_at,
                        candidate.final_price,
                        Jsonb(
                            {
                                "title": title,
                                "property_json": detail.get("PropertyJson"),
                                "solr_title": detail.get("SolrTitle"),
                            }
                        ),
                        Jsonb(
                            {
                                "search": candidate.raw,
                                "top_up": {
                                    "seed": seed,
                                    "page_count": page_count,
                                    "reason": "replace_ineligible_source_samples",
                                },
                            }
                        ),
                    ),
                )
                if await inserted.fetchone():
                    added += 1
                await asyncio.sleep(self.min_interval)
        await connection.execute(
            """
            UPDATE recognition_benchmark_batch
            SET status = 'ready',
                sampling_config = sampling_config || %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                Jsonb(
                    {
                        "last_top_up": {
                            "requested": additional_count,
                            "added": added,
                            "seed": seed,
                            "page_count": page_count,
                        }
                    }
                ),
                batch_id,
            ),
        )
        return added

    async def _fetch_candidates(
        self, *, page_count: int, seed: int
    ) -> list[CardHobbyBenchmarkCandidate]:
        rng = random.Random(seed)
        page_pool = list(range(1, max(20, page_count * 12) + 1))
        pages = sorted(rng.sample(page_pool, k=min(page_count, len(page_pool))))
        candidates: list[CardHobbyBenchmarkCandidate] = []
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=CARDHOBBY_HEADERS,
        ) as client:
            for page in pages:
                payload = None
                for attempt in range(3):
                    try:
                        response = await client.get(
                            CARDHOBBY_SEARCH_URL,
                            params=build_cardhobby_search_params(page),
                        )
                        response.raise_for_status()
                        payload = response.json()
                        break
                    except (httpx.HTTPError, ValueError):
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))
                if payload is None:
                    continue
                for item in (payload.get("data") or {}).get("PagedMarketItemList") or []:
                    candidate = parse_cardhobby_candidate(item)
                    if candidate:
                        candidates.append(candidate)
                await asyncio.sleep(self.min_interval)
        return candidates

    async def _fetch_detail(
        self, client: httpx.AsyncClient, item_id: str
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.get(
                    CARDHOBBY_DETAIL_URL,
                    params={"id": item_id, "memberId": ""},
                    headers={
                        **CARDHOBBY_HEADERS,
                        "Referer": f"{CARDHOBBY_ORIGIN}/market/item/{item_id}",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("result"):
                    raise RuntimeError(f"CardHobby detail failed for {item_id}")
                return payload.get("data") or {}
            except (httpx.HTTPError, RuntimeError, ValueError) as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(
            f"CardHobby detail failed after retries for {item_id}: {last_error}"
        )
