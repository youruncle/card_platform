from dataclasses import dataclass
from decimal import Decimal
import re
import time
from typing import Awaitable, Callable

from card_platform.schemas import CardRecognitionResult


GENERIC_PARALLELS = {"prizm", "refractor", "parallel", "折射", "折射版"}
BIRTHDATE_SERIAL_CAVEAT = "birthday_serial_fraction_removed"


@dataclass(frozen=True, slots=True)
class VisionTier:
    name: str
    model: str
    input_cny_per_million: Decimal
    output_cny_per_million: Decimal


FLASH_TIER = VisionTier("flash", "qwen3-vl-flash", Decimal("0.15"), Decimal("1.5"))
PLUS_TIER = VisionTier("plus", "qwen3-vl-plus", Decimal("1"), Decimal("10"))
MAX_TIER = VisionTier("max", "qwen3.8-max", Decimal("12"), Decimal("36"))


def recognition_quality_issues(result: CardRecognitionResult) -> list[str]:
    issues: list[str] = []
    for field in ("player_name", "season_start", "product_name", "card_number"):
        if getattr(result, field) in (None, ""):
            issues.append(f"missing_{field}")
    if (result.serial_number is None) != (result.serial_total is None):
        issues.append("partial_serial_fraction")
    if result.serial_number and result.serial_total and result.serial_number > result.serial_total:
        issues.append("invalid_serial_fraction")
    if str(result.parallel_name or "").strip().casefold() in GENERIC_PARALLELS:
        issues.append("generic_parallel_name")
    parallel = str(result.parallel_name or "").strip().casefold()
    product = str(result.product_name or "").strip().casefold()
    if "prizm" in parallel and not any(token in product for token in ("prizm", "mosaic")):
        issues.append("parallel_product_technology_conflict")
    if re.search(r"\b\d+\s*/\s*\d+\b|/\s*\d+\b", parallel):
        issues.append("parallel_contains_serial_fraction")
    parallel_words = set(re.findall(r"[a-z]+", parallel))
    if result.serial_total and parallel_words and parallel_words <= {
        "gold", "silver", "red", "blue", "green", "orange", "purple", "black", "white"
    }:
        issues.append("numbered_parallel_name_underspecified")
    caveats = " ".join(result.caveats).casefold()
    if BIRTHDATE_SERIAL_CAVEAT in caveats:
        issues.append("birthdate_serial_false_positive")
    if any(token in caveats for token in ("冲突", "conflict", "无法确认", "不确定")):
        issues.append("model_reported_conflict")
    return issues


def sanitize_birthdate_serial_false_positive(
    result: CardRecognitionResult,
) -> CardRecognitionResult:
    """Discard a serial fraction copied from a printed player birth date.

    A model can split ``BORN: 3/6/73`` into the otherwise plausible serial
    fraction ``3/6``.  Treat the fraction as unknown whenever the same values
    occur as the month/day prefix of an explicitly labelled birth date.  The
    marker deliberately keeps the recognition in research_required state so a
    real, independently visible serial stamp can still be restored by review.
    """
    if result.serial_number is None or result.serial_total is None:
        return result

    evidence = [*result.caveats]
    evidence.extend(
        item
        for values in result.field_evidence.values()
        for item in values
    )
    evidence_text = " ".join(evidence).casefold()
    number = str(result.serial_number)
    total = str(result.serial_total)
    birth_label = r"(?:born|birth(?:day|\s*date)?|d\.?o\.?b\.?|出生(?:日期)?|生日)"
    birthday_fraction = re.compile(
        rf"{birth_label}[^\d]{{0,32}}0*{re.escape(number)}\s*[/.-]\s*"
        rf"0*{re.escape(total)}\s*[/.-]\s*\d{{2,4}}\b",
        re.IGNORECASE,
    )
    if not birthday_fraction.search(evidence_text):
        return result

    caveats = list(result.caveats)
    if not any(BIRTHDATE_SERIAL_CAVEAT in item for item in caveats):
        caveats.append(
            f"{BIRTHDATE_SERIAL_CAVEAT}: ignored {number}/{total} because it is the "
            "month/day prefix of a printed player birth date; verify any serial stamp separately"
        )
    return result.model_copy(update={
        "serial_number": None,
        "serial_total": None,
        "is_numbered": None,
        "caveats": caveats,
    })


