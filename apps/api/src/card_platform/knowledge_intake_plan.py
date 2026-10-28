"""Pure, provenance-preserving plans. Nothing here approves or publishes knowledge."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from card_platform.knowledge_source_inputs import (
    ADAPTER_VERSIONS,
    COMPILER_VERSION,
    PARSER_VERSION,
    canonical,
    issue,
)
from card_platform.knowledge_text import FACT_FIELDS, compare_card_facts

FOCUSED_V2_REQUIRED_INPUTS = [
    "complete_annual_baseline_capture", "frozen_work_scope", "owning_source_rows",
    "per_target_real_three_route_searches", "text_source_image_review",
    "source_fact_audit", "fact_publication_map", "sealed_files",
    "baseline_revision_and_checksum", "preserve_entities_sources_media_indexes_and_assertions",
    "coverage_empty_in_release_manifest",
]


def normalize_url(value: str | None) -> str | None:
    if not value:
        return value
    parts = urlsplit(value)
    # Do not collapse query parameters, URL paths, or aliases.
    host = parts.hostname
    if not host:
        return value.split("#", 1)[0]
    netloc = parts.netloc
    start = netloc.rfind("@") + 1
    netloc = netloc[:start] + netloc[start:].replace(host, host.lower(), 1)
    # urlsplit.hostname already lowercases; preserve port while lowering host.
    if start == 0:
        netloc = netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def candidate_key(candidate: dict[str, Any]) -> str:
    identity = {k: candidate.get(k) for k in
                ("product_source_key", "subject_key", "source_sha256", "field", "value")}
    identity["source_url"] = normalize_url(candidate.get("source_url"))
    return sha256(canonical(identity).encode()).hexdigest()


def _row_key(card, *, semantic=False):
    attrs = card["attributes"]
    values = [attrs.get(k) for k in ("section", "card_number", "player", "source_variant_label")]
    if semantic and isinstance(values[2], str) and "/" in values[2]:
        values[2] = sorted(part.strip() for part in values[2].split("/"))
    return canonical(values)


def _card_subject(key, card):
    return "card:" + key + ":" + sha256(_row_key(card).encode()).hexdigest()


def _candidate(source, fact, subject, locators):
    candidate = {k: source.get(k) for k in
                 ("product_source_key", "source_sha256", "adapter_version")}
    candidate.update(subject_key=subject, field=fact["field"], value=deepcopy(fact["value"]),
                     source_url=normalize_url(source.get("source_url")),
                     locators=deepcopy(locators), base_card_id=fact.get("base_card_id"),
                     variant_id=fact.get("variant_id"), physical_card_id=fact.get("physical_card_id"))
    candidate["candidate_id"] = candidate_key(candidate)
    for field in ("authority", "site", "capture_kind", "observed_at", "limits", "json_pointer"):
        if field in fact:
            candidate[field] = deepcopy(fact[field])
    candidate["provenance"] = [{k: deepcopy(fact[k]) for k in
                                ("authority", "site", "capture_kind", "observed_at", "limits", "json_pointer")
                                if k in fact}]
    candidate["reasons"] = [fact["review_reason"]] if fact.get("review_reason") else []
    if not locators:
        candidate["reasons"].append("missing_source_locator")
    return candidate


def _card_candidates(source, cards):
    result = []
    groups = defaultdict(list)
    for card in cards:
        groups[_row_key(card)].append(card)
    conflicting = {k for k, rows in groups.items() if len({canonical({f: r["attributes"].get(f)
                    for f in FACT_FIELDS}) for r in rows}) > 1}
    for card in cards:
        attrs = card["attributes"]
        locators = attrs.get("source_locators") or ([attrs["source_locator"]]
                                                   if attrs.get("source_locator") else [])
        for field in FACT_FIELDS:
            if field not in attrs:
                continue
            fact = {"field": field, "value": attrs[field],
                    **{k: attrs.get(k) for k in ("base_card_id", "variant_id", "physical_card_id")}}
            if _row_key(card) in conflicting or card.get("review_reason"):
                fact["review_reason"] = "conflicting_official_row"
            candidate = _candidate(source, fact, _card_subject(source["product_source_key"], card), locators)
            candidate["explicit_variant_record_id"] = card.get("id")
            result.append(candidate)
    return result


def _baseline_candidates(inputs):
    result = []
    for key, product in inputs["products"].items():
        attrs = product["attributes"]
        for evidence in attrs.get("supplementary_evidence", []):
            source = {"product_source_key": key, "source_url": evidence.get("source_url"),
                      "source_sha256": evidence.get("source_sha256"), "adapter_version": None}
            for fact in evidence.get("facts", []):
                if not isinstance(fact, dict) or "field" not in fact or "value" not in fact:
                    continue
                locator = fact.get("source_locator") or fact.get("locator")
                result.append(_candidate(source, fact, fact.get("subject_key") or "product:" + key,
                                         [locator] if locator else []))
        for card in inputs["baseline_cards"].get(key, []):
            ca = card["attributes"]
            source = {"product_source_key": key, "source_url": ca.get("source_url") or attrs.get("source_url"),
                      "source_sha256": ca.get("source_checksum"), "adapter_version": ca.get("compiler_version")}
            result.extend(_card_candidates(source, [card]))
    return result


def _provenance_complete(candidate):
    digest = candidate.get("source_sha256")
    return (bool(candidate.get("source_url")) and isinstance(digest, str) and len(digest) == 64
            and bool(candidate.get("locators")))


def _group_key(candidate):
    return (candidate["product_source_key"], candidate["subject_key"], candidate["field"])


def _unique(values):
    return [value for _, value in sorted({canonical(v): v for v in values}.items())]


def _source_action(inputs, extraction):
    key = extraction["product_source_key"]
    attrs = inputs["products"][key]["attributes"]
    url = normalize_url(extraction.get("source_url"))
    hashes, known_without_hash = set(), False
    for row in inputs["baseline"].get("sources") or []:
        metadata = row.get("metadata", {})
        if (row.get("source_key", metadata.get("source_key")) == key
                and normalize_url(row.get("source_url", row.get("url"))) == url):
            if row.get("checksum"):
                hashes.add(row["checksum"])
            else:
                known_without_hash = True
    for evidence in attrs.get("supplementary_evidence", []):
        if normalize_url(evidence.get("source_url")) == url:
            if evidence.get("source_sha256"):
                hashes.add(evidence["source_sha256"])
            else:
                known_without_hash = True
    if extraction.get("adapter") == "official_document_v1":
        if attrs.get("source_checksum"):
            hashes.add(attrs["source_checksum"])
        for card in inputs["baseline_cards"].get(key, []):
            if card["attributes"].get("source_checksum"):
                hashes.add(card["attributes"]["source_checksum"])
        known_without_hash = known_without_hash or not hashes
    reasons = []
    if extraction["status"] == "blocked":
        status = "blocked"
        reasons = [i["code"] for i in extraction["issues"]]
    elif known_without_hash:
        status = "blocked"
        reasons = ["baseline_source_provenance_incomplete"]
    elif extraction.get("source_sha256") in hashes:
        status = "unchanged"
    elif hashes:
        status = "changed"
    else:
        status = "new"
    action = {k: extraction.get(k) for k in
              ("input_id", "product_source_key", "source_url", "source_sha256", "adapter_version")}
    action.update(status=status, reasons=sorted(set(reasons)), semantic_delta=None,
                  source_representation_delta=None)
    if extraction.get("adapter") == "official_document_v1" and extraction["status"] == "ready":
        old = inputs["baseline_cards"].get(key)
        if old is not None:
            # Guard BOTH raw and normalized identities before the existing dict-based comparator.
            ambiguous = any(len({_row_key(c, semantic=semantic) for c in rows}) != len(rows)
                            for semantic in (True, False) for rows in (old, extraction["cards"]))
            if ambiguous:
                action["reasons"] = sorted({*action["reasons"], "ambiguous_official_row_identity"})
            else:
                action["semantic_delta"] = compare_card_facts(extraction["cards"], old)
                action["source_representation_delta"] = compare_card_facts(
                    extraction["cards"], old, normalize_participant_order=False)
    return action


def build_plan(inputs: dict[str, Any], extractions: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for source in extractions:
        official = _card_candidates(source, source["cards"])
        if source["cards"] and source["product_source_key"] not in inputs["baseline_cards"]:
            for candidate in official:
                candidate["reasons"].append("baseline_artifact_unavailable")
        candidates.extend(official)
        for fact in source["facts"]:
            locator = fact.get("locator") or fact.get("source_locator")
            candidates.append(_candidate(source, fact, fact.get("subject_key") or
                                         "product:" + source["product_source_key"],
                                         [locator] if locator else []))
    merged = {}
    for candidate in sorted(candidates, key=canonical):
        identity = candidate["candidate_id"]
        if identity not in merged:
            merged[identity] = candidate
        else:
            old = merged[identity]
            old["locators"] = _unique([*old["locators"], *candidate["locators"]])
            old["provenance"] = _unique([*old["provenance"], *candidate["provenance"]])
            old["reasons"] = sorted({*old["reasons"], *candidate["reasons"]})
    existing = defaultdict(list)
    for old in _baseline_candidates(inputs):
        existing[_group_key(old)].append(old)
    # Legacy product values without a provenance record are not fresh discoveries.
    for candidate in merged.values():
        key = candidate["product_source_key"]
        attrs = inputs["products"][key]["attributes"]
        if (candidate["subject_key"] == "product:" + key and candidate["field"] in attrs
                and not existing[_group_key(candidate)]):
            old = _candidate({"product_source_key": key},
                             {"field": candidate["field"], "value": attrs[candidate["field"]]},
                             candidate["subject_key"], [])
            existing[_group_key(candidate)].append(old)
    incoming = defaultdict(list)
    for candidate in merged.values():
        incoming[_group_key(candidate)].append(candidate)
    decisions = []
    for candidate in sorted(merged.values(), key=lambda c: c["candidate_id"]):
        old = existing[_group_key(candidate)]
        peers = [c for c in incoming[_group_key(candidate)] if c["candidate_id"] != candidate["candidate_id"]]
        reasons = set(candidate.pop("reasons"))
        same = [c for c in old if canonical(c["value"]) == canonical(candidate["value"])]
        duplicate = any(candidate_key(c) == candidate["candidate_id"] and _provenance_complete(c) for c in same)
        if any(not _provenance_complete(c) for c in old):
            reasons.add("baseline_provenance_incomplete")
        differs = any(canonical(c["value"]) != canonical(candidate["value"])
                      for c in [*old, *peers])
        if differs and not duplicate:
            coarse = candidate["subject_key"].startswith("product:") and isinstance(candidate["value"], (dict, list))
            reasons.add("coarse_product_claim_requires_review" if coarse else "conflict")
        if reasons:
            status = "review_needed"
        elif duplicate:
            status = "duplicate"
        elif same or any(canonical(c["value"]) == canonical(candidate["value"]) for c in peers):
            status = "corroborating"
        else:
            status = "new_candidate"
        decisions.append({**candidate, "status": status, "reasons": sorted(reasons),
                          "baseline_evidence": _unique(old),
                          "related_candidate_ids": sorted(c["candidate_id"] for c in peers)})
    actions = sorted((_source_action(inputs, e) for e in extractions),
                     key=lambda a: (a["product_source_key"], a["input_id"]))
    problems = [*inputs["baseline_artifact_issues"],
                *(problem for e in extractions for problem in e["issues"])]
    for action in actions:
        for reason in action["reasons"]:
            problems.append(issue(reason, input_id=action["input_id"],
                                  product_source_key=action["product_source_key"]))
    return {"contract": "knowledge-intake-plan-v1", "baseline": {
                "captured_at": inputs["baseline"]["captured_at"],
                "workspace_revision": inputs["baseline"]["workspace_revision"],
                "published": deepcopy(inputs["baseline"]["published"])},
            "input_hashes": dict(sorted(inputs["input_hashes"].items())),
            "versions": {"parser": PARSER_VERSION, "compiler": COMPILER_VERSION,
                         "adapters": dict(ADAPTER_VERSIONS)},
            "work_scope": {**{k: sorted(v) if isinstance(v, list) else v for k, v in inputs["scope"].items()},
                           "frozen_products": [{f: inputs["products"][k]["attributes"].get(f) for f in
                                                ("source_key", "manufacturer", "product", "sport", "season_start", "season_end")}
                                               for k in sorted(inputs["scope"]["existing_product_keys"])]},
            "source_actions": actions, "fact_decisions": decisions, "issues": _unique(problems),
            "review_queue": [{"candidate_id": c["candidate_id"], "reasons": c["reasons"]}
                             for c in decisions if c["status"] == "review_needed"],
            "llm_calls": 0, "publishable": False,
            "focused_v2_required_inputs": list(FOCUSED_V2_REQUIRED_INPUTS)}
