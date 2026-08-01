import re
from dataclasses import asdict, dataclass, field
from typing import Any

from card_platform.knowledge import KnowledgeResolver


INSERT_ALIASES = {
    "dreamland": ("dreamland", "梦幻之地", "梦境"),
    "night owls": (
        "night owls",
        "night owl",
        "夜莺",
        "夜鹰",
        "夜枭",
        "夜猫子",
        "猫头鹰",
    ),
    "first steps": ("first steps", "第一步", "鞋灯", "小鞋灯", "球鞋"),
    "sweet stroke": ("sweet stroke", "sweet strokes", "sweet stroke jerseys"),
    "hardcourt heroics": ("hardcourt heroics", "硬地英雄"),
    "timeless talents": (
        "timeless talents",
        "timeless talent",
        "永恒天才签",
        "永恒天才",
        "永恒天赋",
        "天赋时刻",
    ),
    "greatest signatures": (
        "greatest signatures",
        "greatest signature",
        "greatest",
        "伟大签",
        "最佳签",
    ),
    "prime relic autographs": (
        "prime relic autographs",
        "prime relic auto",
        "三窗",
    ),
    "rookie patch autographs": (
        "rookie patch autographs",
        "rookie patch auto",
        "大窗",
        "篮球窗",
    ),
    "gu jersey": (
        "gu jersey",
        "game used jersey",
        "game-worn jersey",
        "gu亲穿",
        "gu 亲穿",
        "亲穿球衣",
        "球衣物料",
        "球衣切割",
    ),
}

_INSERT_RESOLVER = KnowledgeResolver(
    {
        "entities": [
            {
                "id": f"legacy-insert:{canonical}",
                "entity_type": "insert",
                "canonical_name": canonical.title(),
                "aliases": list(aliases),
            }
            for canonical, aliases in INSERT_ALIASES.items()
        ]
    }
)


