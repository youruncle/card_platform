"""Owner-scoped discovery. Never used to adjudicate valuation/cost identity."""

from decimal import Decimal
from collections import OrderedDict
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from card_platform.domain.player_aliases import PLAYER_SEARCH_ALIASES, player_roster_components

Name = Annotated[str, Field(min_length=1, max_length=80)]
Names = Annotated[list[Name], Field(max_length=20)]
Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Status = Literal["valued", "unvalued", "queued", "running", "failed", "insufficient_evidence"]


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    holding_status: Literal["held", "sold"] | None = None
    q: str = Field(default="", max_length=160)
    player: Names = Field(default_factory=list)
    brand: Names = Field(default_factory=list)
    product: Names = Field(default_factory=list)
    insert: Names = Field(default_factory=list)
    parallel: Names = Field(default_factory=list)
    card_number: Names = Field(default_factory=list)
    grade_company: Names = Field(default_factory=list)
    valuation_status: list[Status] = Field(default_factory=list, max_length=6)
    year_min: int | None = Field(default=None, ge=1800, le=2200)
    year_max: int | None = Field(default=None, ge=1800, le=2200)
    serial_number: int | None = Field(default=None, ge=1)
    serial_min: int | None = Field(default=None, ge=1)
    serial_max: int | None = Field(default=None, ge=1)
    grade_min: Decimal | None = Field(default=None, ge=0, le=10)
    grade_max: Decimal | None = Field(default=None, ge=0, le=10)
    cost_min: Money | None = None
    cost_max: Money | None = None
    value_min: Money | None = None
    value_max: Money | None = None
    rookie: bool | None = None
    autograph: bool | None = None
    memorabilia: bool | None = None
    numbered: bool | None = None
    graded: bool | None = None
    has_cost: bool | None = None

    @model_validator(mode="after")
    def valid_ranges(self):
        for name in ("year", "serial", "grade", "cost", "value"):
            low, high = getattr(self, name + "_min"), getattr(self, name + "_max")
            if low is not None and high is not None and low > high:
                raise ValueError(f"{name}: 最小值不能大于最大值")
        return self


# Discovery-only vocabulary; ambiguous abbreviations are never identity proof.
SEARCH_ALIASES = {
    "player": [
        *((name, *aliases) for name, aliases in PLAYER_SEARCH_ALIASES.items()),
        ("Kevin Durant", "凯文杜兰特", "凯文·杜兰特", "杜兰特", "KD"),
        ("Kyrie Irving", "凯里欧文", "凯里·欧文", "欧文", "欧神仙"),
    ],
    "parallel": [("Black and White", "黑白", "黑白折")],
}
FIELDS = {
    "player": "player_name",
    "brand": "brand_name",
    "product": "product_name",
    "insert": "insert_name",
    "parallel": "parallel_name",
}


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(c for c in value if c.isalnum() and not unicodedata.combining(c))


class SearchVocabulary:
    def __init__(self, snapshot=None):
        self.index = {}
        self.han_terms = set()
        groups = [(kind, group) for kind, values in SEARCH_ALIASES.items() for group in values]
        for entity in (snapshot or {}).get("entities", []):
            kind = entity.get("entity_type")
            if kind in FIELDS:
                groups.append(
                    (kind, (entity.get("canonical_name") or "", *(entity.get("aliases") or [])))
                )
        for kind, group in groups:
            members = player_roster_components(group[0]) if kind == "player" else ()
            # A roster can be discovered by any participant, but a solo card must
            # not inherit the other participants as aliases of its own identity.
            member_names = (
                {
                    normalize(alias)
                    for member in members
                    for alias in (member, *PLAYER_SEARCH_ALIASES.get(member.casefold(), ()))
                }
                if len(members) > 1
                else set()
            )
            for name in group:
                normalized = normalize(name)
                if re.fullmatch(r"[\u3400-\u9fff]{2,}", normalized):
                    self.han_terms.add(normalized)
                if normalized in member_names:
                    continue
                self.index.setdefault((kind, normalized), set()).update(group)

    def values(self, kind, value):
        return (str(value or ""), *self.index.get((kind, normalize(value)), ()))

    def terms(self, query):
        plain = "".join(c for c in unicodedata.normalize("NFKD", query).casefold()
                        if not unicodedata.combining(c))
        tokens = re.findall(r"[\u3400-\u9fff]+|[0-9]+[-/][0-9]+|[a-z0-9]+", plain)
        terms = []
        for token in tokens:
            if not re.fullmatch(r"[\u3400-\u9fff]+", token):
                terms.append(token)
                continue
            # Longest reviewed alias first, retaining unmatched text as a term.
            # This supports Chinese input without requiring spaces or an NLP service.
            position, pending = 0, ""
            while position < len(token):
                match = next((token[position:end] for end in range(len(token), position + 1, -1)
                              if token[position:end] in self.han_terms), None)
                if match:
                    if pending:
                        terms.append(pending)
                        pending = ""
                    terms.append(match)
                    position += len(match)
                else:
                    pending += token[position]
                    position += 1
            if pending:
                terms.append(pending)
        return list(dict.fromkeys(terms))


