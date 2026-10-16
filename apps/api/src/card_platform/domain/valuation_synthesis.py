"""Evidence-gated, read-only combination of immutable valuation snapshots."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RULE_VERSION = "codex-synthesis-1"


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def _date(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (TypeError, ValueError):
        return None


def _url(value):
    try:
        parts = urlsplit(str(value))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query)
            if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
        ]
        return urlunsplit(
            ("https", parts.netloc.lower(), parts.path.rstrip("/"), urlencode(sorted(query)), "")
        )
    except ValueError:
        return None


def _urls(value):
    if isinstance(value, dict):
        result = set()
        for key, entry in value.items():
            if key in {"url", "source_url"}:
                normalized = _url(entry)
                if normalized:
                    result.add(normalized)
            else:
                result |= _urls(entry)
        return result
    if isinstance(value, list):
        return set().union(*(_urls(entry) for entry in value))
    return set()


def _prices(source, keys):
    values = [_number(source.get(key)) for key in keys]
    if any(value is None for value in values):
        return None
    return values if 0 <= values[0] <= values[1] <= values[2] else None


def synthesize_valuation(original, codex, identity_version_id, base_currency, *, now=None):
    """Return derived prices and audit; caller retains original job status on rejection.

    Unbound legacy snapshots remain reviewable, never silently assumed to match.
    Aggregates with overlapping evidence cannot be disentangled: retain original.
    """
    now = now or datetime.now(timezone.utc)
    result = dict(
        rule_version=RULE_VERSION,
        status="original_only",
        reasons=[],
        currency=original.get("currency") if original else None,
        low_value=original.get("low_value") if original else None,
        fair_value=original.get("fair_value") if original else None,
        high_value=original.get("high_value") if original else None,
        original_id=str(original["id"]) if original else None,
        original_fair_value=original.get("fair_value") if original else None,
        original_currency=original.get("currency") if original else None,
        original_confidence=original.get("confidence") if original else None,
        original_valued_at=str(original.get("valued_at")) if original else None,
        codex_id=str(codex["id"]) if codex else None,
        codex_analysis_version=codex.get("analysis_version") if codex else None,
        codex_fair_value=codex.get("fair_value") if codex else None,
        codex_confidence=codex.get("valuation_confidence") if codex else None,
        identity_version_id=str(identity_version_id) if identity_version_id else None,
        evidence_urls=[],
        weights={},
    )
    if not codex:
        return result
    reasons = result["reasons"]
    raw = codex.get("raw_result") or {}
    confidence = _number(codex.get("valuation_confidence"))
    identity_confidence = _number(codex.get("identity_confidence"))
    if raw.get("reliable_price") is False:
        reasons.append("Codex 明确标注价格未经可靠成交核验")
    if raw.get("identity_conflict") is True or raw.get("identity_compatibility") == "incompatible":
        reasons.append("Codex 明确标注身份冲突，需复核")
    if confidence is None or not Decimal(".5") <= confidence <= 1:
        reasons.append("Codex 估值置信度不足或缺失")
    if (
        not identity_version_id
        or not codex.get("identity_version_id")
        or str(identity_version_id) != str(codex["identity_version_id"])
    ):
        reasons.append("Codex 未绑定当前确认身份版本，需复核同版关系")
    if identity_confidence is None or not Decimal(".8") <= identity_confidence <= 1:
        reasons.append("Codex 身份置信度不足或缺失")
    prices = _prices(codex, ("fair_value_low", "fair_value", "fair_value_high"))
    if prices is None:
        reasons.append("Codex 价格区间无效或缺失")
    if codex.get("currency") != base_currency or (
        original and original.get("currency") != codex.get("currency")
    ):
        reasons.append("币种不同，缺少可追溯汇率，需复核")
    created = _date(codex.get("created_at"))
    if created is None or not 0 <= (now - created).days <= 365:
        reasons.append("Codex 分析日期无效或超过一年")
    evidence = {}
    evidence_prices = {}
    for entry in codex.get("evidence_summary") or []:
        if not isinstance(entry, dict):
            continue
        price, sold, url = (
            _number(entry.get("price")),
            _date(entry.get("sold_at")),
            _url(entry.get("url")),
        )
        if (
            entry.get("type") not in {"completed_sale", "comparable_sale"}
            or entry.get("verified") is not True
            or not url
            or price is None
            or price <= 0
            or entry.get("currency") != codex.get("currency")
            or sold is None
            or not 0 <= (now - sold).days <= 365
            or entry.get("match_type") not in {"exact", "comparable"}
        ):
            continue
        quality = Decimal("1") if entry["match_type"] == "exact" else Decimal(".6")
        weight = quality * Decimal(180) / Decimal(180 + (now - sold).days)
        # Duplicate contradictory labels never increase evidence quality.
        evidence[url] = min(weight, evidence.get(url, weight))
        if url in evidence_prices and evidence_prices[url] != price:
            reasons.append("同一成交链接包含矛盾金额，需复核")
        evidence_prices[url] = price
    result["evidence_urls"] = sorted(evidence)
    if not evidence:
        reasons.append("缺少已核验、含金额日期及同版或可比关系的成交证据")
    elif prices is not None:
        anchor = median(evidence_prices.values())
        result["evidence_price_median"] = anchor
        if not anchor / 2 <= prices[1] <= anchor * 2:
            reasons.append("Codex 估值与已核验成交中位价相差超过两倍，缺少可核验调整依据")
    if reasons:
        result["status"] = (
            "insufficient_evidence"
            if raw.get("reliable_price") is False
            or not evidence
            or confidence is None
            or confidence < Decimal(".5")
            else "review_required"
        )
        return result
    if original and original.get("method") in {"manual", "manual_override"}:
        reasons.append("保留人工估值，Codex 仅作参考")
        return result
    codex_weight = confidence * min(
        sum(evidence.values()) / len(evidence),
        Decimal(180) / Decimal(180 + (now - created).days),
    )
    result["weights"]["codex"] = codex_weight
    if original:
        if str((original.get("evidence") or {}).get("identity_version_id")) != str(
            identity_version_id
        ):
            result["status"] = "review_required"
            reasons.append("原估值未绑定当前确认身份版本，不能确认同版合并")
            return result
        original_prices = _prices(original, ("low_value", "fair_value", "high_value"))
        original_confidence = _number(original.get("confidence"))
        valued = _date(original.get("valued_at"))
        if (
            original_prices is None
            or not original_confidence
            or not 0 < original_confidence <= 1
            or valued is None
        ):
            result["status"] = "review_required"
            reasons.append("原估值缺少有效区间、置信度或时间，需复核")
            return result
        a, b = original_prices[1], prices[1]
        if max(a, b) > 2 * min(a, b):
            result["status"] = "review_required"
            reasons.append("两份估值相差超过两倍，需复核")
            return result
        if _urls(original.get("evidence")) & set(evidence):
            reasons.append("成交证据跨来源重复，保留原估值以避免重复增权")
            return result
        # Without source URLs independence cannot be established.
        if not _urls(original.get("evidence")):
            result["status"] = "review_required"
            reasons.append("原估值缺少证据链接，无法排除重复成交")
            return result
        weight = original_confidence * Decimal(180) / Decimal(180 + max(0, (now - valued).days))
        result["weights"]["original"] = weight
        prices = [
            (left * weight + right * codex_weight) / (weight + codex_weight)
            for left, right in zip(original_prices, prices)
        ]
        result["status"] = "combined"
        reasons.append("按估值置信度、成交证据质量和时效加权")
    else:
        result["status"] = "codex_supported"
        reasons.append("Codex 已核验成交证据补足原估值缺失")
    result.update(
        currency=base_currency,
        **dict(
            zip(
                ("low_value", "fair_value", "high_value"),
                [value.quantize(Decimal(".01")) for value in prices],
            )
        ),
    )
    return result
