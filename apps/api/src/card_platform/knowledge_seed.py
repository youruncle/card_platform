from card_platform.domain.card_identity import INSERT_ALIASES
from card_platform.ingestion.cardhobby import (
    PARALLEL_SEARCH_ALIASES,
    PRODUCT_SEARCH_ALIASES,
)


ATTRIBUTE_ALIASES = {
    "RC": ("RC", "rookie", "新秀", "正新秀"),
    "RPA": ("RPA", "rookie patch autograph", "新秀球衣签"),
    "AUTO": ("AUTO", "autograph", "签字", "卡签"),
    "Sticker Autograph": ("sticker auto", "贴签"),
    "Patch": ("patch", "relic", "物料", "球衣", "切割"),
    "SSP": ("SSP", "超级短印", "超短印"),
    "Serial numbered": ("限编", "编", "serial numbered"),
}

CURATED_ENTITIES = [
    {
        "entity_type": "brand",
        "canonical_name": "Panini",
        "aliases": ["帕尼尼", "Paninni", "Pannini"],
    },
    {
        "entity_type": "brand",
        "canonical_name": "Topps",
        "aliases": ["拓普斯", "Topps Chrome", "Tops"],
    },
    {
        "entity_type": "product",
        "canonical_name": "Court Kings",
        "aliases": ["油画", "Court King", "CK"],
    },
    {
        "entity_type": "product",
        "canonical_name": "Topps Midnight",
        "aliases": ["Midnight", "午夜", "Topps Mid Night"],
    },
    {
        "entity_type": "product",
        "canonical_name": "Topps Three",
        "aliases": ["Topps 3", "Topps III", "三张盒"],
    },
    {
        "entity_type": "product",
        "canonical_name": "National Treasures",
        "aliases": ["国宝", "NT", "National Treasure", "National Tresures"],
    },
    {
        "entity_type": "product",
        "canonical_name": "One and One",
        "aliases": ["One & One", "One N One", "壹和壹", "一加一"],
    },
    {
        "entity_type": "parallel",
        "canonical_name": "Base",
        "aliases": ["base", "基础版", "普卡", "普版"],
    },
    {
        "entity_type": "parallel",
        "canonical_name": "Black",
        "aliases": ["black parallel", "黑平行", "黑折"],
    },
    {
        "entity_type": "parallel",
        "canonical_name": "Bronze",
        "aliases": ["bronze parallel", "铜平行", "铜折"],
    },
]

CURATED_ALIAS_ADDITIONS = {
    ("insert", "First Steps"): ["First Step", "FirstSteps", "鞋灯特卡", "球鞋灯"],
    ("insert", "Dreamland"): ["Dream Land", "梦幻地", "梦幻之地特卡"],
    ("insert", "Night Owls"): [
        "Night Owl",
        "NightOwls",
        "夜莺特卡",
        "猫头鹰特卡",
        "夜枭",
    ],
    ("insert", "Rookie Patch Autographs"): ["RPA", "新秀球衣签", "新秀物料签"],
    ("insert", "Prime Relic Autographs"): ["三窗 RPA", "三窗签", "Prime Relic Auto"],
}

CURATED_RELATIONS = [
    ("insert", "Dreamland", "conflicts_with", "insert", "Night Owls", "不同 insert，不可混用"),
    (
        "insert",
        "Timeless Talents",
        "conflicts_with",
        "insert",
        "Greatest Signatures",
        "不同签字 insert，不可按同卡成交分类",
    ),
]


def builtin_seed() -> list[dict]:
    rows: list[dict] = []
    for canonical, aliases in INSERT_ALIASES.items():
        rows.append(
            {
                "entity_type": "insert",
                "canonical_name": canonical.title(),
                "aliases": list(aliases),
            }
        )
    products: dict[str, set[str]] = {}
    for canonical, aliases in PRODUCT_SEARCH_ALIASES.items():
        key = "One and One" if canonical in {"one and one", "one & one"} else canonical.title()
        products.setdefault(key, set()).update(aliases)
    for canonical, aliases in products.items():
        rows.append(
            {"entity_type": "product", "canonical_name": canonical, "aliases": sorted(aliases)}
        )
    parallels: dict[str, set[str]] = {}
    for canonical, alias in PARALLEL_SEARCH_ALIASES.items():
        key = canonical.removesuffix(" parallel").title()
        parallels.setdefault(key, set()).update((canonical, alias))
    for canonical, aliases in parallels.items():
        rows.append(
            {"entity_type": "parallel", "canonical_name": canonical, "aliases": sorted(aliases)}
        )
    for canonical, aliases in ATTRIBUTE_ALIASES.items():
        rows.append(
            {"entity_type": "attribute", "canonical_name": canonical, "aliases": list(aliases)}
        )
    indexed = {(row["entity_type"], row["canonical_name"]): row for row in rows}
    for addition in CURATED_ENTITIES:
        key = (addition["entity_type"], addition["canonical_name"])
        if key in indexed:
            indexed[key]["aliases"] = sorted(
                set(indexed[key]["aliases"]) | set(addition["aliases"])
            )
        else:
            rows.append(addition)
            indexed[key] = addition
    for key, aliases in CURATED_ALIAS_ADDITIONS.items():
        if key in indexed:
            indexed[key]["aliases"] = sorted(set(indexed[key]["aliases"]) | set(aliases))
    return rows