# Published vocabulary contains no owner-specific cards. Version IDs change on
# publication, so a small cache avoids rebuilding it for every private search.
_vocabulary_cache = OrderedDict()


async def search_vocabulary(connection):
    result = await connection.execute(
        "SELECT published_version_id FROM knowledge_workspace WHERE name='Card Platform'"
    )
    row = await result.fetchone()
    version = row["published_version_id"] if row else None
    if version in _vocabulary_cache:
        _vocabulary_cache.move_to_end(version)
        return _vocabulary_cache[version]
    entities = []
    if version:
        result = await connection.execute(
            """SELECT entity->>'entity_type' AS entity_type,
                      entity->>'canonical_name' AS canonical_name,
                      entity->'aliases' AS aliases
               FROM knowledge_version kv,
                    jsonb_array_elements(kv.snapshot->'entities') AS entity
               WHERE kv.id=%s AND entity->>'entity_type'=ANY(%s::text[])""",
            (version, list(FIELDS)),
        )
        entities = await result.fetchall()
    vocabulary = SearchVocabulary({"entities": entities})
    _vocabulary_cache[version] = vocabulary
    while len(_vocabulary_cache) > 2:
        _vocabulary_cache.popitem(last=False)
    return vocabulary


def one_edit(left, right):
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        differences = [i for i, (a, b) in enumerate(zip(left, right, strict=True)) if a != b]
        return len(differences) <= 1 or (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and left[differences[0]] == right[differences[1]]
            and left[differences[1]] == right[differences[0]]
        )
    short, long = sorted((left, right), key=len)
    i = next((i for i in range(len(short)) if short[i] != long[i]), len(short))
    return short[i:] == long[i + 1 :]


def text_match(query, values, *, fuzzy=True):
    needle = normalize(query)
    if not needle:
        return False
    if any(needle in normalize(value) for value in values):
        return True
    # Small Latin spelling errors only; never fuzzy IDs, Chinese characters or numbers.
    if fuzzy and re.fullmatch(r"[a-z]{4,}", needle):
        return any(
            one_edit(needle, word)
            for value in values
            for word in re.findall(r"[a-z]{5,}", unicodedata.normalize("NFKD", value).casefold())
        )
    return False


def in_range(value, low, high):
    if low is None and high is None:
        return True
    return value is not None and (low is None or value >= low) and (high is None or value <= high)


