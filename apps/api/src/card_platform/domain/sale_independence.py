"""Conservative physical-copy keys; gallery/boilerplate images are never keys."""
import re
from decimal import Decimal

RULE_VERSION = 'physical-sale-independence-1'


def seller_key(row):
    source = row.get('provider_raw_payload') or row.get('raw_payload') or {}
    seller = str(source.get('seller_id') or '').strip()
    return f'{row.get("source_code")}:{seller}' if seller else None


def independence_confidence_cap(sales):
    sellers = {sale.seller_key for sale in sales if sale.seller_key}
    if len(sales) == 1 or min(sale.age_days for sale in sales) > 180:
        return Decimal('.45')
    if all(sale.seller_key for sale in sales) and len(sellers) == 1:
        return Decimal('.45')
    if len(sellers) < 3:
        return Decimal('.65')
    return Decimal('.90')


def full_serial(title: str) -> tuple[int, int] | None:
    # Exclude release seasons and dates. A title fraction is only a duplicate
    # risk key inside one card identity; it cannot establish a priced identity.
    from card_platform.domain.number_roles import serial_non_unique_role
    if serial_non_unique_role([title]):
        return None
    stamp = bool(re.search(r'serial|stamp|foil|限编|烫印|机器流水|\d\s*编', title, re.I))
    pairs = {(int(n), int(t)) for n, t in re.findall(
        r'(?<![\d/])0*(\d{1,6})\s*/\s*0*(\d{1,6})(?![\d/])', title)
        if 0 < int(n) <= int(t) <= 100000
        and not (1900 <= int(n) <= 2100 or (not stamp and 10 <= int(n) < 100 and int(t) - int(n) == 1))}
    return next(iter(pairs)) if len(pairs) == 1 else None


def physical_keys(row: dict) -> list[tuple[str, str]]:
    payload = row.get('raw_payload') or {}
    keys = []
    binding = payload.get('visual_evidence_binding') or {}
    digest = binding.get('candidate_sha256')
    try:
        confidence = Decimal(str(payload.get('visual_confidence') or 0))
        confidence = confidence if confidence.is_finite() else Decimal(0)
    except (ValueError, ArithmeticError):
        confidence = Decimal(0)
    if (payload.get('visual_verdict') == 'same'
            and confidence >= Decimal('.9')
            and payload.get('visual_match_evidence')
            and re.fullmatch(r'[0-9a-f]{64}', str(digest or ''))):
        keys.append(('same_card_photo_reuse_risk', digest))
    serial = full_serial(str(row.get('listing_title') or ''))
    if serial:
        keys.append(('same_physical_card_serial', f'{serial[0]}/{serial[1]}'))
    # Only explicit seller-scoped consignment codes, never arbitrary trailing
    # warehouse IDs or similar photographs alone, establish duplicate risk.
    source = row.get('provider_raw_payload') or payload
    seller = str(source.get('seller_id') or '').strip()
    code = str(source.get('consignment_code') or '').strip()
    if seller and code:
        keys.append(('same_seller_consignment', f'{row.get("source_code")}:{seller}:{code}'))
    return keys


def cluster_copy_records(records):
    """Qualified records only. Evidence links and owned status are transitive."""
    parents = list(range(len(records)))
    seen = {}
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for i, record in enumerate(records):
        for key in record['keys']:
            if key in seen:
                a, b = root(i), root(seen[key])
                parents[max(a,b)] = min(a,b)
            else:
                seen[key] = i
    owned_roots = {root(i) for i, record in enumerate(records) if record.get('owned')}
    kept, excluded = [], []
    for i, record in enumerate(records):
        anchor = root(i)
        if anchor in owned_roots:
            excluded.append((i, None, 'owned_copy_history'))
        elif anchor == i:
            kept.append(i)
        else:
            reason = next((k[0] for k in record['keys'] if k in records[anchor]['keys']),
                          'same_physical_card_cluster')
            excluded.append((i, anchor, reason))
    return kept, excluded


def unique_physical_rows(rows: list[dict], identity: dict | None = None):
    """Call only after source/identity admission; never merge rejected variants."""
    owned = ((identity or {}).get('serial_number'), (identity or {}).get('serial_total'))
    owned = tuple(int(x) for x in owned) if all(owned) else None
    records = [dict(keys=physical_keys(row), owned=owned is not None and
                    full_serial(str(row.get('listing_title') or '')) == owned) for row in rows]
    kept, dropped = cluster_copy_records(records)
    excluded = [dict(dropped_id=str(rows[i].get('id')),
        kept_id=str(rows[anchor].get('id')) if anchor is not None else None,
        reason_code=reason, source_url=rows[i].get('source_url'),
        reason=('本件历史' if reason == 'owned_copy_history' else
                '照片复用风险，独立性未证，仅计一次' if reason == 'same_card_photo_reuse_risk' else
                '同实卡或再上风险，仅保留一条独立样本'))
        for i, anchor, reason in dropped]
    return [rows[i] for i in kept], excluded
