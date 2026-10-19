"""Roles require local evidence; a numeric shape alone cannot establish rarity."""
import re

ROLE_VERSION = 'number-roles-4'


def _positive_role_claims(text: object) -> str:
    return re.sub(r'(?:并不是|不是|并非|並非|\bnot\s+)[^，。；;,.]*', '', str(text), flags=re.I)


def _first_appearance_number_spans(value: object, text: str, *, year: bool = False):
    token = str(value or '').strip().lstrip('#').strip()
    if not re.fullmatch(r'\d+', token):
        return
    pattern = (
        r'(?:first\s+appearance|首次(?:漫画|漫畫)?(?:出场|出場|登场|登場|出现|出現))'
        r'[^;；。\n]{0,100}?\(?\s*((?:19|20)\d{2})\s*\)?\s*#\s*(\d+)(?![\dA-Za-z])'
    )
    group = 1 if year else 2
    for match in re.finditer(pattern, text, re.I):
        if int(match.group(group)) == int(token):
            yield match.span(group)


def first_appearance_number_role(value: object, evidence: list[str], *, year: bool = False) -> bool:
    """Bind a biography's comic year/issue to that token, never a nearby card ID."""
    return any(span for text in evidence for span in _first_appearance_number_spans(
        value, _positive_role_claims(text), year=year))


def _external_handwritten_number_spans(token: str, text: str):
    number = r'(?P<number>' + re.escape(token) + r')(?![\dA-Za-z])'
    bound = r'(?<![\dA-Za-z])' + number
    role = (r'(?:(?:外部|外写|外寫|卡套|套袋)[^;；。\n\d]{0,8}手[写寫]|'
            r'external\s+handwritten|(?:sleeve|holder)\s+handwritten)')
    for pattern in (
        role + r'[^\dA-Za-z;；。\n]{0,8}(?:(?:no\.?|card\s*(?:number|no\.?|#))\s*)?' + number,
        bound + r'[\s\'"“”‘’]*(?:是|为|為|is)\s*' + role,
        r'handwritten\s*[\'"“”‘’]*' + bound
        + r'[\'"“”‘’]*\s+on\s+(?:the\s+)?(?:sleeve|holder)',
    ):
        for match in re.finditer(pattern, text, re.I):
            yield match.span('number')


def is_layout_label(value: object) -> bool:
    return bool(re.fullmatch(
        r'(?:(?:upper|lower|top|bottom)[\s_-]*(?:left|right|center)|(?:左|右)(?:上|下)(?:角|方)?)',
        str(value or '').strip(), re.I))


def non_catalog_number_role(value: object, evidence: list[str]) -> bool:
    # Bind the role to the proposed token, not an unrelated sticker/jersey
    # elsewhere on the same card. Negative descriptions do not assert a role.
    token = str(value or '').strip()
    if not token:
        return False
    if is_layout_label(token) or re.fullmatch(r'(?:CODE\s*#?\s*)?CMP\d{5,}', token, re.I):
        return True
    claims = [_positive_role_claims(text) for text in evidence]
    novel_roles = [list(_first_appearance_number_spans(token, text))
                   + list(_external_handwritten_number_spans(token, text)) for text in claims]
    if any(novel_roles):
        # The same digits may independently be a real card ID and a comic issue
        # or sleeve mark. Bind each occurrence, without splitting the dot in No.9
        # or depending on which punctuation/conjunction joins the observations.
        explicit_catalog = (r'(?:card\s*(?:number|no\.?|#)|(?<![A-Za-z])no\.?|卡[号號])'
                            r'\s*[:：#\'"“”‘’]*\s*(?P<number>' + re.escape(token) + r')(?![\dA-Za-z])')
        if not any(not any(start < match.end('number') and match.start('number') < end
                           for start, end in roles)
                   for text, roles in zip(claims, novel_roles)
                   for match in re.finditer(explicit_catalog, text, re.I)):
            return True
    role = (r'(?:球衣[号號](?:码|碼)?|jersey\s*(?:number|no\.?|#)|'
            r'(?:外部|价格|售卖|卡套|套袋)?贴纸|貼紙|壳贴(?:号码)?|'
            r'sticker\s*(?:label|number)|(?:版权)?生产(?:串码|代码|码)|'
            r'厂印串码|制造串码|production\s*code|事件日期|event\s*date)')
    bound_token = r'(?<![\dA-Za-z])' + re.escape(token) + r'(?![\dA-Za-z])'
    for text in evidence:
        text = re.sub(r'(?:不是|并非|並非|not\s+)[^，。；;,.]*', '', str(text), flags=re.I)
        if (re.search(role + r'[^\dA-Za-z，。；;,.]{0,8}' + bound_token, text, re.I)
                or re.search(bound_token + r'''[\s'"“”‘’]*(?:是|为|為|is|系因|来自|來自)\s*(?:底部|背面|正面|外部)?\s*'''
                             + role, text, re.I)):
            return True
    return False


def serial_non_unique_role(evidence: list[str], *, number=None, total=None) -> str | None:
    for text in evidence:
        # Explicit physical stamp is a separate positive observation. Do not
        # let an unrelated set-position sentence erase a valid stamped serial.
        for clause in re.split(r'[;；。\n]', str(text)):
            clause = re.sub(r'not\s+(?:individually|uniquely)\s+numbered', 'print quantity', clause, flags=re.I)
            clause = re.sub(r'(?:不是|并非|並非|not\s+)[^，。；;,.]*', '', clause, flags=re.I)
            pairs = {(int(n),int(t)) for n,t in re.findall(r'(\d+)\s*(?:/|\bof\b)\s*(\d+)', clause, re.I)}
            if number is not None and total is not None and (int(number),int(total)) not in pairs:
                continue
            if re.search(r'套组(?:内|序|编号)|套組(?:內|序|編號)|set\s*(?:position|sequence)|(?:card|number)\s+\d+\s+of\s+\d+\s+(?:in|within)', clause, re.I):
                return 'set_position'
            if re.search(r'印量说明|印量說明|每张同样|每張同樣|print\s*quantity|not\s+(?:individually|uniquely)\s+numbered', clause, re.I):
                return 'print_quantity'
    return None