def recognition_quality_score(result: CardRecognitionResult) -> Decimal:
    required = (result.player_name, result.season_start, result.product_name, result.card_number)
    completeness = Decimal(sum(value not in (None, "") for value in required)) / Decimal(4)
    penalty = Decimal("0.08") * len(recognition_quality_issues(result))
    return max(Decimal("0"), result.confidence * Decimal("0.65") + completeness * Decimal("0.35") - penalty)


def estimate_call_cost_cny(
    tier: VisionTier, prompt_tokens: int | None, completion_tokens: int | None
) -> Decimal:
    input_cost = Decimal(prompt_tokens or 0) * tier.input_cny_per_million / Decimal(1_000_000)
    output_cost = Decimal(completion_tokens or 0) * tier.output_cny_per_million / Decimal(1_000_000)
    return (input_cost + output_cost).quantize(Decimal("0.000001"))


def finalize_recognition_status(result: CardRecognitionResult) -> CardRecognitionResult:
    result = sanitize_birthdate_serial_false_positive(result)
    issues = recognition_quality_issues(result)
    return result.model_copy(update={
        "recognition_status": "research_required" if issues else "resolved",
        "recognition_quality_issues": issues,
    })


class TieredVisionRouter:
    def __init__(
        self,
        flash: Callable[[str | None], Awaitable[tuple[CardRecognitionResult, dict]]],
        plus: Callable[[str | None], Awaitable[tuple[CardRecognitionResult, dict]]] | None,
        maximum: Callable[[str | None], Awaitable[tuple[CardRecognitionResult, dict]]] | None,
        *,
        plus_threshold: Decimal = Decimal("0.88"),
        max_threshold: Decimal = Decimal("0.74"),
    ) -> None:
        self.flash = flash
        self.plus = plus
        self.maximum = maximum
        self.plus_threshold = plus_threshold
        self.max_threshold = max_threshold

    async def recognize(self) -> CardRecognitionResult:
        candidates: list[CardRecognitionResult] = []
        trace: list[dict[str, object]] = []
        total_cost = Decimal("0")

        async def call(tier: VisionTier, function, reason: str) -> CardRecognitionResult | None:
            nonlocal total_cost
            prior = candidates[-1].model_dump_json(exclude={"recognition_route"}) if candidates else None
            started = time.monotonic()
            try:
                result, usage = await function(prior)
            except Exception as error:
                trace.append({
                    "tier": tier.name, "model": tier.model, "reason": reason,
                    "status": "failed", "error_type": type(error).__name__,
                    "error": str(error)[:240],
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "estimated_cost_cny": "0",
                })
                return None
            result = sanitize_birthdate_serial_false_positive(result)
            issues = recognition_quality_issues(result)
            cost = estimate_call_cost_cny(
                tier, usage.get("prompt_tokens"), usage.get("completion_tokens")
            )
            total_cost += cost
            trace.append({
                "tier": tier.name, "model": tier.model, "reason": reason,
                "confidence": str(result.confidence), "quality_issues": issues,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "estimated_cost_cny": str(cost),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "status": "succeeded",
            })
            candidates.append(result)
            return result

        current = await call(FLASH_TIER, self.flash, "default_low_cost_route")
        issues = recognition_quality_issues(current) if current else ["flash_call_failed"]
        if self.plus and (current is None or current.confidence < self.plus_threshold or issues):
            upgraded = await call(PLUS_TIER, self.plus, "flash_quality_gate_failed")
            if upgraded is not None:
                current = upgraded
                issues = recognition_quality_issues(current)
        visual_hard_issues = {
            "partial_serial_fraction", "invalid_serial_fraction", "model_reported_conflict",
        }
        missing_critical = any(issue.startswith("missing_") for issue in issues)
        if self.maximum and (
            current is None or current.confidence < self.max_threshold
            or visual_hard_issues.intersection(issues) or missing_critical
        ):
            await call(MAX_TIER, self.maximum, "plus_hard_identity_gate_failed")

        if not candidates:
            raise RuntimeError("all configured vision model tiers failed")
        best = max(candidates, key=recognition_quality_score)
        successful_trace = [item for item in trace if item.get("status") == "succeeded"]
        return finalize_recognition_status(best.model_copy(update={
            "recognition_model": successful_trace[candidates.index(best)]["model"],
            "recognition_route": trace,
            "recognition_estimated_cost_cny": total_cost,
        }))
