from card_platform.domain.card_identity import CardIdentity
from card_platform.domain.hierarchical_estimator import HierarchicalEstimate


class Explainability:
    @staticmethod
    def build(identity: CardIdentity, estimate: HierarchicalEstimate, rows: dict[str, dict]) -> dict:
        comparables = []
        for weighted in estimate.evidence:
            sale = weighted.sale
            row = rows[sale.id]
            comparables.append(
                {
                    "id": sale.id,
                    "title": row.get("listing_title"),
                    "source_url": row.get("source_url"),
                    "occurred_at": row["occurred_at"].isoformat(),
                    "sale_price": str(row["sale_price"]),
                    "shipping_price": str(row["shipping_price"]),
                    "landed_price": str(row["sale_price"] + row["shipping_price"]),
                    "currency": row["currency"],
                    "fx_rate_to_base": str(row.get("fx_rate_to_base") or 1),
                    "normalized_card_price": str(sale.sale_price_base),
                    "normalized_landed_price": str(sale.sale_price_base + sale.shipping_base),
                    "tier": sale.classification.tier.value,
                    "hard_conflicts": sale.classification.hard_conflicts,
                    "field_matches": sale.classification.field_matches,
                    "similarity": str(sale.classification.similarity),
                    "source_reliability": str(sale.classification.source_reliability),
                    "raw_weight": str(weighted.raw_weight),
                    "final_weight": str(weighted.final_weight),
                    "used_for_center": weighted.used_for_center,
                    "adjustment_factor": str(sale.adjustment.factor),
                    "adjustment_basis": sale.adjustment.basis,
                    "adjustment_evidence_ids": list(sale.adjustment.evidence_ids),
                }
            )
        return {
            "strategy": "分层可比估值：T0 优先，匹配相似度与来源可靠度分离",
            "formula": "T0>=2 时仅 T0 决定卡价中心；次级证据仅扩大区间和检查异常",
            "resolved_identity": identity.as_dict(),
            "card_price": {
                "low": str(estimate.low),
                "fair": str(estimate.fair),
                "high": str(estimate.high),
            },
            "landed_price": {
                "low": str(estimate.landed_low),
                "fair": str(estimate.landed_fair),
                "high": str(estimate.landed_high),
            },
            "comparables": comparables,
        }
