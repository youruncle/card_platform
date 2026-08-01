from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Adjustment:
    factor: Decimal = Decimal("1")
    basis: str = "no adjustment"
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reproducible(self) -> bool:
        return self.factor == 1 or len(self.evidence_ids) >= 2


class Weighting:
    TIER_WEIGHTS = {"T0": Decimal("1"), "T1": Decimal("0.35"), "T2": Decimal("0.12"), "T3": Decimal("0.04")}

    @classmethod
    def raw_weight(
        cls,
        tier: str,
        similarity: Decimal,
        source_reliability: Decimal,
        age_days: int,
    ) -> Decimal:
        recency = max(Decimal("0.35"), Decimal("1") - Decimal(age_days) / Decimal("730"))
        return cls.TIER_WEIGHTS.get(tier, Decimal("0")) * similarity * source_reliability * recency