def matches(card, filters, snapshot=None):
    if filters.holding_status is not None and card.get("holding_status", "held") != filters.holding_status:
        return False
    vocabulary = snapshot if isinstance(snapshot, SearchVocabulary) else SearchVocabulary(snapshot)
    expanded = {key: vocabulary.values(key, card.get(field)) for key, field in FIELDS.items()}
    for key, values in expanded.items():
        requested = getattr(filters, key)
        if requested and not any(text_match(v, values) for v in requested):
            return False
    for key in ("card_number", "grade_company"):
        requested = getattr(filters, key)
        if requested and normalize(card.get(key)) not in {normalize(v) for v in requested}:
            return False
    flags = {
        "rookie": card.get("is_rookie"),
        "autograph": card.get("is_autograph"),
        "memorabilia": card.get("is_memorabilia"),
        "numbered": card.get("is_numbered"),
        "graded": bool(card.get("grade_company")),
        "has_cost": card.get("purchase_total_cost") is not None,
    }
    for key, value in flags.items():
        selected = getattr(filters, key)
        if selected is not None and value is not selected:
            return False
    if filters.serial_number is not None and card.get("serial_number") != filters.serial_number:
        return False
    start, end = card.get("season_start"), card.get("season_end") or card.get("season_start")
    if filters.year_min is not None and (end is None or end < filters.year_min):
        return False
    if filters.year_max is not None and (start is None or start > filters.year_max):
        return False
    for prefix, field in (
        ("serial", "serial_total"),
        ("grade", "grade_value"),
        ("cost", "purchase_total_cost"),
        ("value", "fair_value"),
    ):
        if not in_range(
            card.get(field), getattr(filters, prefix + "_min"), getattr(filters, prefix + "_max")
        ):
            return False
    if filters.valuation_status:
        states = {
            card.get("valuation_status"),
            "valued" if card.get("fair_value") is not None else "unvalued",
        }
        if card.get("valuation_status") in {"identifying", "searching", "calculating"}:
            states.add("running")
        if not states.intersection(filters.valuation_status):
            return False
    return not filters.q.strip() or keyword_score(card, filters.q, vocabulary) > 0


def keyword_score(card, query, vocabulary):
    """Discovery ranking only: partial terms never become identity evidence."""
    query = query.strip()
    exact_id = re.fullmatch(r"(?:id\s*[:#]?\s*|#)?(\d+)", query, re.I)
    if exact_id:
        return 1000 if card.get("display_id") == int(exact_id[1]) else 0
    # Punctuation separates terms; accents/case do not affect recall. Keep
    # serial/year forms together so 7/50 does not become two broad numbers.
    terms = vocabulary.terms(query)
    if not terms:
        return 0
    fields = []
    for kind, weight in (("player", 6), ("product", 4), ("insert", 4), ("parallel", 4), ("brand", 2)):
        values = vocabulary.values(kind, card.get(FIELDS[kind]))
        normalized = tuple({normalize(v) for v in values if v})
        words = {word for v in values for word in re.findall(r"[a-z]{4,}", unicodedata.normalize("NFKD", v).casefold())}
        fields.append((weight, normalized, words))
    start, end = card.get("season_start"), card.get("season_end") or card.get("season_start")
    exact_values = {normalize(card.get(k)) for k in ("card_number", "grade_company", "grade_value", "season_start", "season_end", "serial_number", "serial_total", "display_id")}
    if start is not None and end is not None:
        exact_values.add(normalize(f"{start}-{str(end)[-2:]}"))
    if card.get("serial_number") is not None and card.get("serial_total") is not None:
        exact_values.add(normalize(f"{card['serial_number']}/{card['serial_total']}"))
    if normalize(query) in exact_values:
        # Preserve literal card numbers such as CH-5 even though Latin hyphens
        # split free-text queries like Durant-Topps into independent keywords.
        return 1000
    score, text_hit = 0, False
    has_text = any(re.search(r"[a-z\u3400-\u9fff]", t) for t in terms)
    for term in terms:
        needle = normalize(term)
        textual = bool(re.search(r"[a-z\u3400-\u9fff]", term))
        quality = 0
        if textual:
            for weight, values, words in fields:
                if needle in values:
                    quality = max(quality, weight * 3)
                elif any(needle in value for value in values):
                    quality = max(quality, weight * 2)
                elif re.fullmatch(r"[a-z]{4,}", term) and any(one_edit(term, word) for word in words):
                    quality = max(quality, weight)
        if needle in exact_values:
            quality = max(quality, 3)
        if quality:
            score += 100 + quality
            text_hit = text_hit or textual
    # A shared year alone must not return cards for an unrelated player query.
    if has_text and not text_hit:
        return 0
    phrase = normalize(query)
    if any(phrase == value for _, values, _ in fields for value in values):
        score += 30
    return score


