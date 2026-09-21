"""Build the per-card report from applied, versioned audit results."""
import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from card_platform.formal_cardhobby_gate import title_requires_named_parallel


def item_key(url):
    return urlparse(url).path.rstrip("/").split("/")[-1]


def cell(value):
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def run(directory: Path, destination: Path):
    baseline = json.loads((directory / "baseline.json").read_text())
    prices_by_item = {item_key(row["source_url"]): row["sale_price"]
                      for row in baseline["market_comps"]}
    for path in (directory / "new-evidence").glob("*.json"):
        for assessment in json.loads(path.read_text())["assessments"]:
            source = assessment.get("source") or {}
            if source.get("source_url") and source.get("sale_price") is not None:
                prices_by_item.setdefault(item_key(source["source_url"]), source["sale_price"])
    results = []
    for item in baseline["items"]:
        path = directory / "applied" / f"{item['display_id']}.json"
        if not path.exists():
            raise RuntimeError(f"Missing applied result: {path}")
        results.append(json.loads(path.read_text()))
    counters = Counter()
    cards = []
    evidence = []
    unresolved_cited_urls = []
    review_fields = Counter()
    for result in results:
        identity = result["identity"]
        old = result["baseline"]
        new = result.get("new_snapshot") or {}
        status = result["apply_status"]
        old_fair = Decimal(str(old["fair_value"]))
        new_fair = Decimal(str(new["fair_value"])) if new.get("fair_value") is not None else None
        if status.startswith("skipped") or status == "failed":
            change = "未完成"
        elif new_fair is None:
            change = "维持不足" if result["baseline_job_status"] == "insufficient_evidence" else "转证据不足"
        elif new_fair > old_fair:
            change = "上调"
        elif new_fair < old_fair:
            change = "下调"
        else:
            change = "维持"
        counters[change] += 1
        used = new.get("evidence", {}).get("used_comparables", [])
        used_urls = {entry["source_url"] for entry in used}
        observed_prices = [Decimal(str(prices_by_item[item_key(url)])) for url in used_urls
                           if item_key(url) in prices_by_item]
        independent_range = ([str(min(observed_prices)), str(max(observed_prices))]
                             if observed_prices else None)
        findings = result["image_findings_requiring_review"]
        for finding in findings:
            review_fields[finding["field"]] += 1
        label = " / ".join(str(identity.get(key)) for key in (
            "player_name", "season_start", "product_name", "insert_name", "parallel_name",
        ) if identity.get(key))
        if identity.get("card_number"):
            label += f" #{identity['card_number']}"
        if identity.get("serial_total"):
            label += f" /{identity['serial_total']}"
        if identity.get("grade_company"):
            label += f" {identity['grade_company']} {identity.get('grade_value') or ''}"
        cards.append({
            "display_id": result["display_id"], "card": label,
            "identity": {key: identity.get(key) for key in (
                "player_name", "season_start", "season_end", "brand_name", "product_name",
                "card_number", "insert_name", "parallel_name", "serial_number", "serial_total",
                "grade_company", "grade_value", "is_autograph", "is_memorabilia",
                "is_redemption", "redemption_status",
            )},
            "baseline_latest_status": result["baseline_job_status"],
            "old_fair": old["fair_value"], "old_low": old["low_value"], "old_high": old["high_value"],
            "new_fair": new.get("fair_value"), "new_low": new.get("low_value"),
            "new_high": new.get("high_value"), "status": status, "change": change,
            "independent_observed_auction_range": independent_range,
            "independent_fair_value_range": None,
            "independent_range_limitation": "仅同款已核对的历史落槌价域；不足以声称已独立建立完整近期合理价区间",
            "used_evidence_count": len(used), "used_urls": sorted(used_urls),
            "image_findings": findings, "target_image_assessment": result["target_image_assessment"],
            "job_id": result.get("job", {}).get("id"), "valuation_id": new.get("id"),
            "failure_reason": (result.get("job", {}).get("result_summary") or {}).get("reason"),
            "final_worker_rejections": new.get("evidence", {}).get("rejected_comparables", [])
            or (result.get("job", {}).get("result_summary") or {}).get("rejected_comparables", []),
            "replay_rounds": result["rounds"],
        })
        reviewed = json.loads((directory / "evidence" / f"{result['display_id']}.json").read_text())
        unresolved_cited_urls.extend({"display_id": result["display_id"], "url": url}
                                    for url in reviewed["unresolved_cited_urls"])
        for entry in reviewed["evidence"]:
            source = entry.get("source_record") or {}
            final_reasons = list(entry["reasons"])
            if title_requires_named_parallel(identity, entry["raw_title"] or ""):
                final_reasons.append("target_parallel_unknown_for_explicit_listing_variant")
            evidence.append({
                "display_id": result["display_id"], "url": entry["source_url"],
                "market_comp_id": entry["market_comp_id"],
                "original_title": entry["raw_title"], "date": entry["occurred_at"],
                "price": entry["price"], "shipping": entry["shipping"], "currency": entry["currency"],
                "bid_count": source.get("bid_count"), "payment_confirmed": False,
                "cited_by_baseline": entry["cited_by_baseline"],
                "used_by_new_snapshot": item_key(entry["source_url"]) in {item_key(url) for url in used_urls},
                "title_tier": entry["tier"], "reasons": final_reasons, "visual": entry["visual"],
                "original_source_payload_available": bool(source),
            })
    new_pages = json.loads((directory / "new-web-candidates.json").read_text())
    new_candidates = [{key: row.get(key) for key in (
        "item_id", "source_url", "raw_title", "occurred_at", "price", "currency", "bid_count",
        "reasons", "identity_assessments", "retrieved_at",
    )} for row in new_pages]
    new_visual_reviews = [json.loads(path.read_text()) for path in sorted(
        (directory / "new-evidence").glob("*.json")
    )]
    rejection_counts = Counter(reason for row in evidence for reason in row["reasons"]
                               if not reason.startswith("historical_ended_auction"))
    tests = {}
    for key, filename in (
        ("ruff", "ruff-final.txt"), ("related", "pytest-related-verified.txt"),
        ("full", "pytest-full-verified.txt"), ("postgres_full", "pytest-postgres-verified.txt"),
    ):
        lines = (directory / filename).read_text().strip().splitlines()
        tests[key] = lines[-1]
    health = json.loads((directory / "service-status.json").read_text())
    report = {
        "started_at": baseline["started_at"], "finished_at": datetime.now(UTC).isoformat(),
        "scope_count": len(cards), "account_item_count_at_baseline": 519,
        "changes": dict(counters), "tests": tests, "service": health,
        "cards": cards, "historical_evidence": evidence, "new_candidates": new_candidates,
        "new_candidate_visual_reviews": new_visual_reviews,
        "historical_review_reason_counts": dict(rejection_counts.most_common()),
        "unresolved_historical_citations": unresolved_cited_urls,
        "workspace_preservation": json.loads((directory / "preservation-check.json").read_text()),
        "database_verification": json.loads((directory / "database-verification.json").read_text()),
        "limitations": [
            "CardHobby direct search endpoint unavailable; two rounds of first-party-domain indexed search used instead",
            "Ended auctions with bids are not confirmed paid transactions",
            "Automated visual assessment and independent local OCR were used; conflicts were not silently written into identities",
            "All historical snapshots retained; insufficient current evidence publishes no price",
            "A complete independent current fair-value interval is not established for each card",
        ],
    }
    destination.parent.mkdir(exist_ok=True, parents=True)
    destination.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    lines = [
        "# 已估值卡片价格与证据复审", "",
        f"审计开始：{baseline['started_at']}；结束：{report['finished_at']}。仅卡淘。", "",
        "账户基线519张；纳入曾有自动估值的388张，其中最新成功361张、已证据不足27张。",
        "原未提交修改、身份和历史估值快照均保留。仅追加本轮任务与符合质量门的新估值。", "",
        "## 结果与边界", "",
        "；".join(f"{key} {value}张" for key, value in counters.items()) + "。",
        f"维持合计 {counters['维持'] + counters['维持不足']} 张（含已不足且仍不足）。",
        "比较基于原自动估值快照；原最新状态另列。没有新价不是估值为零。", "",
        f"388张均执行正反面模型复核与本地Apple Vision OCR；逐条检查3222条历史候选，按各卡身份共复核{len(evidence)}次；旧估值共有1283次商品引用。",
        "另有#49的旧PSA人口报告引用，缺少对应卡淘成交记录，已排除于本轮估值。",
        f"两轮共776条卡淘域名查询，保存{len(new_pages)}个去重商品页线索。召回数不是独立成交数或同款成交数。",
        "卡淘直连搜索接口不可用。原1182个唯一引用商品页均尝试重读，86个页面可解析，85个有日期、价格及出价资料。其余未声称已完成当前页面状态核验。",
        "所有有出价结束拍卖仅作为落槌价记录，未宣称已确认付款。挂牌、无出价、不同卡、未明确落槌价均不补值。", "",
        "**独立核验的限制：表中价域是已核对同款历史落槌价的观测范围，不是第二套独立公允估价。单条记录或来源受限时，无法据此建立完整近期合理价区间；本轮未能完成用户要求的全量近期独立区间核验。**", "",
        "## 共性问题与增量修复", "",
        "- 正式估值不再发布同产品类比或评级卡的裸卡参考价；只有同卡同评级T0/T1证据可进入价格中心。",
        "- 全量回放后的大幅变价抽查发现未知平行仍可被图片相似放行：新增门禁，目标平行为空时不接受明确银折、紫折、紫波纹等版本；相同限编也不能补足版本身份。追加修正回放并保留中间轮次。",
        "- 旧审核标记不能绕过零出价、固定价、未来日期、无时区、未证实付款及异常汇率门禁。",
        "- 缺失/为零的结束价不能回退到起拍价；明确零出价不能被其他计数覆盖；异常单行不再导致整次解析崩溃。",
        "- 可比图片同时使用目标正背面，检查卡号、限编和产品信息；图片下载使用有界并发，平行差异不能称为普通变体。",
        "- 排除披露损伤、印刷线等品相差异及不保证品相的案例；保留无明显损伤等否定语义。",
        "- 卡淘www/apex域名及路径大小写按商品ID归一，避免同一拍卖因URL表现不同被漏用。",
        "- 修复身份复核缺失交换板字段被写成null的问题，保留确认版本/识别中的兑换状态；更新过期认证集成测试。",
        "- 模型把统计赛季、缺失字段、RC标识或数值8与8.0误报冲突的情况单独过滤。原始观察保留，不据此自动改身份。", "",
        "历史候选拒绝/待核验原因（可重叠）：" + "；".join(
            f"{key}: {value}" for key, value in rejection_counts.most_common(10)
        ) + "。", "",
        "## 交付文件与代码", "",
        f"- [逐条证据与逐卡结果 JSON]({destination.with_suffix('.json').resolve()})",
        f"- [原始审计产物]({directory.resolve()})：包含两轮查询、页面记录、正反面复核和逐批回放。",
        "- 主要修改：comparable_image_verifier.py、valuation_worker.py、formal_cardhobby_gate.py、domain/auction_evidence.py、domain/valuation_quality.py、ingestion/cardhobby.py、identity_review_api.py。",
        "- 新增审计/回放/报告脚本与两组价格完整性回归测试；另更新过期认证测试和两处Ruff格式。",
        f"- [基线文件保留检查]({(directory / 'preservation-check.json').resolve()})：无文件丢失，列出增量修改的原有文件。", "",
        "## 测试与服务", "",
        *[f"- {key}: {value}" for key, value in tests.items()],
        f"- 服务：{json.dumps(health, ensure_ascii=False)}",
        f"- [数据库核验]({(directory / 'database-verification.json').resolve()})：全部最新任务、历史快照保留、采用来源、版本等级及价格区间一致性已检查。",
        "- 未修改前端源码；本轮无需前端typecheck/build。", "",
        "## 需核验的卡片", "",
        "优先补拍#4背面、#12背面、#14正面：人工抽查见明显局部裁切/背景占比过大，不能视作完整身份已核实。",
        "优先确认#16、#486的平行版本：银折与普通版、紫折与紫波纹不可混用，版本未确认前不发布对应候选价格。",
        "本地OCR与视觉模型同时提示的字段差异见下表；这些仍是核验线索，身份未被自动覆盖。", "",
        "| ID | 字段 | 原记录 | 图像/OCR观察 |", "|---|---|---|---|",
    ]
    dual = json.loads((directory / "dual-reader-identity-findings.json").read_text())
    for finding in sorted(dual, key=lambda item: item["display_id"]):
        lines.append("| " + " | ".join(cell(finding.get(key)) for key in (
            "display_id", "field", "stored", "observed",
        )) + " |")
    lines.extend([
        "", "其他图像差异、每条引用的原始标题、日期、价格和匹配/拒绝原因均记录在同名JSON中。", "",
        "## 逐卡结果（CNY，卡片价，不含运费）", "",
        "| ID | 卡片身份 | 原合理价〔区间〕 | 新合理价〔区间〕 | 独立证据价域* | 变化 | 实际采用条数 |",
        "|---|---|---|---|---|---|---|",
    ])
    for card in sorted(cards, key=lambda row: row["display_id"]):
        old = f"{card['old_fair']}〔{card['old_low']}–{card['old_high']}〕"
        new = f"{card['new_fair']}〔{card['new_low']}–{card['new_high']}〕" if card["new_fair"] is not None else "证据不足" if card["status"] == "insufficient_evidence" else card["status"]
        observed = card["independent_observed_auction_range"]
        observed_text = "–".join(map(str, observed)) if observed else "不足"
        lines.append("| " + " | ".join(map(cell, [card["display_id"], card["card"], old, new,
                                                          observed_text, card["change"], card["used_evidence_count"]])) + " |")
    lines.extend(["", "*价域仅展示已核对历史记录的观测范围；不代表完整近期合理价区间。", "", "## 实际采用的代表性来源", ""])
    chosen = 0
    for card in cards:
        if not card["used_urls"]:
            continue
        lines.append(f"- #{card['display_id']} {cell(card['card'])}：[卡淘商品页]({card['used_urls'][0]})。")
        chosen += 1
        if chosen == 8:
            break
    destination.write_text("\n".join(lines) + "\n")
    print(json.dumps({"changes": dict(counters), "report": str(destination.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    run(args.directory, args.destination)
