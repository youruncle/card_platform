import re


HARD_LOT_PATTERN = re.compile(
    r"(?:\blot\b|打包|多卡|多张同出|\d+\s*张同出|整套|未拆|卡盒|盒卡|卡包|箱卡|"
    r"\bpack\b|\bbox\b)",
    re.IGNORECASE,
)
AMBIGUOUS_SET_CARD_PATTERN = re.compile(r"套卡", re.IGNORECASE)
MEMORABILIA_PATTERN = re.compile(r"球衣|物料|切割|patch|jersey|relic|\brpa\b", re.I)
STRONG_MEMORABILIA_PATTERN = re.compile(
    r"物料|切割|patch|jersey|relic|\brpa\b|球衣\s*(?:卡|窗|物料|切割)", re.I
)
UNIFORM_DESCRIPTION_PATTERN = re.compile(
    r"(?:主场|客场|公牛|湖人|球队|身穿).{0,12}(?:\d{1,2}\s*号)?球衣(?:选图)?",
    re.I,
)


def is_multi_card_listing(title: str, product_name: object = None) -> bool:
    """Detect lots while preserving audited single-card seller vocabulary.

    CardHobby sellers use ``银标套卡`` for individual cards from Upper Deck's
    MJ's Back set.  The phrase is ambiguous in isolation, so it is exempted only
    when both the catalog product and the listing title independently identify
    MJ's Back.  Explicit quantity, lot, sealed-product, or full-set language
    remains a hard rejection.
    """
    if HARD_LOT_PATTERN.search(title):
        return True
    if not AMBIGUOUS_SET_CARD_PATTERN.search(title):
        return False
    normalized_product = re.sub(r"[^0-9a-z]+", " ", str(product_name or "").casefold()).strip()
    normalized_title = re.sub(r"[^0-9a-z]+", " ", title.casefold()).strip()
    return not (
        "mj s back" in normalized_product
        and "mj s back" in normalized_title
        and "银标套卡" in title
    )


def is_memorabilia_listing(title: str) -> bool:
    """Separate a depicted uniform from an actual memorabilia card.

    Chinese sellers often describe the uniform visible in the photograph with
    phrases such as ``公牛客场23号球衣`` or ``主场球衣选图``.  Those phrases do
    not imply that the card contains jersey material.  Strong material/window
    vocabulary remains authoritative.
    """
    if STRONG_MEMORABILIA_PATTERN.search(title):
        return True
    if UNIFORM_DESCRIPTION_PATTERN.search(title):
        return False
    return bool(MEMORABILIA_PATTERN.search(title))
