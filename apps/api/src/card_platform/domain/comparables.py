import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from card_platform.domain.card_identity import CardIdentity, INSERT_ALIASES, normalize_text
from card_platform.knowledge import KnowledgeResolver


class ComparableTier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ComparableInput:
    title: str
    grade_company: str | None = None
    grade_value: Decimal | None = None
    source_reliability: Decimal = Decimal("0.70")
    source_code: str | None = None
    visual_verdict: str | None = None
    visual_confidence: Decimal | None = None
    visual_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Classification:
    tier: ComparableTier
    similarity: Decimal
    source_reliability: Decimal
    field_matches: dict[str, bool | None] = field(default_factory=dict)
    hard_conflicts: list[str] = field(default_factory=list)


def _contains(text: str, value: object) -> bool:
    value_text = normalize_text(value)
    return bool(value_text and value_text in normalize_text(text))


def _contains_product(text: str, value: object) -> bool:
    target = normalize_text(value)
    normalized = normalize_text(text)
    aliases = {
        "topps three": ("topps three", "topps 3"),
        "topps 3": ("topps three", "topps 3"),
        "mosaic basketball": ("mosaic", "马赛克"),
        "prizm basketball": ("prizm", "prizm系列"),
        "optic basketball": ("optic", "杜蕾斯"),
        "select basketball": ("select", "select系列"),
        "national treasures basketball": ("national treasures", "国宝"),
    }
    return any(alias in normalized for alias in aliases.get(target, (target,)) if alias)


def _contains_parallel(text: str, value: object) -> bool:
    target = normalize_text(value)
    normalized = normalize_text(text)
    aliases = {
        "silver": ("silver", "silver parallel", "银平行", "银折"),
        "silver parallel": ("silver", "silver parallel", "银平行", "银折"),
        "red": ("red", "red parallel", "红平行", "红折"),
        "red parallel": ("red", "red parallel", "红平行", "红折"),
        "blue": ("blue", "blue parallel", "蓝平行", "蓝折"),
        "blue parallel": ("blue", "blue parallel", "蓝平行", "蓝折"),
        "gold": ("gold", "gold parallel", "金平行", "金折"),
        "gold parallel": ("gold", "gold parallel", "金平行", "金折"),
        "fusion red and yellow": (
            "fusion red and yellow",
            "red yellow mosaic",
            "红黄马赛克",
            "红金马赛克",
            "黄马赛克",
        ),
    }
    return any(alias in normalized for alias in aliases.get(target, (target,)) if alias)


def _contains_other_parallel(text: str, target: object) -> bool:
    target_text = normalize_text(target)
    normalized = normalize_text(text)
    parallel_aliases = {
        "silver": ("silver", "silver parallel", "银平行", "银折"),
        "red": ("red", "red parallel", "红平行", "红折"),
        "blue": ("blue", "blue parallel", "蓝平行", "蓝折"),
        "gold": ("gold", "gold parallel", "金平行", "金折"),
        "green": ("green", "green parallel", "绿平行", "绿折"),
        "purple": ("purple", "purple parallel", "紫平行", "紫折"),
        "black": ("black", "black parallel", "黑平行", "黑折"),
        "bronze": ("bronze", "bronze parallel", "铜平行", "铜折"),
    }
    target_aliases = set(parallel_aliases.get(target_text, (target_text,)))
    for canonical, aliases in parallel_aliases.items():
        if canonical == target_text or set(aliases) & target_aliases:
            continue
        if any(alias in normalized for alias in aliases if alias):
            return True
    return False


