import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from card_platform.domain.redemption import redemption_identity
from card_platform.knowledge import KnowledgeResolver
from card_platform.domain.product_identity import product_matches_title


INSERT_ALIASES = {
    "rookie of the year favorites": ("rookie of the year favorites", "roty favorites"),
    "roses": ("roses", "玫瑰", "玫瑰特卡"),
    "playing cards": ("playing cards", "扑克", "扑克牌"),
    # The circular artwork crosses the printed word, so OCR/vision commonly
    # drops the hidden "no" and returns Hoop-Itic. The checklist/seller name is
    # Hoopnotic; keep the visual transcription variants as aliases for every
    # player in this insert rather than correcting an individual collection row.
    "hoopnotic": (
        "hoopnotic",
        "hoop-notic",
        "hoop notic",
        "hoopotic",
        "hoop-itic",
        "hoop itic",
    ),
    "retrofractor variations": (
        "retrofractor variations",
        "retrofractor variation",
        "retrofractor",
        "retro refractor",
        "复古银折",
        "复古",
        "变化版",
    ),
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
    "award winner holograms": (
        "award winner holograms",
        "ward winner holograms",
        "award winner hologram",
        "ward winner hologram",
        "mvp全息",
    ),
    "finals mvps": (
        "finals mvps",
        "finals mvp",
        "fmvp",
        "总决赛mvp",
        "总决赛 mvp",
        "捧杯",
        "双捧杯",
        "奖杯",
    ),
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
    "bounce house": ("bounce house", "跳跳屋", "弹跳屋", "扣篮板报"),
    "jam masters": ("jam masters", "扣篮大师", "大头特卡", "大头"),
    "bomb squad": ("bomb squad", "爆炸小队"),
    "magicians": ("magicians", "魔术师"),
    "penmanship": ("penmanship", "书法签"),
    "jumbo jersey autographs": (
        "jumbo jersey autographs",
        "jumbo jersey auto",
        "大窗球衣签字",
        "大窗 签字 球衣",
        "ja 签字 卡签 球衣",
    ),
    "remarkable rookie jerseys dual": (
        "remarkable rookie jerseys dual",
        "dual jersey",
        "dual jerseys",
        "双窗球衣",
        "双人物料",
    ),
    "fanatical": ("fanatical", "狂热"),
    "starquest": ("starquest", "star quest", "一星", "星际迷航", "星际探索"),
    "stratospheric stars": (
        "stratospheric stars",
        "stratospheric",
        "平流层之星",
    ),
    "winning tags": ("winning tags", "winning tag", "制胜标签"),
    "principal powers": ("principal powers", "principal power"),
    "colossal materials": ("colossal materials", "colossal", "大窗物料"),
    "nucleus": ("nucleus", "核心三人", "三人核心"),
    "moment in time": ("moment in time", "mit"),
    "topps chrome autographs": (
        "topps chrome autographs",
        "topps chrome autograph card",
        "chrome autograph",
    ),
    "rookie focus": (
        "rookie focus",
        "focus",
        "新秀聚焦",
        "聚焦特卡",
    ),
    # Reviewed cross-product card-kind vocabulary. These entries let every
    # marketplace matcher distinguish an explicit *other* insert from a terse
    # title that merely omits the requested insert name.
    "calligraphy": ("calligraphy", "书法签", "书法签字"),
    "hall of fame": ("hall of fame", "名人堂", "名人堂签"),
    "serenity": ("serenity", "宁静特卡"),
    "cactus jack": ("cactus jack", "蟑螂盒"),
    "image variation": ("image variation", "变化版", "变体版"),
    "passport": ("passport", "护照特卡", "护照"),
    "illuminators": ("illuminators", "辐射源特卡", "辐射源"),
    "nba 2k17": ("nba2k17", "nba 2k17"),
    "modern strokes": (
        "modern strokes",
        "现代进攻艺术",
        "进攻艺术",
        "笔刷特卡",
    ),
    "maestros": ("maestros", "大师特卡"),
    "contemporaries": ("contemporaries", "contemporary", "当代特卡"),
    "buzz factor": ("buzz factor",),
    "hurricane": ("hurricane", "破风龙驹"),
    "frostbite": ("frostbite", "fbostbite", "冰霜"),
    "thunder road": ("thunder road", "雷霆之路"),
    "epic performers": ("epic performers",),
    "elevate": ("elevate", "升华特卡", "升华"),
    "city edition": ("city edition",),
    "road to finals": (
        "road to finals",
        "road to the finals",
        "总决赛之路",
        "总冠军之路",
        "季后赛特卡",
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
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_marks = "".join(character for character in folded
                            if not unicodedata.combining(character))
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", without_marks).strip()


_IDENTITY_PLACEHOLDERS = {
    "none",
    "null",
    "unknown",
    "unspecified",
    "not applicable",
    "not identified",
    "n a",
    "na",
    "未知",
    "未识别",
    "无法识别",
}


def is_placeholder_identity_value(value: object) -> bool:
    """Identify serialization/OCR placeholders before they become card facts."""
    token = normalize_text(value)
    return token in _IDENTITY_PLACEHOLDERS or bool(re.fullmatch(
        r'(?:无法识别|未识别|未知)(?:球员|人物|主体|品牌|产品|卡号|系列)', token))


def canonical_insert(value: object) -> str | None:
    if not normalize_text(value) or is_placeholder_identity_value(value):
        return None
    resolved = _INSERT_RESOLVER.resolve("insert", value)
    if resolved.entity_id:
        return resolved.canonical_value
    # This is a complete structured field, not a marketplace title. Finding a
    # known word inside an unknown name cannot establish an equivalent insert.
    return str(value).strip()


def known_insert_from_text(value: object) -> str | None:
    matches = _INSERT_RESOLVER.find_in_text("insert", value)
    return matches[0].canonical_value if len(matches) == 1 else None


def is_manufacturer_tracking_code(
    value: object, *, brand_name: object = None, product_name: object = None
) -> bool:
    """Return true for packaging/web codes that are not checklist numbers."""
    token = re.sub(r"\s+", "", str(value or "")).upper()
    if re.fullmatch(r"(?:CODE#?)?CMP\d{5,}", token):
        return True
    topps_context = "topps" in normalize_text(brand_name) or normalize_text(
        product_name
    ) in {"nba hoops", "topps chrome", "chrome"}
    return bool(topps_context and re.fullmatch(r"\d{6,}", token))


def is_intrinsic_product_finish(parallel: object, product_name: object) -> bool:
    """Return OCR'd technology words that repeat the product family itself.

    ``Chrome`` in Bowman Chrome and ``Prizm`` in Prizm are product stock, not a
    separately priced parallel.  The same word remains meaningful outside its
    native family (for example a Flux ``Prizm`` parallel), so this is based on
    both fields rather than a global stop-word list.
    """
    finish = normalize_text(parallel)
    product = normalize_text(product_name)
    if not finish or not product:
        return False
    product_tokens = set(product.split())
    if finish in {"chrome", "mosaic", "optic"} and finish in product_tokens:
        return True
    if finish == "prizm":
        return "prizm" in product_tokens or "premium stock" in product or any(
            token in product_tokens for token in ("mosaic", "optic")
        )
    return finish == product


def has_misplaced_season_numbering(identity: dict[str, Any]) -> bool:
    """Detect season text that OCR placed into card/serial number fields."""
    card_number = str(identity.get("card_number") or "")
    pairs = [
        (int(start), int(end))
        for start, end in re.findall(
            r"(?<!\d)(\d{2})\s*[-–—/]\s*(\d{2})(?!\d)", card_number
        )
        if (int(end) - int(start)) % 100 in {0, 1}
    ]
    if not pairs:
        return False
    serial_number = identity.get("serial_number")
    serial_total = identity.get("serial_total") or identity.get("print_run")
    if serial_total is None:
        return False
    return any(
        int(serial_total) in {start, end}
        and (
            serial_number is None
            or re.search(rf"(?:^|/)\s*{int(serial_number)}\s*(?:/|$)", card_number)
        )
        for start, end in pairs
    )


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
    is_redemption: bool = False
    redemption_status: str = "unknown"
    is_rookie: bool | None = None
    is_autograph: bool | None = None
    is_memorabilia: bool | None = None
    is_patch: bool | None = None
    is_numbered: bool | None = None
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
        product_token = normalize_text(stored.get("product_name"))
        insert_token = normalize_text(stored_insert)
        # Vision/OCR sometimes swaps a printed insert heading with the product
        # logo on the back. Repair the hierarchy from generic catalog roles;
        # this applies to every card in these sets rather than special-casing a
        # player or collection item.
        hierarchy_swaps = {
            ("principal powers", "ud reserve"): ("Upper Deck Reserve", "Principal Powers"),
            ("principal powers", "upper deck reserve"): (
                "Upper Deck Reserve",
                "Principal Powers",
            ),
            ("starquest", "ud choice"): ("UD Choice", "Starquest"),
            ("starquest", "upper deck choice"): ("UD Choice", "Starquest"),
        }
        swapped = hierarchy_swaps.get((product_token, insert_token))
        if swapped:
            stored = {**stored, "product_name": swapped[0]}
            stored_insert = swapped[1]
            product_token = normalize_text(swapped[0])
        role_migrations = {
            # Daily Dribble is a Topps flagship insert heading, not a product
            # family. Apply the ontology rule to every player/card in the set.
            "daily dribble": ("Topps Basketball", "Daily Dribble"),
        }
        migrated = role_migrations.get(product_token)
        if migrated and not stored_insert:
            stored = {**stored, "product_name": migrated[0]}
            stored_insert = migrated[1]
            product_token = normalize_text(migrated[0])
            insert_token = normalize_text(stored_insert)
        if (
            stored_insert
            and product_matches_title(stored_insert, stored.get("product_name"))
            and product_matches_title(stored.get("product_name"), stored_insert)
        ):
            # OCR commonly repeats the front product logo in the insert field
            # (for example NBA Hoops inside Hoops Basketball). Exact symmetric
            # family equivalence is safe to clear; longer real insert names such
            # as Topps Chrome Autographs are not symmetric and remain intact.
            stored_insert = None
        if normalize_text(stored_insert) == "prizm" and any(
            token in product_token for token in ("mosaic", "optic")
        ):
            # Panini prints PRIZM on the back of regular Mosaic/Optic cards as
            # a technology mark. It is neither the insert nor a named parallel.
            stored_insert = None
        misplaced_season_numbering = has_misplaced_season_numbering(stored)
        serial_total = (
            None
            if misplaced_season_numbering
            else stored.get("serial_total") or stored.get("print_run")
        )
        card_number = stored.get("card_number")
        if misplaced_season_numbering:
            card_number = None
        from card_platform.domain.number_roles import is_layout_label
        if (is_placeholder_identity_value(card_number) or is_layout_label(card_number)
                or normalize_text(card_number) in {"top left", "top right", "bottom left", "bottom right",
                                                   "top center", "bottom center"}):
            card_number = None
        if card_number:
            normalized_number = str(card_number).strip().lstrip("#")
            if is_manufacturer_tracking_code(
                normalized_number,
                brand_name=stored.get("brand_name"),
                product_name=stored.get("product_name"),
            ):
                card_number = None
            elif normalize_text(normalized_number) in {"nno", "no number", "unnumbered"}:
                card_number = None
            elif re.fullmatch(r"\d+\s*/\s*\d+", normalized_number):
                # Import locally: identity_review uses this module's normalizer.
                from card_platform.domain.identity_review import is_tcg_collector_number
                if not is_tcg_collector_number(stored):
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
        if is_placeholder_identity_value(stored_parallel):
            stored_parallel = None
        stored_parallel_text = normalize_text(stored_parallel)
        if stored_parallel_text == "refactor":
            # Common OCR/LLM spelling error for the checklist term Refractor.
            stored_parallel = "Refractor"
            stored_parallel_text = "refractor"
        elif is_intrinsic_product_finish(stored_parallel, product_token):
            # Product technology copied into the parallel field is not a
            # separately priced version. The same word outside that product
            # family remains intact (for example Flux Prizm).
            stored_parallel = None
            stored_parallel_text = ""
        rookie_marker = stored_parallel_text in {
            "rc",
            "rookie",
            "新秀",
            "正新秀",
        }
        stored_is_rookie = True if rookie_marker else stored.get("is_rookie")
        if stored_parallel_text in {"rc", "rookie", "新秀", "正新秀"}:
            stored_parallel = None
        # OCR frequently copies a product technology/logo (for example
        # "Chrome") into the parallel field. An exact duplicate of the product
        # is not a named parallel and should be researched as the base finish.
        if stored_parallel_text and is_intrinsic_product_finish(
            stored_parallel, product_token
        ):
            stored_parallel = None
        insert_attribute_text = normalize_text(stored_insert)
        context_is_memorabilia = bool(
            re.search(
                r"(?:物料|切割|球衣|patch|\bjersey\b|\bmem(?:orabilia)?\b|\bgu\b)",
                context,
                re.I,
            )
            or re.search(
                r"(?:jersey|materials?|memorabilia|relic|threads|game floor)",
                insert_attribute_text,
                re.I,
            )
            or insert_attribute_text in {"sweet stroke", "gu jersey"}
        )
        context_is_patch = bool(
            re.search(r"(?:补丁|patch)", context, re.I)
            or re.search(r"(?:patch)", insert_attribute_text, re.I)
        )
        context_is_autograph = bool(
            re.search(r"(?:签字|卡签|贴签|签名|\bauto(?:graph)?\b|\brpa\b)", context, re.I)
            or re.search(r"autographs?", insert_attribute_text, re.I)
            or insert_attribute_text
            in {"rookie patch autographs", "prime relic autographs", "timeless talents"}
        )
        if (
            context_insert is None
            and stored_insert is None
            and context_is_autograph
            and context_is_memorabilia
        ):
            context_insert = "Rookie Patch Autographs"
        values = {
            "player_name": ("" if is_placeholder_identity_value(stored.get("player_name"))
                            else stored.get("player_name") or ""),
            "season_start": stored.get("season_start"),
            "season_end": stored.get("season_end"),
            "brand_name": (
                None
                if is_placeholder_identity_value(stored.get("brand_name"))
                else stored.get("brand_name")
            ),
            "product_name": (
                None
                if is_placeholder_identity_value(stored.get("product_name"))
                else stored.get("product_name")
            ),
            "set_name": (
                None
                if is_placeholder_identity_value(stored.get("set_name"))
                else stored.get("set_name")
            ),
            "card_number": card_number,
            "serial_number": (
                None if misplaced_season_numbering else stored.get("serial_number")
            ),
            "serial_total": (
                None if misplaced_season_numbering else stored.get("serial_total")
            ),
            "print_run": (
                None if misplaced_season_numbering else stored.get("print_run")
            ),
            "insert": context_insert or stored_insert,
            "parallel": context_parallel or stored_parallel,
            "grade_company": (
                None
                if is_placeholder_identity_value(stored.get("grade_company"))
                else stored.get("grade_company")
            ),
            "grade_value": (
                str(stored["grade_value"])
                if stored.get("grade_value") is not None
                and not is_placeholder_identity_value(stored.get("grade_value"))
                else None
            ),
            "is_redemption": redemption_identity(stored)[0],
            "redemption_status": redemption_identity(stored)[1],
            "is_rookie": stored_is_rookie,
            "is_autograph": True if context_is_autograph else stored.get("is_autograph"),
            "is_memorabilia": True if stored.get("is_patch") or context_is_memorabilia else stored.get("is_memorabilia"),
            "is_patch": True if context_is_patch else stored.get("is_patch"),
            "is_numbered": stored.get("is_numbered"),
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
                if (
                    key not in values
                    or raw_value in (None, "")
                    or is_placeholder_identity_value(raw_value)
                ):
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
        # Recover only a missing card kind from the reviewed product checklist.
        # R-* is NOT globally unique. Scope to this release and its 25 entries;
        # never infer a parallel/print run from the red flower artwork.
        if (
            not values["insert"]
            and normalize_text(values["brand_name"]) == "topps"
            and normalize_text(values["product_name"]) in {
                "signature class", "topps signature class",
                "signature class basketball", "topps signature class basketball",
            }
            and (values["season_start"], values["season_end"]) in {
                (2025, 2026), (2025, None), (2026, None), (2026, 2026),
            }
            and re.fullmatch(
                r"#?R-(?:[1-9]|1[0-9]|2[0-5])",
                str(values["card_number"] or "").strip(), re.I,
            )
        ):
            values["insert"] = "Roses"
            provenance["insert"] = [{
                "source": "catalog", "value": "Roses",
                "source_url": (
                    "https://cdn.shopify.com/s/files/1/0662/9749/5709/files/"
                    "2025-26_Topps_Signature_Class_Basketball_Checklist.pdf?v=1777313247"
                ),
                "reason": "Topps official checklist p.10: Roses R-1 through R-25",
            }]
        return CardIdentity(**values, evidence=provenance, conflicts=conflicts)
