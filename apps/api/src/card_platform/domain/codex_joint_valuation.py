"""Joint identity/evidence decisions. Model price estimates are never market samples."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.product_identity import named_child_product_for_parent
from card_platform.domain.comparables import (
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
    Classification,
)
from card_platform.domain.hierarchical_estimator import ClassifiedSale, HierarchicalEstimator
from card_platform.domain.valuation_synthesis import _date, _number, _url

RULE_VERSION = "codex-joint-evidence-2"
UNCERTAIN = re.compile(
    r"疑似|候选|未确认|待确认|不确定|难读|可能|candidate|uncertain|unknown|\?", re.I
)


def sale_url(value):
    url = _url(value)
    if not url:
        return None
    parts = urlsplit(url)
    if re.fullmatch(r"(?:www\.|m\.)?ebay\.(?:com|co\.uk|de|ca|com\.au)", parts.hostname or ""):
        match = re.fullmatch(r"/itm/(?:[^/]+/)?(\d+)", parts.path)
        if match:
            return "https://www.ebay.com/itm/" + match[1]
    return url


def norm(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def identity_fields(raw):
    """Recover explicit values only; keep unparseable/uncertain fields as caveats."""
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    uncertain = []
    aliases = dict(
        player_name=("player_name", "player", "character", "subject"),
        product_name=("product_name", "product", "series", "set", "brand"),
        card_number=("card_number", "number"),
        insert_name=("insert_name", "insert"),
        parallel_name=("parallel_name", "parallel"),
        grade_company=("grade_company",),
    )
    for field, keys in aliases.items():
        value = next((raw[k] for k in keys if raw.get(k) not in (None, "")), None)
        if value is None:
            continue
        if UNCERTAIN.search(str(value)):
            uncertain.append(field)
            continue
        value = re.split(r"[（(;；]", str(value))[0].strip()
        if field == "card_number":
            match = re.match(r"#?([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)", value)
            value = match[1] if match else None
        if value:
            result[field] = value
    year = next(
        (raw.get(k) for k in ("season_start", "year", "season", "season_researched") if raw.get(k)),
        None,
    )
    if year and not UNCERTAIN.search(str(year)):
        match = re.search(r"(19\d{2}|20\d{2})", str(year))
        if match:
            result["season_start"] = int(match[1])
    for field, alias in [("is_autograph", "autograph"), ("is_memorabilia", "memorabilia")]:
        value = raw.get(field, raw.get(alias))
        if isinstance(value, bool):
            result[field] = value
        elif isinstance(value, str) and value.startswith(("未见", "无", "非")):
            result[field] = False
    serial = raw.get("serial_total", raw.get("print_run"))
    if serial is None:
        match = re.search(
            r"\d+\s*/\s*(\d+)", str(raw.get("serial_number") or raw.get("serial") or "")
        )
        if match:
            serial = match[1]
    if serial is not None and str(serial).isdigit():
        result["serial_total"] = int(serial)
    if raw.get("grade_value") is not None:
        result["grade_value"] = str(raw["grade_value"])
    return result, uncertain


def same_value(field, left, right):
    if field in {"season_start", "serial_total", "is_autograph", "is_memorabilia"}:
        return left == right
    a, b = norm(left), norm(right)
    if field == "product_name":
        if named_child_product_for_parent(left, right) or named_child_product_for_parent(
            right, left
        ):
            return False
        ignored = {"panini", "topps", "basketball", "nba", "cards"}
        aa, bb = set(a.split()) - ignored, set(b.split()) - ignored
        return bool(aa and bb) and (aa <= bb or bb <= aa)
    if field == "parallel_name":
        bases = {"base", "regular", "standard", "普通版", "基础"}
        if str(left).casefold() in bases and str(right).casefold() in bases:
            return True
    return bool(a and b) and a == b


def reconcile_identity(item, snapshot, image_hashes):
    raw = snapshot.get("raw_result") or {}
    reasons = []
    if raw.get("identity_conflict") is True or raw.get("identity_compatibility") == "incompatible":
        reasons.append("历史分析明确记录身份冲突")
    confirmed = item.get("_confirmed_identity") or {}
    for field in confirmed.keys() & {
        "player_name",
        "season_start",
        "product_name",
        "card_number",
        "insert_name",
        "parallel_name",
        "serial_total",
        "grade_company",
        "grade_value",
        "is_autograph",
        "is_memorabilia",
    }:
        current, previous = item.get(field), confirmed[field]
        if current != previous and not same_value(field, current, previous):
            reasons.append("当前字段与先前人工确认不一致：" + field)
    if str(raw.get("collection_item_id")) != str(item["id"]):
        reasons.append("历史分析不属于当前卡片")
    images = {
        entry.get("side"): entry
        for entry in (raw.get("input_images") or [])
        if isinstance(entry, dict)
    }
    for side in ("front", "back"):
        image = images.get(side, {})
        if (
            image.get("url") != item.get("image_" + side + "_url")
            or not image_hashes.get(side)
            or image.get("sha256") != image_hashes[side]
        ):
            reasons.append(side + " 原图已变化或无法核验")
    confidence = _number(snapshot.get("identity_confidence"))
    if confidence is None or confidence < Decimal(".8"):
        reasons.append("历史身份置信度不足")
    recovered, uncertain = identity_fields(raw.get("identity") or {})
    merged = dict(item)
    agreed = []
    supplemented = []
    conflicts = []
    for field, value in recovered.items():
        current = item.get(field)
        if current in (None, "") and field in confirmed and not same_value(field, current, value):
            conflicts.append(field)
        elif current in (None, ""):
            merged[field] = value
            supplemented.append(field)
        elif same_value(field, current, value):
            agreed.append(field)
        else:
            conflicts.append(field)
    required = {"player_name", "season_start", "product_name", "card_number"}
    missing = required - set(recovered)
    if missing:
        reasons.append("历史身份缺少明确字段：" + ",".join(sorted(missing)))
    if conflicts:
        reasons.append("系统与 Codex 身份冲突：" + ",".join(conflicts))
    # Unknown parallel/grade cannot silently become an exact-card match.
    if set(uncertain) & {"parallel_name", "card_number", "grade_company"}:
        reasons.append("历史版本或评级仍不确定：" + ",".join(uncertain))
    return merged, dict(
        agreed=agreed,
        supplemented=supplemented,
        conflicts=conflicts,
        uncertain=uncertain,
        reasons=reasons,
        matched=not reasons,
        snapshot_id=str(snapshot["id"]),
        analysis_version=snapshot.get("analysis_version"),
    )


def extract_evidence(snapshot):
    output = []
    for index, entry in enumerate(snapshot.get("evidence_summary") or []):
        if not isinstance(entry, dict):
            entry = {"detail": str(entry)}
        text = " ".join(
            str(entry.get(k) or "") for k in ("title", "detail", "status", "match_reason")
        )
        kind = str(entry.get("type") or "")
        description = kind + " " + text
        if re.search(
            r"best.offer|议价|实际成交价未知|uncertain_amount|unknown.*price", description, re.I
        ):
            category = "unknown_sale_price"
        elif re.search(
            r"挂牌|asking|listing_identity|auction_start|ended_listing_not_sale", description, re.I
        ):
            category = "asking_price"
        elif re.search(r"排除|excluded|different_|不同平行", kind, re.I):
            category = "excluded"
        elif re.search(
            r"aggregat|index|聚合|guide|价格标签|价格指南|非直接成交|original_retail", kind, re.I
        ):
            category = "market_reference"
        elif re.search(r"sold|sale|拍卖|成交", kind, re.I):
            category = "reported_sale"
        else:
            category = "identity_reference"
        price = _number(entry.get("price"))
        currency = entry.get("currency")
        if price is None:
            match = re.search(r"(\d+(?:\.\d+)?)\s*(美元|人民币|元|USD|CNY)", text, re.I)
            if match:
                price = _number(match[1])
                currency = "USD" if match[2].lower() in {"美元", "usd"} else "CNY"
        date = entry.get("sold_at") or entry.get("transaction_date")
        if not date:
            match = re.search(r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})", text)
            if match:
                date = f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}"
        if date and _date(date) is None:
            date = None
        urls = re.findall(r"https?://[^\s，；。]+", text)
        url = sale_url(entry.get("url") or entry.get("source_url") or (urls[0] if urls else ""))
        output.append(
            dict(
                snapshot_id=str(snapshot["id"]),
                analysis_version=snapshot.get("analysis_version"),
                evidence_index=index,
                kind=category,
                url=url,
                price=price,
                currency=currency,
                sold_at=date,
                verified=False,
                original_type=kind,
                original_text=text,
            )
        )
    return output


def _base_price(price, currency, base_currency, fx, now):
    price = _number(price)
    if price is None or price <= 0:
        return None
    if currency == base_currency:
        return price
    rate = _number(fx.get("rate"))
    date = _date(fx.get("date"))
    if (
        fx.get("base") == currency
        and fx.get("quote") == base_currency
        and rate
        and rate > 0
        and date
        and 0 <= (now - date).days <= 7
        and fx.get("url")
    ):
        return price * rate
    return None


def joint_valuation(
    item,
    history,
    original,
    market,
    base_currency,
    *,
    reviews,
    fx,
    image_hashes,
    now=None,
    identity_reviews=None,
):
    now = now or datetime.now(timezone.utc)
    result = dict(
        rule_version=RULE_VERSION,
        status="original_only" if original else "insufficient_evidence",
        reasons=[],
        currency=original.get("currency") if original else None,
        low_value=original.get("low_value") if original else None,
        fair_value=original.get("fair_value") if original else None,
        high_value=original.get("high_value") if original else None,
        original_id=str(original["id"]) if original else None,
        original_fair_value=original.get("fair_value") if original else None,
        codex_id=str(history[0]["id"]) if history else None,
        codex_analysis_version=history[0].get("analysis_version") if history else None,
        evidence_urls=[],
        weights={},
        recovered_evidence=[],
        accepted_evidence=[],
        rejected_evidence=[],
        identity_checks=[],
        fx=fx,
        source="system_market_evidence_and_codex_identity",
    )
    if not history:
        return result
    for snapshot in history:
        result["recovered_evidence"].extend(extract_evidence(snapshot))
    if original and original.get("method") in {"manual", "manual_override"}:
        result["reasons"].append("保留人工估值；历史证据仅供校验")
        return result
    item = dict(item)
    key = str(image_hashes.get("front")) + ":" + str(image_hashes.get("back"))
    review = (identity_reviews or {}).get(key) or {}
    result["identity_corrections"] = {}
    reviewed_at = _date(review.get("checked_at"))
    if reviewed_at and reviewed_at <= now and not item.get("_human_confirmed"):
        for field, value in review.get("fields", {}).items():
            if field in {
                "player_name",
                "season_start",
                "product_name",
                "insert_name",
                "parallel_name",
                "card_number",
                "serial_total",
                "is_autograph",
                "is_memorabilia",
            }:
                result["identity_corrections"][field] = dict(
                    before=item.get(field),
                    after=value,
                    reason=review.get("reason"),
                    checked_at=review["checked_at"],
                )
                item[field] = value
    target, check = reconcile_identity(item, history[0], image_hashes)
    result["identity_checks"].append(check)
    result["joint_identity"] = {
        k: target.get(k)
        for k in (
            "player_name",
            "season_start",
            "product_name",
            "card_number",
            "parallel_name",
            "insert_name",
            "serial_total",
            "grade_company",
            "is_autograph",
            "is_memorabilia",
        )
    }
    if not check["matched"]:
        result["status"] = "review_required"
        result["reasons"].extend(check["reasons"])
        return result
    eligible_snapshots = {str(history[0]["id"])}
    for snapshot in history[1:]:
        _, older = reconcile_identity(target, snapshot, image_hashes)
        result["identity_checks"].append(older)
        if older["matched"]:
            eligible_snapshots.add(str(snapshot["id"]))
    resolved = CardIdentityResolver().resolve(target)
    classifier = ComparableClassifier()
    sales = {}
    audits = {}
    contradictory_sales = set()
    # Recheck only previously published system evidence. No unreviewed cache promotion.
    prior_evidence = (original or {}).get("evidence") or {}
    used_ids = {
        str(x.get("market_comp_id") or x.get("id"))
        for key in ("used_comparables", "comparables")
        for x in prior_evidence.get(key, [])
        if isinstance(x, dict)
    }
    missing_ids = used_ids - {str(comp["id"]) for comp in market}
    for comp_id in sorted(missing_ids):
        result["rejected_evidence"].append(
            dict(source="system", id=comp_id, reasons=["原估值引用的成交记录缺失"])
        )
    for comp in market:
        if str(comp["id"]) not in used_ids:
            continue
        url = sale_url(comp.get("source_url"))
        date = _date(comp.get("occurred_at"))
        price = _base_price(comp.get("sale_price"), comp.get("currency"), base_currency, fx, now)
        payload = comp.get("raw_payload") or {}
        sale_state = str(payload.get("sale_status") or "").casefold()
        price_basis = str(payload.get("price_basis") or "").casefold()
        sale_invalid = (
            payload.get("actual_price_known") is False
            or (sale_state and sale_state not in {"sold", "ended_with_bid", "completed"})
            or (
                price_basis
                and price_basis
                not in {"fixed_price_sale", "auction_final_price", "displayed_sold_price"}
            )
        )
        classification = classifier.classify(
            resolved,
            ComparableInput(
                title=comp.get("listing_title") or "",
                grade_company=comp.get("grade_company"),
                grade_value=_number(comp.get("grade_value")),
            ),
        )
        if (
            sale_invalid
            or not url
            or not date
            or not 0 <= (now - date).days <= 365
            or price is None
            or classification.hard_conflicts
            or classification.tier not in {ComparableTier.T0, ComparableTier.T1}
        ):
            result["rejected_evidence"].append(
                dict(
                    source="system",
                    id=str(comp["id"]),
                    url=url,
                    reasons=(
                        ["成交状态或实际成交金额未确认"]
                        if sale_invalid
                        else classification.hard_conflicts or ["同版信息、日期或币种不足"]
                    ),
                )
            )
            continue
        if url in sales:
            prior_date = _date(audits[url].get("sold_at"))
            if (
                price != sales[url].sale_price_base
                or not prior_date
                or prior_date.date() != date.date()
            ):
                contradictory_sales.add(url)
                result["rejected_evidence"].append(
                    dict(
                        source="system",
                        url=url,
                        reasons=["同一成交在系统原记录中的金额或日期不一致"],
                    )
                )
            continue
        sales[url] = ClassifiedSale(url, price, Decimal(0), (now - date).days, classification)
        audits[url] = dict(
            source="system",
            url=url,
            price_base=price,
            tier=classification.tier.value,
            original_price=comp["sale_price"],
            currency=comp["currency"],
            sold_at=str(comp["occurred_at"]),
        )
    for e in result["recovered_evidence"]:
        if e["snapshot_id"] not in eligible_snapshots or e["kind"] != "reported_sale":
            continue
        review = reviews.get(e["url"]) or {}
        if (
            not review.get("sale_confirmed")
            or not review.get("actual_price_known")
            or not review.get("identity_confirmed")
        ):
            continue
        checked = _date(review.get("checked_at"))
        date = _date(review.get("sold_at"))
        if (
            not checked
            or not 0 <= (now - checked).days <= 90
            or not date
            or not 0 <= (now - date).days <= 365
        ):
            continue
        # Verification applies to a source version, not arbitrary cards sharing a URL.
        source_identity = review.get("identity") or {}
        required = ("player_name", "season_start", "product_name", "card_number", "parallel_name")
        mismatch = [
            k
            for k in required
            if source_identity.get(k) is None
            or target.get(k) is None
            or not same_value(k, target[k], source_identity[k])
        ]
        if (target.get("insert_name") or source_identity.get("insert_name")) and not same_value(
            "insert_name", target.get("insert_name"), source_identity.get("insert_name")
        ):
            mismatch.append("insert_name")
        for k in ("serial_total", "grade_company", "grade_value", "is_autograph", "is_memorabilia"):
            if target.get(k) != source_identity.get(k):
                mismatch.append(k)
        if mismatch:
            result["rejected_evidence"].append(
                dict(
                    source="codex",
                    url=e["url"],
                    reasons=["核验成交身份不一致：" + ",".join(mismatch)],
                )
            )
            continue
        price = _base_price(review.get("price"), review.get("currency"), base_currency, fx, now)
        if price is None:
            continue
        # Explicitly reviewed source fields were compared above; the sports-only
        # free-text classifier cannot parse non-sport character names reliably.
        classification = Classification(ComparableTier.T0, Decimal("1"), Decimal(".65"))
        url = e["url"]
        if url in sales:
            prior = audits[url]
            prior_date = _date(prior.get("sold_at"))
            if (
                price.quantize(Decimal(".01"))
                != sales[url].sale_price_base.quantize(Decimal(".01"))
                or not prior_date
                or prior_date.date() != date.date()
            ):
                contradictory_sales.add(url)
                result["rejected_evidence"].append(
                    dict(
                        source="joint",
                        url=url,
                        reasons=["同一成交在系统和复核来源中的金额或日期不一致"],
                        system_price_base=sales[url].sale_price_base,
                        reviewed_price_base=price,
                    )
                )
            else:
                prior["corroborated_by_codex"] = e["snapshot_id"]
            continue
        sales[url] = ClassifiedSale(url, price, Decimal(0), (now - date).days, classification)
        audits[url] = dict(
            source="codex_recovered",
            url=url,
            price_base=price,
            original_price=review["price"],
            currency=review["currency"],
            sold_at=review["sold_at"],
            checked_at=review["checked_at"],
            snapshot_id=e["snapshot_id"],
            verification_scope=review["scope"],
            tier=classification.tier.value,
        )
    for url in contradictory_sales:
        sales.pop(url, None)
        audits.pop(url, None)
    result["accepted_evidence"] = list(audits.values())
    result["evidence_urls"] = sorted(sales)
    if contradictory_sales:
        result["status"] = "review_required"
        result["reasons"].append("引用的同一成交存在金额或日期冲突，保留原值待复核")
        return result
    if not sales:
        if result["rejected_evidence"]:
            result["status"] = "review_required"
        result["reasons"].append("已提取历史信息，但没有通过联合身份与成交核验的价格样本")
        return result
    # If the evidence set and identity are unchanged, preserve the existing estimator result.
    recovered = any(e["source"] == "codex_recovered" for e in audits.values())
    if original and not recovered and not result["rejected_evidence"]:
        result["status"] = "joint_validated"
        result["reasons"].append("Codex 身份与原估值已采用成交一致，保留原估值")
        return result
    if original and not recovered and result["rejected_evidence"]:
        result["status"] = "review_required"
        result["reasons"].append(
            "原成交样本不能全部通过联合校验，保留原值并待复核，避免仅凭文字缺失改变价格"
        )
        return result
    estimate = HierarchicalEstimator().estimate(list(sales.values()))
    result.update(
        status="joint_recalculated",
        currency=base_currency,
        low_value=estimate.low,
        fair_value=estimate.fair,
        high_value=estimate.high,
        confidence=estimate.confidence,
    )
    result["reasons"].append("系统成交与 Codex 历史线索共同核验后重算；未平均两份模型报价")
    if len(sales) == 1:
        result["reasons"].append("仅一条独立成交，区间较宽；不代表已确认当前成交价")
    if recovered:
        result["reasons"].append("补核了历史链接的成交状态、金额与文字身份；实物品相仍有不确定性")
    fingerprint = json.dumps(result["joint_identity"], sort_keys=True, default=str)
    result["identity_fingerprint"] = hashlib.sha256(fingerprint.encode()).hexdigest()
    return result
