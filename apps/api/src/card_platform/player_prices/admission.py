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
from card_platform.player_prices.image_evidence import (
    admit_receipt,
    _read_bound_receipt,
    image_pair_key,
)
from card_platform.player_prices.image_reviews import admit_review
from card_platform.player_prices.photo_captures import read_bound_capture
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
    """Production evidence includes server photo peers, never a request body."""
    candidates, conflicts, invalid = source_candidates(observations)
    records = {(r["source_id"], r["observation_sha256"]): r for r in evidence}
    reasons, physical, rows = Counter(), Counter(), []
    photo_sources_in_scope = {"item:" + sale.item_id for _, sale, _ in candidates} | conflicts
    photo_records = [
        r
        for r in records.values()
        if r["source_id"] in photo_sources_in_scope or r.get("photo_context_only")
    ]
    if conflicts:
        reasons["conflicting_source"] = len(conflicts)
    if invalid:
        reasons["invalid_observation"] = invalid
    for obs, sale, _ in candidates:
        source = "item:" + sale.item_id
        record = records.get((source, obs["raw_row_sha256"]))
        if (
            record is None
            or record.get("receipt") is None
            or not record.get("price_eligible", True)
        ):
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
    # Distinct listing IDs do not establish independent evidence when they
    # reuse the exact same two photos. Do not arbitrarily pick a winning sale
    # from an unresolved cluster; keep the captures, exclude every member.
    photo_sources, uncertain, checked_peers = defaultdict(set), set(), {}

    def bound_photo(capture):
        key = (
            capture["source_id"],
            capture["observation_sha256"],
            capture.get("capture_manifest_sha256"),
            capture["receipt_sha256"],
        )
        if key not in checked_peers:
            try:
                if capture.get("capture_manifest_sha256"):
                    checked_peers[key] = read_bound_capture(capture, blob_reader)
                    return checked_peers[key]
                sale, _ = _read_bound_receipt(
                    capture["observation"], capture["receipt"], blob_reader
                )
                if (
                    capture["observation_sha256"] != capture["observation"]["raw_row_sha256"]
                    or capture["receipt_sha256"] != capture["receipt"]["receipt_sha256"]
                    or capture["source_id"] != "item:" + sale.item_id
                ):
                    raise ValueError("unbound_photo_peer")
                checked_peers[key] = image_pair_key(capture["receipt"])
            except Exception:
                # Unavailable old evidence cannot establish independence.
                checked_peers[key] = None
        return checked_peers[key]

    for row in rows:
        pair = tuple(sorted(row["identity"]["image_evidence"]["image_sha256"]))
        photo_sources[pair].add(row["source_id"])
    # Photo reuse is independently knowable even if today's candidate is
    # pending or fails a price-identity/condition gate. Its old valid captures
    # still cannot leave already published peer prices active.
    for record in photo_records:
        source = record["source_id"]
        for peer in record.get("photo_peers", []):
            if peer["source_id"] == source:
                continue
            anchor = peer.get("photo_anchor", record)
            anchor_pair, peer_pair = bound_photo(anchor), bound_photo(peer)
            if (
                anchor["source_id"] == source
                and anchor_pair is not None
                and anchor_pair == peer_pair
            ):
                photo_sources[anchor_pair].update([source, peer["source_id"]])
            else:
                uncertain.update([source, peer["source_id"]])
    reused_sources = {
        source for sources in photo_sources.values() if len(sources) > 1 for source in sources
    }
    independent = []
    for row in rows:
        if row["source_id"] in reused_sources:
            reasons["reused_photo_pair"] += 1
        elif row["source_id"] in uncertain:
            reasons["photo_independence_pending"] += 1
        else:
            independent.append(row)
    rows = independent
    return rows, dict(
        sample_count=len(candidates) + len(conflicts) + invalid,
        identified_count=len(rows),
        rejected_count=sum(reasons.values()),
        rejection_reasons=dict(reasons),
        image_rejection_reasons=dict(physical),
        photo_reuse_sources=sorted(reused_sources),
        photo_pending_sources=sorted(uncertain - reused_sources),
        evidence_policy=POLICY,
        catalog_version=catalog.version,
        catalog_errors=len(catalog.errors),
        catalog_available=catalog.version is not None,
    )
