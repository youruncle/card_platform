"""Conservative parsing of serial-number stamps from visible card evidence."""

from collections.abc import Iterable
from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class VisibleSerial:
    number: int
    total: int
    evidence: str


_SERIAL_FRACTION = re.compile(
    r"(?<![\d/\uff0f\u2215\u2044])0*(\d{1,6})\s*"
    r"(?P<separator>[/\uff0f\u2215\u2044]|\bof\b)\s*0*(\d{1,6})"
    r"(?![\dA-Za-z])",
    re.IGNORECASE,
)
_DATE_CONTINUATION = re.compile(r"^\s*[/\uff0f\u2215\u2044.-]\s*\d{1,4}\b")
_FULL_DATE_CONTEXT = re.compile(
    r"\b\d{1,2}\s*[/\uff0f\u2215\u2044.-]\s*"
    r"[/\uff0f\u2215\u2044.-]?\s*\d{1,2}\s*"
    r"[/\uff0f\u2215\u2044.-]\s*\d{2,4}\b"
)
_PROSE_MONTH_DAY = re.compile(
    r"(?:\b(?:on|by|in)\b\s*|[A-Za-z][A-Za-z .'-]{1,40},\s*)"
    r"(?:[\d|Il]\s*[/\uff0f\u2215\u2044|]\s*){0,2}"
    r"\d{1,2}\s*[/\uff0f\u2215\u2044]\s*\d{1,2}\b",
    re.IGNORECASE,
)
_OCR_DATE_PREFIX = re.compile(
    r"\b(?:on|by|in)\b[\d|Il\s/\uff0f\u2215\u2044.-]{0,20}$",
    re.IGNORECASE,
)
_PHYSICAL_CONTEXT = re.compile(
    r"(?:serial(?:\s*(?:number|stamp|foil))?|numbered|print\s*run|"
    r"card\s*(?:front|back)|\bfront\b|\bback\b|\bocr\b|printed|visible|foil\s*stamp|"
    r"正面|背面|卡面|卡背|印刷|可见|清晰|竖排|横排|左上|右上|左下|右下|"
    r"限编|编号)",
    re.IGNORECASE,
)
_REMOTE_CONTEXT = re.compile(
    r"(?:联网候选|目录核验|validated[_ ]anchors?|tcdb|beckett|checklist|"
    r"official\s+(?:checklist|database)|权威(?:来源|图鉴|渠道))",
    re.IGNORECASE,
)
_SPECULATIVE_CONTEXT = re.compile(
    r"(?:推测|推断|猜测|可能|疑似|假设|"
    r"\b(?:infer(?:red|ence)?|guess(?:ed)?|possibly|probably|"
    r"assum(?:e|ed|ption))\b)",
    re.IGNORECASE,
)
_NEGATIVE_CONTEXT = re.compile(
    r"(?:(?:\bno\b|\bnot\b|\bwithout\b|absence\s+of|未见|没有|无|不是|并非)"
    r"[^.;；。\n]{0,32}(?:serial|numbered|限编|编号|x/y|fraction)|"
    r"(?:serial|numbered|限编|编号|x/y|fraction)"
    r"[^.;；。\n]{0,32}(?:\bno\b|\bnot\b|\bwithout\b|absence|未见|没有|无|不是|并非))",
    re.IGNORECASE,
)
_NON_CARD_MARK_CONTEXT = re.compile(
    r"(?:手写|handwritten|套袋|sleeve|外部标记|external\s+mark)",
    re.IGNORECASE,
)
_DATE_LABEL = re.compile(
    r"(?:\bbirth(?:day|\s*date)?\b|\bborn\b|\bd\.?o\.?b\.?\b|"
    r"\bdate\b|\bdebut\b|"
    r"生日|出生(?:日期)?|日期|首秀)",
    re.IGNORECASE,
)
_OCR_BIRTH_LABEL = re.compile(r"\b(?:bone|bane|bant)\s*[:：]", re.IGNORECASE)
_STAT_CONTEXT = re.compile(
    r"(?:\bfg\b|field\s*goals?|shooting|\bpoints?\b|\brebounds?\b|"
    r"\bassists?\b|\bstats?\b|\brecord\b|投篮|命中|得分|篮板|助攻|战绩)",
    re.IGNORECASE,
)
_STAT_TABLE_CONTEXT = re.compile(
    r"(?:\b(?:fg|ft|3p)\s*%|\b(?:career|nba)\s+totals?\b|"
    r"(?:投篮|罚球|三分)命中率)",
    re.IGNORECASE,
)
_DENSE_NUMERIC_ROW = re.compile(
    r"(?:^|\s)\d+\s*[/\uff0f\u2215\u2044]\s*\d+"
    r"(?:\s+\d+(?:\.\d+)?){2,}(?:\s|$)"
)
_CARD_NUMBER_CONTEXT = re.compile(
    r"(?:card\s*(?:number|no\.?|#)|卡号|(?:^|\s)#)\s*$",
    re.IGNORECASE,
)
_EXPLICIT_SERIAL_CONTEXT = re.compile(
    r"(?:serial(?:\s*(?:number|stamp|foil))?|numbered|print\s*run|"
    r"foil\s*stamp|限编|序列编号)",
    re.IGNORECASE,
)