def structured_sql_filters(filters):
    """Safe prefilters; text/alias recall and ranking remain in Python."""
    clauses, parameters = [], []

    def compare(column, operator, value):
        if value is not None:
            clauses.append(f"{column} {operator} %s")
            parameters.append(value)

    compare("ci.holding_status", "=", filters.holding_status)
    for name, column in (
        ("rookie", "cd.is_rookie"), ("autograph", "cd.is_autograph"),
        ("memorabilia", "cd.is_memorabilia"), ("numbered", "ci.is_numbered"),
    ):
        compare(column, "=", getattr(filters, name))
    if filters.graded is not None:
        clauses.append("COALESCE(ci.grade_company, '') " + ("<> ''" if filters.graded else "= ''"))
    if filters.has_cost is not None:
        clauses.append("purchase.total IS " + ("NOT NULL" if filters.has_cost else "NULL"))
    compare("ci.serial_number", "=", filters.serial_number)
    compare("COALESCE(cs.season_end, cs.season_start)", ">=", filters.year_min)
    compare("cs.season_start", "<=", filters.year_max)
    for prefix, column in (("serial", "ci.serial_total"), ("grade", "ci.grade_value"), ("cost", "purchase.total")):
        compare(column, ">=", getattr(filters, prefix + "_min"))
        compare(column, "<=", getattr(filters, prefix + "_max"))
    return "".join(" AND " + clause for clause in clauses), tuple(parameters)


async def search_item_ids(connection, owner_id, filters):
    if set(filters.model_dump(exclude_defaults=True)) == {"holding_status"}:
        result = await connection.execute(
            """SELECT id FROM collection_item
               WHERE owner_id=%s AND deleted_at IS NULL AND holding_status=%s
               ORDER BY created_at DESC,id DESC""",
            (owner_id, filters.holding_status),
        )
        return [row["id"] for row in await result.fetchall()]
    vocabulary = await search_vocabulary(connection)
    sql_filters, parameters = structured_sql_filters(filters)
    result = await connection.execute(
        f"""
        SELECT ci.id,ci.display_id,ci.holding_status,ci.is_numbered,ci.serial_number,ci.serial_total,
               ci.grade_company,ci.grade_value,p.full_name AS player_name,b.name AS brand_name,
               cs.product_name,cs.season_start,cs.season_end,cd.insert_name,cd.parallel_name,
               cd.card_number,cd.is_rookie,cd.is_autograph,cd.is_memorabilia,
               purchase.total AS purchase_total_cost,
               CASE WHEN lv.currency=u.base_currency AND
                 NOT COALESCE(vj.created_at>lv.valued_at AND vj.status<>'succeeded',false)
                 THEN lv.fair_value END AS fair_value,
               CASE WHEN lv.valued_at>vj.created_at THEN 'succeeded' ELSE vj.status END AS valuation_status
        FROM collection_item ci
        JOIN app_user u ON u.id=ci.owner_id
        JOIN card_definition cd ON cd.id=ci.card_definition_id
        JOIN card_set cs ON cs.id=cd.card_set_id
        JOIN brand b ON b.id=cs.brand_id
        JOIN player p ON p.id=cd.player_id
        LEFT JOIN latest_item_valuation lv ON lv.collection_item_id=ci.id
        LEFT JOIN LATERAL (
          SELECT (unit_price*quantity+fee_amount)*fx_rate_to_base AS total FROM card_transaction
          WHERE collection_item_id=ci.id AND transaction_type='buy'
          ORDER BY transacted_at,created_at,id LIMIT 1
        ) purchase ON true
        LEFT JOIN LATERAL (
          SELECT status,created_at FROM valuation_job WHERE collection_item_id=ci.id
          ORDER BY created_at DESC LIMIT 1
        ) vj ON true
        WHERE ci.owner_id=%s AND ci.deleted_at IS NULL {sql_filters}
        ORDER BY ci.created_at DESC,ci.id DESC
        """,
        (owner_id, *parameters),
    )
    strict_filters = filters.model_copy(update={"q": ""})
    ranked = []
    for row in await result.fetchall():
        if not matches(row, strict_filters, vocabulary):
            continue
        score = keyword_score(row, filters.q, vocabulary) if filters.q else 1
        if score > 0:
            ranked.append((score, row["id"]))
    # SQL order is the deterministic tie-breaker (newest first, then UUID).
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item_id for _, item_id in ranked]