def normalize_text(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()


def canonical_insert(value: object) -> str | None:
    if not normalize_text(value):
        return None
    resolved = _INSERT_RESOLVER.resolve("insert", value)
    if resolved.entity_id:
        return resolved.canonical_value
    matches = _INSERT_RESOLVER.find_in_text("insert", value)
    return matches[0].canonical_value if len(matches) == 1 else str(value).strip()


def known_insert_from_text(value: object) -> str | None:
    matches = _INSERT_RESOLVER.find_in_text("insert", value)
    return matches[0].canonical_value if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class CardIdentity:
    player_name: str
    season_start: int | None = None
    season_end: int | None = None
    brand_name: str | None = None
    product_name: str | None = None
    set_name: str | None = None
    card_number: str | None = None
    serial_number: int | None = None
    serial_total: int | None = None
    print_run: int | None = None
    insert: str | None = None
    parallel: str | None = None
    grade_company: str | None = None
    grade_value: str | None = None
    is_rookie: bool = False
    is_autograph: bool = False
    is_patch: bool = False
    evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CardIdentityResolver:
    """Resolve identity fields while preserving provenance and field semantics."""

    PRIORITY = {"database": 10, "catalog": 20, "listing": 30, "physical_front": 90, "physical_back": 100}

    def resolve(
        self,
        stored: dict[str, Any],
        evidence: list[dict[str, Any]] | None = None,
    ) -> CardIdentity:
        context = " ".join(
            str(stored.get(key) or "")
            for key in ("purchase_context", "notes")
        )
        stored_insert = canonical_insert(stored.get("insert") or stored.get("insert_name"))
        serial_total = stored.get("serial_total") or stored.get("print_run")
        card_number = stored.get("card_number")
        if card_number:
            normalized_number = str(card_number).strip().lstrip("#")
            if normalize_text(normalized_number) in {"nno", "no number", "unnumbered"}:
                card_number = None
            elif re.fullmatch(r"\d+\s*/\s*\d+", normalized_number):
                card_number = None
            elif (
                serial_total
                and normalized_number == str(serial_total)
                and re.search(
                    rf"(?:/\s*{int(serial_total)}\b|\b{int(serial_total)}\s*编)",
                    context,
                    re.I,
                )
                and not re.search(
                    rf"(?:#|no\.?\s*){re.escape(normalized_number)}\b",
                    context,
                    re.I,
                )
            ):
                card_number = None
            elif serial_total and normalized_number == str(serial_total):
                card_number = None
        context_insert = known_insert_from_text(context)
        context_parallel = None
        if re.search(r"(?:红平行|红折|\bred(?:\s+parallel)?\b)", context, re.I):
            context_parallel = "Red"
        elif re.search(r"(?:银平行|银折|\bsilver(?:\s+prizm)?\b)", context, re.I):
            context_parallel = "Silver"
        stored_parallel = stored.get("parallel") or stored.get("parallel_name")
        stored_parallel_text = normalize_text(stored_parallel)
        stored_is_rookie = bool(stored.get("is_rookie")) or stored_parallel_text in {
            "rc",
            "rookie",
            "新秀",
            "正新秀",
        }
        if stored_parallel_text in {"rc", "rookie", "新秀", "正新秀"}:
            stored_parallel = None
        context_is_patch = bool(
            re.search(
                r"(?:物料|切割|球衣|patch|\bjersey\b|\bmem(?:orabilia)?\b|\bgu\b)",
                context,
                re.I,
            )
            or normalize_text(stored_insert) in {"sweet stroke", "gu jersey"}
        )
        context_is_autograph = bool(
            re.search(r"(?:签字|卡签|贴签|签名|\bauto(?:graph)?\b|\brpa\b)", context, re.I)
            or normalize_text(stored_insert)
            in {"rookie patch autographs", "prime relic autographs", "timeless talents"}
        )
        if context_insert is None and context_is_autograph and context_is_patch:
            context_insert = "Rookie Patch Autographs"
        values = {
            "player_name": stored.get("player_name") or "",
            "season_start": stored.get("season_start"),
            "season_end": stored.get("season_end"),
            "brand_name": stored.get("brand_name"),
            "product_name": stored.get("product_name"),
            "set_name": stored.get("set_name"),
            "card_number": card_number,
            "serial_number": stored.get("serial_number"),
            "serial_total": stored.get("serial_total"),
            "print_run": stored.get("print_run"),
            "insert": context_insert or stored_insert,
            "parallel": context_parallel or stored_parallel,
            "grade_company": stored.get("grade_company"),
            "grade_value": (
                str(stored["grade_value"]) if stored.get("grade_value") is not None else None
            ),
            "is_rookie": stored_is_rookie,
            "is_autograph": bool(stored.get("is_autograph")) or context_is_autograph,
            "is_patch": bool(stored.get("is_patch")) or context_is_patch,
        }
        provenance: dict[str, list[dict[str, Any]]] = {
            key: [{"source": "database", "value": value}]
            for key, value in values.items()
            if value not in (None, "")
        }
        chosen_priority = {key: self.PRIORITY["database"] for key in values}
        conflicts: list[str] = []
        for item in evidence or []:
            source = str(item.get("source") or "listing")
            priority = self.PRIORITY.get(source, 0)
            fields = item.get("fields") or {}
            for key, raw_value in fields.items():
                if key not in values or raw_value in (None, ""):
                    continue
                value = canonical_insert(raw_value) if key == "insert" else raw_value
                if key in {"serial_number", "serial_total", "print_run"}:
                    value = int(value)
                if key == "card_number":
                    value = str(value).lstrip("#")
                provenance.setdefault(key, []).append({"source": source, "value": value})
                old = values.get(key)
                if old not in (None, "") and normalize_text(old) != normalize_text(value):
                    conflicts.append(f"{key}: {old} → {value} ({source})")
                if priority >= chosen_priority.get(key, 0):
                    values[key] = value
                    chosen_priority[key] = priority
        if values["serial_total"] is None:
            values["serial_total"] = values["print_run"]
        if values["print_run"] is None:
            values["print_run"] = values["serial_total"]
        return CardIdentity(**values, evidence=provenance, conflicts=conflicts)
