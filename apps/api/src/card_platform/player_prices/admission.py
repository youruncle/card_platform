"""Price admission from current server evidence, separate from query candidates."""

from collections import Counter, defaultdict
from functools import lru_cache
import re
import json

from card_platform.player_prices.discovery import card_key
from card_platform.domain.listing_title import is_multi_card_listing
from card_platform.domain.nba_player_catalog import nba_player_manifest
from card_platform.domain.player_aliases import matching_player_aliases
from card_platform.player_prices.subjects import canonical_subject_name
from card_platform.player_prices.image_evidence import admit_receipt
from card_platform.player_prices.image_reviews import admit_review
from card_platform.public_source_catalog import validated_sale

POLICY = "price-image-admission-v1"
SEALED_PRODUCT = re.compile(
    r"单包|散包|随机发|原盒(?!直出)|一盒\s*[\d一二三四五六七八九十]+\s*包|一包\s*[\d一二三四五六七八九十]+\s*张"
)


def source_candidates(observations):
    """Deduplicate facts before queueing; never spend on conflicting captures."""
    grouped, invalid = {}, set()
    for obs in observations:
        try:
            sale, captured = validated_sale(obs)
        except (ValueError, KeyError, TypeError):
            invalid.add(json.dumps(obs, sort_keys=True, default=str))
            continue
        grouped.setdefault("item:" + sale.item_id, []).append((obs, sale, captured))
    chosen, conflicts = [], set()
    for source, entries in grouped.items():
        if len({(sale.title, sale.sale_price, sale.occurred_at) for _, sale, _ in entries}) > 1:
            conflicts.add(source)
        else:
            chosen.append(max(entries, key=lambda entry: entry[2]))
    return chosen, conflicts, len(invalid)


@lru_cache(maxsize=1)
def _subject_mentions():
    names = defaultdict(set)
    for entity in nba_player_manifest()["entities"]:
        if entity.get("review_status") != "approved":
            continue
        name = entity["canonical_name"]
        for alias in (name, *matching_player_aliases(name.casefold())):
            names[alias.casefold()].add(name)
    # Only compare aliases whose first word/character occurs in the title.
    # A single alternation over the full historical NBA vocabulary scans tens
    # of thousands of unrelated aliases at every title position.
    groups = defaultdict(list)
    for alias in sorted(names, key=len, reverse=True):
        token = re.match(r"[a-z0-9]+", alias)
        key = token.group() if token else alias[0]
        expression = (
            r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            if re.search("[a-z0-9]", alias)
            else re.escape(alias)
        )
        groups[key].append(expression)
    return {key: re.compile("|".join(values)) for key, values in groups.items()}, names


def queue_candidates(candidates, player_name):
    """Title gates only avoid unsafe jobs; they never establish card identity."""
    target = canonical_subject_name(player_name)
    patterns, names = _subject_mentions()
    result = []
    for obs, sale, captured in candidates:
        if is_multi_card_listing(sale.title) or SEALED_PRODUCT.search(sale.title):
            continue
        title = sale.title.casefold()
        tokens = set(re.findall(r"(?<![a-z0-9])[a-z0-9]+|[^a-z0-9\s]", title))
        matches = []
        for token in tokens:
            pattern = patterns.get(token)
            if pattern is not None:
                matches.extend(pattern.finditer(title))
        # Preserve the original global leftmost/longest, non-overlapping
        # semantics: Bronny's 小勒布朗詹姆斯 must not also name his father.
        mentioned, consumed = set(), -1
        for match in sorted(matches, key=lambda m: (m.start(), -(m.end() - m.start()))):
            if match.start() < consumed:
                continue
            mentioned.update(names[match.group()])
            consumed = match.end()
        if mentioned - {target}:
            continue
        result.append(obs)
    return result


def verified_price_rows(observations, player_name, catalog, *, evidence, blob_reader):
    """Evidence must come from ImageJobs.evidence, never a request body."""
    candidates, conflicts, invalid = source_candidates(observations)
    records = {(r["source_id"], r["observation_sha256"]): r for r in evidence}
    reasons, physical, rows = Counter(), Counter(), []
    if conflicts:
        reasons["conflicting_source"] = len(conflicts)
    if invalid:
        reasons["invalid_observation"] = invalid
    for obs, sale, _ in candidates:
        source = "item:" + sale.item_id
        record = records.get((source, obs["raw_row_sha256"]))
        if record is None:
            reasons["image_evidence_pending"] += 1
            continue
        approved = record["approved_review_sha256s"]
        if approved:
            # Latest approved correction is authoritative. Never silently fall
            # back to an older correction or uncorrected model observation.
            decision = admit_review(
                obs,
                record["receipt"],
                player_name,
                catalog,
                review_sha256=approved[-1],
                approved_review_sha256s=set(approved),
                blob_reader=blob_reader,
            )
        else:
            decision = admit_receipt(
                obs, record["receipt"], player_name, catalog, blob_reader=blob_reader
            )
        if decision["status"] != "confirmed":
            reasons["image_evidence_rejected"] += 1
            physical.update(decision["reason_codes"])
            continue
        identity = decision["identity"]
        rows.append(
            dict(
                source_id=source,
                card_key=card_key(identity),
                identity=identity,
                sale_price=sale.sale_price,
                occurred_at=sale.occurred_at,
                source_url=sale.source_url,
                raw_title=sale.title,
            )
        )
    return rows, dict(
        sample_count=len(candidates) + len(conflicts) + invalid,
        identified_count=len(rows),
        rejected_count=sum(reasons.values()),
        rejection_reasons=dict(reasons),
        image_rejection_reasons=dict(physical),
        evidence_policy=POLICY,
        catalog_version=catalog.version,
        catalog_errors=len(catalog.errors),
        catalog_available=catalog.version is not None,
    )