def _candidate_clause(text: str, start: int, end: int) -> tuple[str, str]:
    # OCR provenance includes normalized float coordinates. A decimal point
    # inside 0.128 is not a sentence boundary: splitting it loses the physical
    # source label and silently clears a valid stamp when stored data is read.
    boundaries = list(re.finditer(r"[;；。!?！？\n]|(?<!\d)\.|\.(?!\d)", text))
    clause_start = max(
        (boundary.end() for boundary in boundaries if boundary.end() <= start),
        default=0,
    )
    clause_end = min(
        (boundary.start() for boundary in boundaries if boundary.start() >= end),
        default=len(text),
    )
    return text[clause_start:clause_end], text[clause_start:start]


def _candidate_is_visible_serial(
    text: str,
    match: re.Match[str],
    *,
    source_is_physical: bool,
) -> bool:
    number = int(match.group(1))
    total = int(match.group(3))
    if not 0 < number <= total <= 100_000:
        return False
    plausible_month_day = number <= 12 and total <= 31
    # A third numeric component is a date-shaped token even when OCR corrupts
    # the month above 12 (for example ``BORN16/19/78``).  It is never enough
    # evidence for a two-component print-run stamp.
    if _DATE_CONTINUATION.match(text[match.end() :]):
        return False

    context, prefix = _candidate_clause(text, match.start(), match.end())
    raw_prefix = text[max(0, match.start() - 32) : match.start()]
    separator = match.group("separator").casefold()

    # Remote/speculative markers describe the provenance of the whole evidence
    # record, not merely the punctuation-delimited clause containing x/y.  A
    # producer must not be able to turn a catalogue guess into physical proof
    # by appending a sentence such as "the back shows 20/20".
    if _REMOTE_CONTEXT.search(text):
        return False
    if _SPECULATIVE_CONTEXT.search(text):
        return False
    if _NEGATIVE_CONTEXT.search(context):
        return False
    if _NON_CARD_MARK_CONTEXT.search(context):
        return False
    if (
        plausible_month_day
        and (
            _FULL_DATE_CONTEXT.search(text)
            or _PROSE_MONTH_DAY.search(text)
            or _OCR_DATE_PREFIX.search(prefix)
        )
        and not _EXPLICIT_SERIAL_CONTEXT.search(text)
    ):
        return False
    if _STAT_CONTEXT.search(context) and not _EXPLICIT_SERIAL_CONTEXT.search(context):
        return False
    if (
        _STAT_TABLE_CONTEXT.search(text) or _DENSE_NUMERIC_ROW.search(context)
    ) and not _EXPLICIT_SERIAL_CONTEXT.search(context):
        return False
    if _CARD_NUMBER_CONTEXT.search(raw_prefix) and not _EXPLICIT_SERIAL_CONTEXT.search(raw_prefix):
        return False

    # A labelled date always disqualifies the fraction it introduces, even if
    # OCR collapsed a full date into something that no longer looks like a
    # month/day (for example ``BORN: 2/1763``).  A later, explicitly labelled
    # serial stamp in the same clause is still independent physical evidence.
    date_labels = list(_DATE_LABEL.finditer(prefix))
    # Low-contrast birth rows can lose the second slash and corrupt BORN.
    # Restrict these OCR label variants to a collapsed month/day/year shape;
    # a player's name or an ordinary three-digit print run is not a date.
    denominator_digits = match.group(3)
    collapsed_birth_date = (
        number <= 12
        and len(denominator_digits) in (4, 5)
        and 1 <= int(denominator_digits[:2]) <= 31
    )
    if collapsed_birth_date:
        date_labels.extend(_OCR_BIRTH_LABEL.finditer(prefix))
        date_labels.sort(key=lambda label: label.start())
        # Preserve a birth-row label when a recrop corrupts it still further
        # (including another alphabet) or duplicates one digit in the year.
        # A nearby player's surname alone is not enough: require label + colon
        # + the same month, and never override an independently labelled stamp.
        for label in _OCR_BIRTH_LABEL.finditer(text):
            birth_month = re.match(r"\s*(\d{1,2})\s*[/／∕⁄]", text[label.end():])
            if (
                birth_month
                and int(birth_month.group(1)) == number
                and not _EXPLICIT_SERIAL_CONTEXT.search(context)
            ):
                return False
        # A local recrop can turn 2/17/63 into 2/1763. Preserve the full-image
        # context instead of treating the recrop as independent serial proof.
        for date in _FULL_DATE_CONTEXT.finditer(text):
            parts = re.findall(r"\d+", date.group())
            if (
                len(parts) == 3
                and int(parts[0]) == number
                and int(parts[1] + parts[2]) == total
                and not _EXPLICIT_SERIAL_CONTEXT.search(context)
            ):
                return False
    if date_labels:
        serial_labels = list(_EXPLICIT_SERIAL_CONTEXT.finditer(prefix))
        if not serial_labels or serial_labels[-1].start() < date_labels[-1].start():
            return False
    if (
        plausible_month_day
        and _DATE_LABEL.search(context)
        and not _EXPLICIT_SERIAL_CONTEXT.search(context)
    ):
        return False

    if separator == "of":
        # "20 of 20" is common prose/statistics.  Accept it only when the
        # evidence itself identifies a physical serial/print-run stamp.
        return bool(_EXPLICIT_SERIAL_CONTEXT.search(context))
    return (
        source_is_physical
        or bool(_PHYSICAL_CONTEXT.search(context))
        or bool(
            re.fullmatch(
                r"\s*0*\d{1,6}\s*[/\uff0f\u2215\u2044]\s*0*\d{1,6}\s*",
                text,
            )
        )
    )


