"""Roles require local evidence; a numeric shape alone cannot establish rarity."""
import re

ROLE_VERSION = 'number-roles-1'


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
    role = r'(?:球衣[号號]|jersey\s*(?:number|no\.?|#)|(?:外部|价格|售卖|卡套|套袋)?贴纸|貼紙|sticker\s*(?:label|number)|production\s*code|版权生产码)'
    for text in evidence:
        text = re.sub(r'(?:不是|并非|並非|not\s+)[^，。；;,.]*', '', str(text), flags=re.I)
        if re.search(role + r'[^\dA-Za-z，。；;]{0,8}' + re.escape(token)
                     + r'(?![\dA-Za-z])', text, re.I):
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
