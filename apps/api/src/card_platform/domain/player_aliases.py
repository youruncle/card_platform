"Shared deterministic player aliases; query-only ambiguous names are excluded from matching."

import re

PLAYER_SEARCH_ALIASES = {
    "jaren jackson jr": ("小贾伦 杰克逊", "贾伦 杰克逊", "JJJ"),
    "lebron james": ("勒布朗 詹姆斯", "勒布朗詹姆斯", "詹姆斯"),
    "stephen curry": ("斯蒂芬 库里", "库里"),
    "scottie barnes": ("斯科蒂 巴恩斯", "巴恩斯"),
    "brandon miller": ("布兰登 米勒",),
    "luka dončić": ("卢卡 东契奇", "东契奇"),
    "luka doncic": ("卢卡 东契奇", "东契奇"),
    "kevin garnett": ("凯文 加内特", "加内特"),
    "matas buzelis": ("马塔斯 布泽利斯", "布泽利斯"),
    "tristan da silva": ("特里斯坦 达席尔瓦", "达席尔瓦"),
    "matas buzelis tristan da silva": (
        "Matas Buzelis",
        "Tristan da Silva",
        "马塔斯 布泽利斯 特里斯坦 达席尔瓦",
        "布泽利斯 达席尔瓦",
    ),
    "victor wembanyama": ("维克托 文班亚马", "文班亚马"),
    "tracy mcgrady": ("特雷西 麦克格雷迪", "麦迪", "T-MAC"),
    "vj edge combe": ("VJ Edgecombe", "VJ 埃奇库姆", "埃奇库姆"),
    "vj edgecombe": ("VJ Edge Combe", "VJ 埃奇库姆", "埃奇库姆"),
    "scoot henderson": ("斯库特 亨德森", "亨德森"),
    "zach lavine": ("扎克 拉文", "拉文"),
    "jalen duren": ("杰伦 杜伦", "杜伦"),
    "brandon ingram": ("布兰登 英格拉姆", "英格拉姆"),
    "shaedon sharpe": ("谢登 夏普", "夏普"),
    "paolo banchero": ("保罗 班凯罗", "班凯罗"),
    "shareef abdur rahim": ("谢里夫 阿卜杜 拉希姆", "拉希姆"),
    "grant hill": ("格兰特 希尔",),
    "toni kukoc": ("托尼 库科奇", "托尼 库克奇", "库科奇"),
    "jerome williams": ("杰罗姆 威廉姆斯",),
    "tim thomas": ("蒂姆 托马斯",),
    "tony delk": ("托尼 德尔克",),
    "john stockton": ("约翰 斯托克顿", "斯托克顿"),
    "glenn robinson": ("格伦 罗宾逊", "大狗"),
    "kobe bryant": ("科比 布莱恩特", "科比"),
    "shaquille o neal": ("沙奎尔 奥尼尔", "奥尼尔", "大鲨鱼"),
    "magic johnson": ("埃尔文 约翰逊", "魔术师 约翰逊", "魔术师"),
    "jayson tatum": ("杰森 塔图姆", "塔图姆"),
    "jason kidd": ("杰森基德", "贾森基德", "杰森 基德", "贾森 基德", "基德"),
    "hakeem olajuwon": ("哈基姆 奥拉朱旺", "奥拉朱旺", "大梦"),
    "andrew nembhard": ("安德鲁 内姆哈德", "内姆哈德", "内姆布哈德"),
    "patrick ewing": ("帕特里克 尤因", "尤因"),
    "joel embiid": ("乔尔 恩比德", "恩比德"),
    "jaylen brown": ("杰伦 布朗", "杰伦布朗"),
    "alonzo mourning": ("阿朗佐 莫宁", "莫宁"),
    "stephon marbury": ("斯蒂芬 马布里", "马布里"),
    "chauncey billups": ("昌西 比卢普斯", "比卢普斯"),
    "elton brand": ("埃尔顿 布兰德", "布兰德"),
    "joan beringer": ("琼 贝兰热", "贝兰热"),
    "jason terry": ("杰森 特里", "杰森特里"),
    "alex sarr": ("亚历克斯 萨尔", "萨尔"),
    "nate ament": ("内特 阿门特", "阿门特"),
    "jamal murray": ("贾马尔 穆雷", "穆雷"),
    "derik queen": ("德里克 奎因", "奎因"),
    "eddie robinson": ("埃迪 罗宾逊", "埃迪罗宾逊"),
    "bryon russell": ("拜伦 拉塞尔", "拜伦拉塞尔"),
    "los angeles lakers": ("洛杉矶 湖人", "湖人"),
    "bronny james jr.": ("布朗尼 詹姆斯", "布朗尼詹姆斯", "布朗尼"),
    "bronny james jr": ("布朗尼 詹姆斯", "布朗尼詹姆斯", "布朗尼"),
    "anthony davis": ("安东尼 戴维斯", "安东尼戴维斯", "浓眉"),
}
# These nicknames are useful discovery terms but are not identity evidence on
# their own. For example, sellers call Josh Giddey “澳大利亚魔术师”; accepting
# the bare word 魔术师 as a Magic Johnson match would cross players.
PLAYER_MATCH_ALIAS_EXCLUSIONS = {
    "magic johnson": {"魔术师"},
    "lebron james": {"詹姆斯"},
    # “基德” is useful discovery vocabulary but is a substring of Kerry
    # Kittles' common Chinese name “基德尔斯”; require Jason/贾森 for identity.
    "jason kidd": {"基德"},
}


