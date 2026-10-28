"""Coverage of the verified published baseline, never of planned candidate additions."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from card_platform.knowledge_source_inputs import canonical, issue


def _counts(inputs, keys):
    keys = set(keys)
    missing = {row["product_source_key"] for row in inputs["baseline_artifact_issues"]
               if row["product_source_key"] in keys}
    missing.update(key for key in keys if key not in inputs["baseline_cards"])
    observed = sum(len(inputs["baseline_cards"].get(key, [])) for key in keys)
    assets, problems = defaultdict(list), []
    for key in sorted(keys):
        for record in inputs["products"][key]["attributes"].get("reference_images", []):
            if not record.get("asset_id"):
                problems.append(issue("image_asset_id_missing", product_source_key=key))
                continue
            assets[record["asset_id"]].append((key, record))
    image_types, pairs, hashes = Counter(), defaultdict(dict), set()
    for asset_id, records in sorted(assets.items()):
        digests = {r.get("sha256") for _, r in records if r.get("sha256")}
        hashes.update(digests)
        if len(digests) != 1:
            problems.append(issue("image_asset_hash_conflict", detail=asset_id))
            image_types["conflicted"] += 1
            continue
        types = {r.get("image_type", "unknown") for _, r in records}
        image_types[next(iter(types)) if len(types) == 1 else "conflicted"] += 1
        for key, record in records:
            if record.get("pair_id") and record.get("image_type") == "verified_card_scan":
                pairs[(key, record["pair_id"])][asset_id] = record
    declared_pairs = 0
    for faces_by_id in pairs.values():
        faces = list(faces_by_id.values())
        if (len(faces) == 2 and {f.get("side") for f in faces} == {"front", "back"}
                and len({f["sha256"] for f in faces}) == 2):
            declared_pairs += 1
    return {"known_product_count": len(keys),
            "annual_product_denominator": None, "annual_discovery_complete": False,
            "observed_verified_checklist_records": observed,
            "checklist_variant_records": None if missing else observed,
            "missing_artifact_count": len(missing), "distinct_base_cards": None,
            "reference_image_records": len(assets), "unique_image_hashes": len(hashes),
            "image_type_counts": dict(sorted(image_types.items())),
            "declared_specimen_pair_records": declared_pairs,
            "freshly_verified_specimen_pairs": None,
            "full_card_image_denominator": None, "full_card_image_percent": None}, problems


def build_coverage(inputs: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    known_keys = [key for key, product in inputs["products"].items()
                  if product["attributes"].get("manufacturer") in {"Topps", "Panini"}]
    scope_keys = inputs["scope"]["existing_product_keys"]
    known, problems = _counts(inputs, known_keys)
    scoped, _ = _counts(inputs, scope_keys)
    scoped["work_scope_product_count"] = len(scope_keys)
    groups = defaultdict(list)
    for key in known_keys:
        attrs = inputs["products"][key]["attributes"]
        groups[(attrs.get("season_start"), attrs.get("manufacturer"))].append(key)
    grouped = []
    for (season, manufacturer), keys in sorted(groups.items(), key=lambda p: canonical(p[0])):
        all_counts, _ = _counts(inputs, keys)
        work_counts, _ = _counts(inputs, set(keys) & set(scope_keys))
        grouped.append({"season_start": season, "manufacturer": manufacturer,
                        "known_baseline": all_counts, "work_scope": work_counts})
    registry = inputs["baseline"]["coverage_registry"]
    return {"contract": "knowledge-coverage-report-v1", "as_of": inputs["baseline"]["captured_at"],
            "known_baseline": known, "work_scope": scoped, "by_season_manufacturer": grouped,
            "annual_research_unit_count": len(inputs["scope"]["annual_research_keys"]),
            "coverage_registry": {"status": registry["status"], "row_count": len(registry["rows"])},
            "source_rows_available": inputs["baseline"].get("sources") is not None,
            "workflow": {"search_evidence_not_provided": len(scope_keys),
                         "review_needed_candidates": len(plan["review_queue"]),
                         "source_blocked": sum(s["status"] == "blocked" for s in plan["source_actions"]),
                         "external_blocked": 0},
            "issues": sorted([*inputs["baseline_artifact_issues"], *problems], key=canonical)}


def render_report(plan: dict[str, Any], coverage: dict[str, Any]) -> str:
    counts = coverage["known_baseline"]
    total = counts["checklist_variant_records"]
    lines = ["# 卡牌知识只读增量报告", "", "**只读计划，不是录入完成。**",
             "候选未审核、未发布；没有写入生产，也没有自动合并卡款。", "",
             f"- 输入时间：{coverage['as_of']}",
             f"- 发布基线：v{plan['baseline']['published']['version']}",
             f"- 已知基线产品：{counts['known_product_count']}；本批范围：{coverage['work_scope']['work_scope_product_count']}",
             f"- 已校验目录版本行：{counts['observed_verified_checklist_records']}；已知基线总行数：{total if total is not None else '未知'}",
             f"- 缺失或未通过校验的产品 artifact：{counts['missing_artifact_count']}",
             f"- 参考图片记录：{counts['reference_image_records']}；明确声明的实物双面样本：{counts['declared_specimen_pair_records']}",
             "- 年度总产品分母、跨源基础卡总数及全卡图片覆盖率：未知。",
             f"- 待审候选：{len(plan['review_queue'])}；模型调用：0。",
             "- 本次未提供真实搜索审计，不等于历史从未搜索，也不等于全网不存在。", "",
             "## 缺口与恢复动作", ""]
    problems = {canonical(p): p for p in [*plan["issues"], *coverage["issues"]]}
    for _, problem in sorted(problems.items()):
        lines.append(f"- `{problem['code']}`（{problem.get('product_source_key') or '全局'}）：{problem['recovery_action']}")
    lines.extend(["", "发布仍需 focused-v2 的完整年度基线、原始来源、逐目标真实三路线搜索、文本/来源/图片审核、事实映射与封存材料。",
                  "本报告不替代发布闸门，不是 release bundle。", ""])
    return "\n".join(lines)
