import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from card_platform.domain.card_identity import normalize_text


GENERIC_PARALLELS = {"prizm", "refractor", "parallel", "折射", "折射版", "普卡"}


def is_scoped_standard_refractor(identity: dict[str, Any]) -> bool:
    """Topps Chrome's named Refractor is distinct from a generic finish claim.

    Sapphire is a separate product/finish scope. This admits an explicit name;
    it does not infer that name from a photograph, serial total or seller title.
    """
    product = normalize_text(identity.get('product_name'))
    scope = ' '.join(normalize_text(identity.get(field))
                     for field in ('product_name', 'set_name', 'insert_name'))
    return (normalize_text(identity.get('brand_name')) == 'topps'
            and 'chrome' in product.split()
            and normalize_text(identity.get('parallel_name') or identity.get('parallel')) == 'refractor'
            and not re.search(r'sapphire|蓝宝石|藍寶石', scope))


def is_tcg_collector_number(identity: dict[str, Any]) -> bool:
    """Recognize a catalog fraction only within an explicit Pokémon scope.

    The denominator is the checklist size, never evidence of a print run.
    Other TCG formats can be added when their product scope is verified.
    """
    number = re.fullmatch(r"(\d{1,4})\s*/\s*(\d{1,4})", str(identity.get("card_number") or "").strip())
    if not number or not all(int(value) > 0 for value in number.groups()):
        return False
    brand = normalize_text(identity.get("brand_name"))
    if brand in {"panini", "topps", "upper deck", "fleer", "skybox", "leaf"}:
        return False
    pokemon_brands = {"pokemon", "pokémon", "the pokemon company", "the pokémon company", "宝可梦", "寶可夢", "ポケモン"}
    return brand in pokemon_brands or bool(re.search(
        r"\bpok[eé]mon\b|宝可梦|寶可夢|ポケモン",
        str(identity.get("product_name") or ""), re.I,
    ))


class ConfirmedIdentity(BaseModel):
    player_name: str = Field(min_length=1)
    season_start: int = Field(ge=1940, le=2200)
    season_end: int | None = Field(default=None, ge=1940, le=2200)
    brand_name: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    set_name: str | None = None
    card_number: str | None = None
    insert_name: str | None = None
    parallel_name: str | None = None
    serial_number: int | None = Field(default=None, gt=0)
    serial_total: int | None = Field(default=None, gt=0)
    is_rookie: bool
    is_redemption: bool = False
    redemption_status: str = "unknown"
    is_autograph: bool
    autograph_type: str = "none"
    is_memorabilia: bool
    memorabilia_types: list[str] = Field(default_factory=list)
    grade_company: str | None = None
    grade_value: float | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def validate_identity(self) -> "ConfirmedIdentity":
        if self.season_end is not None and self.season_end not in {
            self.season_start, self.season_start + 1
        }:
            raise ValueError("season_end must equal season_start or season_start + 1")
        if self.serial_number and not self.serial_total:
            raise ValueError("serial_total is required when serial_number is present")
        if self.serial_number and self.serial_total and self.serial_number > self.serial_total:
            raise ValueError("serial_number cannot exceed serial_total")
        card_number = (self.card_number or "").strip()
        fraction = re.fullmatch(r"0*(\d+)\s*/\s*0*(\d+)", card_number)
        if fraction and not is_tcg_collector_number(self.model_dump()):
            raise ValueError("card_number must not contain a serial fraction")
        if (normalize_text(self.parallel_name) in GENERIC_PARALLELS
                and not is_scoped_standard_refractor(self.model_dump())):
            raise ValueError("generic Prizm/Refractor cannot be confirmed as a parallel")
        has_numbered_anchor = bool(self.card_number or self.insert_name or self.set_name)
        if self.serial_total and not self.parallel_name and not has_numbered_anchor:
            raise ValueError("a numbered card requires a specific parallel_name")
        if self.autograph_type not in {"on_card", "sticker", "cut", "unknown", "none"}:
            raise ValueError("invalid autograph_type")
        if not self.is_autograph and self.autograph_type != "none":
            raise ValueError("autograph_type must be none when is_autograph is false")
        if not self.is_memorabilia and self.memorabilia_types:
            raise ValueError("memorabilia_types require is_memorabilia")
        return self


def structured_evidence_decision(
    classification: dict[str, Any], evidence_level: str, *, identity_changed: bool = False,
) -> tuple[str, list[str]]:
    if identity_changed:
        return "needs_review", ["identity_changed_requires_evidence_revalidation"]
    reasons = list(classification.get("hard_conflicts") or [])
    tier = classification.get("comparable_tier") or classification.get("tier")
    if tier != "T0":
        reasons.append("not_exact_identity")
    if evidence_level not in {
        "confirmed_transaction", "ended_auction", "legacy_cardhobby_sale",
        "human_verified_exact_identity", "codex_assisted_exact_identity",
        "aggregated_sale", "legacy_market_comp",
    }:
        reasons.append("evidence_grade_not_eligible")
    reasons = list(dict.fromkeys(reasons))
    return ("rejected", reasons) if reasons else ("accepted", ["exact_identity_t0_e0_e2"])