# Recognition and old imports can preserve a printed multi-player roster as one
# whitespace-separated string.  Keep its subjects structured here so a seller
# title naming only one subject cannot satisfy the identity gate as an alias of
# the whole roster.  This is domain vocabulary, not card-number/version logic.
PLAYER_ROSTER_COMPONENTS = {
    "matas buzelis tristan da silva": (
        "Matas Buzelis",
        "Tristan da Silva",
    ),
}


def player_roster_components(player_name: object) -> tuple[str, ...]:
    """Return independently matchable subjects for a player/roster label."""
    canonical = str(player_name or "").strip()
    if not canonical:
        return ()
    explicit = tuple(
        part.strip()
        for part in re.split(r",|\s+&\s+|\s+and\s+|\s+/\s+", canonical, flags=re.I)
        if part.strip()
    )
    if len(explicit) > 1:
        return explicit
    normalized = " ".join(canonical.casefold().split())
    return PLAYER_ROSTER_COMPONENTS.get(normalized, (canonical,))


def matching_player_aliases(normalized_name: str) -> tuple[str, ...]:
    excluded = {
        *PLAYER_MATCH_ALIAS_EXCLUSIONS.get(normalized_name, set()),
        *PLAYER_ROSTER_COMPONENTS.get(normalized_name, ()),
    }
    excluded_casefolded = {value.casefold() for value in excluded}
    return tuple(
        alias
        for alias in PLAYER_SEARCH_ALIASES.get(normalized_name, ())
        if alias.casefold() not in excluded_casefolded
    )


# Reviewed discovery vocabulary; these aliases do not establish target identity.
# Independent CardHobby listings: /market/item/106572373 and /market/item/100775394.
REVIEWED_QUERY_ALIASES = {"trae young": ("特雷 杨", "特雷杨", "吹杨")}


def query_player_aliases(player_name: object) -> tuple[str, ...]:
    normalized = " ".join(str(player_name or "").casefold().split())
    if normalized in REVIEWED_QUERY_ALIASES:
        return REVIEWED_QUERY_ALIASES[normalized]
    # Restrict OCR correction to mixed Latin/Cyrillic strings that resolve to
    # an entire reviewed entity. Never expose this substitution to matching.
    if re.search(r"[a-z]", normalized) and re.search(r"[а-я]", normalized):
        corrected = normalized.translate(str.maketrans({"е": "e", "а": "a", "о": "o", "с": "c", "р": "p", "х": "x", "у": "y"}))
        if corrected in REVIEWED_QUERY_ALIASES:
            return (corrected, *REVIEWED_QUERY_ALIASES[corrected])
    return ()