def visible_serial_evidence(
    texts: Iterable[str],
    *,
    source_is_physical: bool = False,
) -> list[VisibleSerial]:
    """Return candidate x/y stamps backed by local, non-speculative evidence.

    Adjacent OCR rows are also considered so vertically arranged or fragmented
    ``x / y`` text can be recovered.  Repeated observations are retained here;
    callers that need a safe single answer should use :func:`unique_visible_serial`.
    """

    rows = [text.strip() for text in texts if isinstance(text, str) and text.strip()]
    blocks = list(rows)
    for size in (2, 3):
        for index in range(len(rows) - size + 1):
            block = "\n".join(rows[index : index + size])
            if re.fullmatch(
                r"\s*\d{1,6}\s*[/\uff0f\u2215\u2044]\s*\d{1,6}\s*",
                block,
            ):
                blocks.append(block)

    candidates: list[VisibleSerial] = []
    seen: set[tuple[int, int, str]] = set()
    for text in blocks:
        for match in _SERIAL_FRACTION.finditer(text):
            if not _candidate_is_visible_serial(
                text,
                match,
                source_is_physical=source_is_physical,
            ):
                continue
            candidate = VisibleSerial(
                number=int(match.group(1)),
                total=int(match.group(3)),
                evidence=text,
            )
            key = (candidate.number, candidate.total, candidate.evidence)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
    return candidates


def unique_visible_serial(
    texts: Iterable[str],
    *,
    source_is_physical: bool = False,
) -> VisibleSerial | None:
    """Return one unambiguous serial pair, accepting duplicate observations."""

    candidates = visible_serial_evidence(
        texts,
        source_is_physical=source_is_physical,
    )
    pairs = {(candidate.number, candidate.total) for candidate in candidates}
    if len(pairs) != 1:
        return None
    number, total = next(iter(pairs))
    return next(
        candidate
        for candidate in candidates
        if (candidate.number, candidate.total) == (number, total)
    )


def serial_field_evidence(field_evidence: dict[str, list[str]]) -> list[str]:
    """Collect equivalent serial provenance fields used by model/OCR producers."""

    return [
        text
        for field in ("is_numbered", "serial_number", "serial_total")
        for text in (field_evidence.get(field) or [])
        if isinstance(text, str)
    ]