class ComparableClassifier:
    def __init__(self, resolver: KnowledgeResolver | None = None) -> None:
        self.resolver = resolver

    def classify(self, target: CardIdentity, comp: ComparableInput) -> Classification:
        text = comp.title
        normalized = normalize_text(text)
        conflicts: list[str] = []
        visual_confidence = comp.visual_confidence or Decimal("0")
        if comp.visual_verdict == "different" and visual_confidence >= Decimal("0.80"):
            conflicts.append("visual_identity_conflict")
        if re.search(
            r"(?:\blot\b|多张\s*lot|多卡|套卡|整套|卡盒|卡包|\bbox\b|\bpack\b)",
            text,
            re.I,
        ):
            conflicts.append("lot_or_multi_card")
        target_raw = target.grade_company is None
        comp_graded = bool(comp.grade_company or re.search(r"\b(?:PSA|BGS|SGC|CGC)\s*\d", text, re.I))
        if target_raw != (not comp_graded):
            conflicts.append("grade_state_conflict")
        elif not target_raw and (
            normalize_text(comp.grade_company) != normalize_text(target.grade_company)
            or comp.grade_value is None
            or Decimal(comp.grade_value) != Decimal(str(target.grade_value))
        ):
            conflicts.append("grade_conflict")
        target_material = target.is_patch
        comp_material = bool(
            re.search(
                r"(?:物料|切割|球衣|patch|\bjersey\b|\bmem(?:orabilia)?\b|\bgu\b)",
                text,
                re.I,
            )
            or any(
                normalize_text(alias) in normalized
                for canonical in ("sweet stroke", "hardcourt heroics", "gu jersey")
                for alias in INSERT_ALIASES[canonical]
            )
        )
        if target_material != comp_material:
            conflicts.append("material_conflict")
        target_autograph = target.is_autograph
        comp_autograph = bool(
            re.search(r"(?:签字|卡签|贴签|签名|\bauto(?:graph)?\b|\brpa\b)", text, re.I)
        )
        if target_autograph != comp_autograph:
            conflicts.append("autograph_conflict")

        player = _contains(text, target.player_name)
        product = _contains_product(text, target.product_name)
        season_tokens = (
            {
                str(target.season_start),
                str(target.season_end),
                f"{target.season_start}-{str(target.season_end)[-2:]}",
            }
            if target.season_start and target.season_end
            else {str(target.season_start)}
            if target.season_start
            else set()
        )
        explicit_seasons: set[tuple[int, int]] = set()
        for start_text, end_text in re.findall(
            r"(?<!\d)((?:19|20)\d{2})\s*[-–/]\s*((?:(?:19|20)\d{2})|\d{2})(?!\d)",
            text,
        ):
            start_value = int(start_text)
            end_value = int(end_text)
            if len(end_text) == 2:
                end_value = (start_value // 100) * 100 + end_value
            explicit_seasons.add((start_value, end_value))
        target_season = (
            (int(target.season_start), int(target.season_end))
            if target.season_start and target.season_end
            else None
        )
        season = (
            target_season in explicit_seasons
            if explicit_seasons and target_season
            else not season_tokens or any(token in text for token in season_tokens)
        )
        card_number_pattern = (
            rf"(?<![0-9a-z])(?:#|no\.?\s*)?"
            rf"{re.escape(str(target.card_number or ''))}(?![0-9a-z])"
        )
        card_number = bool(
            target.card_number and re.search(card_number_pattern, text, re.I)
        )
        explicit_card_numbers = {
            normalize_text(value)
            for value in re.findall(
                r"(?:#|\bno\.?\s*)([0-9a-z][0-9a-z-]{0,15})",
                text,
                re.I,
            )
        }
        target_card_number = normalize_text(target.card_number)
        if (
            target_card_number
            and explicit_card_numbers
            and target_card_number not in explicit_card_numbers
        ):
            conflicts.append("card_number_conflict")
        insert_aliases = (
            self.resolver.aliases_for("insert", target.insert)
            if self.resolver and self.resolver.entities
            else INSERT_ALIASES.get(normalize_text(target.insert), (target.insert or "",))
        )
        insert = any(normalize_text(alias) in normalized for alias in insert_aliases if alias)
        if normalize_text(target.insert) == "rookie patch autographs" and re.search(
            r"\brpa\b", text, re.I
        ):
            insert = True
        if (
            normalize_text(target.insert) == "rookie patch autographs"
            and comp_material
            and bool(re.search(r"(?:签字|卡签|贴签|签名|\bauto(?:graph)?\b)", text, re.I))
        ):
            insert = True
        if comp.visual_verdict == "same" and visual_confidence >= Decimal("0.85"):
            insert = True
        target_parallel = normalize_text(target.parallel)
        parallel = _contains_parallel(text, target.parallel)
        if target_parallel == "base":
            named_parallel = bool(
                re.search(
                    r"(?:\bred\b|\bblue\b|\bgold\b|\bgreen\b|\bpurple\b|"
                    r"\bblack\b|\bsilver\b|\bbronze\b|红折|蓝折|金折|绿折|"
                    r"紫折|黑折|银折|铜折)",
                    text,
                    re.I,
                )
            )
            parallel = not named_parallel
            if named_parallel:
                conflicts.append("parallel_conflict")
        elif target_parallel and not parallel and _contains_other_parallel(text, target.parallel):
            conflicts.append("parallel_conflict")
        totals = {
            int(number)
            for pair in re.findall(
                r"(?:/\s*(\d+)(?!\d)|(?<!\d)(\d+)\s*[编遍])",
                text,
            )
            for number in pair
            if number
        }
        target_total = target.serial_total or target.print_run
        serial_total = bool(target_total and int(target_total) in totals)
        if (
            serial_total
            and target_parallel
            and target_parallel != "base"
            and not _contains_other_parallel(text, target.parallel)
        ):
            parallel = True
        if totals and target_total and int(target_total) not in totals:
            conflicts.append("serial_total_conflict")
        if not player:
            conflicts.append("player_conflict")
        if not product or not season:
            conflicts.append("product_conflict")

        if self.resolver and self.resolver.entities:
            target_resolution = self.resolver.resolve("insert", target.insert)
            known_other_insert = any(
                match.entity_id != target_resolution.entity_id
                and normalize_text(match.canonical_value) != "gu jersey"
                for match in self.resolver.find_in_text("insert", text)
            )
        else:
            known_other_insert = any(
                any(normalize_text(alias) in normalized for alias in aliases)
                for canonical, aliases in INSERT_ALIASES.items()
                if canonical != "gu jersey"
                and normalize_text(canonical) != normalize_text(target.insert)
            )
        if normalize_text(target.insert) == "first steps" and re.search(
            r"(?:胶片|一级|二级|三级|炫彩怒吼|定装照)", text, re.I
        ):
            known_other_insert = True
        if known_other_insert:
            conflicts.append("insert_conflict")
        if conflicts:
            return Classification(
                ComparableTier.REJECTED,
                Decimal("0"),
                comp.source_reliability,
                hard_conflicts=conflicts,
            )

        fields = {
            "player": player,
            "season": season,
            "product": product,
            "card_number": card_number,
            "insert": insert,
            "parallel": parallel,
            "serial_total": serial_total,
            "grade": True,
            "visual": (
                comp.visual_verdict == "same"
                if comp.visual_verdict is not None
                else None
            ),
        }
        serial_requirement_met = target_total is None or serial_total
        parallel_requirement_met = target_parallel in {"", "base"} or parallel
        if insert and parallel_requirement_met and serial_requirement_met:
            tier, similarity = ComparableTier.T0, Decimal("1.00")
        elif insert and (card_number or target.card_number is None):
            tier, similarity = ComparableTier.T1, Decimal("0.82")
        elif player and product:
            tier, similarity = ComparableTier.T2, Decimal("0.60")
        else:
            tier, similarity = ComparableTier.T3, Decimal("0.42")
        if (
            comp.visual_verdict == "different"
            and visual_confidence >= Decimal("0.55")
            and tier == ComparableTier.T0
        ):
            # A low-resolution reference can still reveal large layout/pose
            # differences, but it is not strong enough to hard-reject a textually
            # exact sale. Demote it from exact-card evidence instead.
            tier, similarity = ComparableTier.T1, Decimal("0.78")
        return Classification(tier, similarity, comp.source_reliability, fields)
