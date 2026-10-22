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


_VERSION_FIELDS = (
    'card_definition_id', 'brand_name', 'season_start', 'season_end', 'product_name',
    'set_name', 'card_number', 'insert_name', 'parallel_name', 'serial_total',
    'is_autograph', 'is_memorabilia', 'is_patch', 'is_redemption', 'redemption_status',
)


def _version_values(row):
    from card_platform.domain.card_identity import normalize_text
    values = dict(row.get('identity') or {})
    values.update({key: row[key] for key in _VERSION_FIELDS if key in row})
    return {key: normalize_text(value) for key, value in values.items()
            if key in _VERSION_FIELDS and value is not None}


def _same_printed_version(row, identity):
    # Callers first admit the full current identity/finish. Explicit differing
    # versions still must not share a copy key or claim the target's serial.
    tier = row.get('comparable_tier')
    if tier is not None and str(tier) not in {'T0', 'T1'}:
        return False
    target, candidate = _version_values(identity), _version_values(row)
    return not any(target[key] != value for key, value in candidate.items() if key in target)


def _certificate_key(row):
    source = row.get('provider_raw_payload') or {}
    company = str(row.get('grade_company') or source.get('grade_company') or '').strip().casefold()
    certificate = str(row.get('grade_cert_number') or source.get('grade_cert_number') or '').strip().casefold()
    # Only explicit structured certificate fields; never arbitrary title/order
    # numbers or a common company+grade label. Companies have separate namespaces.
    if company and re.fullmatch(r'[a-z0-9-]{4,80}', certificate):
        return f'{company}:{certificate}'
    return None


def owned_copy_history(row: dict, identity: dict | None = None) -> bool:
    """Whether an admitted current source is this held physical card's history.

    A cached classification boolean, notes URL or purchase amount is not proof.
    The source binding must validate for this owner/item; serial/certificate
    comparisons apply only after full same-version admission by the caller.
    """
    from card_platform.purchase_source_roles import is_owned_purchase_source
    identity = identity or {}
    source = row.get('provider_raw_payload') or {}
    if is_owned_purchase_source(identity, {'source_url': row.get('source_url') or source.get('source_url')}):
        return True
    if not _same_printed_version(row, identity):
        return False
    certificate = _certificate_key(row)
    if certificate and certificate == _certificate_key(identity):
        return True
    try:
        owned = (int(identity['serial_number']), int(identity['serial_total']))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return 0 < owned[0] <= owned[1] and full_serial(str(row.get('listing_title') or '')) == owned


def physical_keys(row: dict, identity: dict | None = None) -> list[tuple[str, str]]:
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
    certificate = _certificate_key(row)
    if certificate:
        keys.append(('same_physical_card_certificate', certificate))
    # Only explicit seller-scoped consignment codes, never arbitrary trailing
    # warehouse IDs or similar photographs alone, establish duplicate risk.
    source = row.get('provider_raw_payload') or payload
    seller = str(source.get('seller_id') or '').strip()
    code = str(source.get('consignment_code') or '').strip()
    if seller and code:
        keys.append(('same_seller_consignment', f'{row.get("source_code")}:{seller}:{code}'))
    # The worker supplies one fully admitted version. Keep explicit row scopes
    # separate as well: repeated serial/cert digits across products are not copies.
    scope = {**_version_values(identity or {}), **_version_values(row)}
    prefix = repr(tuple(sorted(scope.items())))
    tier = row.get('comparable_tier')
    contextual = tier is not None and str(tier) not in {'T0', 'T1'}
    contextual_prefix = prefix
    owned_photo = False
    if contextual:
        # Contextual rows can reach _calculate without hard conflicts, but their
        # complete finish/version is unproven. Purchase ownership proves this
        # source's history, not its title serial/certificate/consignment claims.
        # Only its already observed primary photo may propagate that ownership.
        contextual_scope = {**scope, 'non_same_card_source': str(row.get('id') or row.get('source_url') or '')}
        contextual_prefix = repr(tuple(sorted(contextual_scope.items())))
        owned_photo = owned_copy_history(row, identity)
    scoped_keys = []
    for kind, value in keys:
        share_owned_photo = owned_photo and kind == 'same_card_photo_reuse_risk'
        key_prefix = contextual_prefix if contextual and not share_owned_photo else prefix
        scoped_keys.append((kind, f'{key_prefix}:{value}'))
    # A marketplace item is one transaction even when legacy external IDs,
    # URL casing, or contextual classifications differ. It is independent of
    # serial/version keys and never links two different items of one seller.
    from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url
    transaction = canonical_public_item_url(row.get('source_url'))
    if transaction:
        scoped_keys.append(('same_underlying_marketplace_sale', transaction))
    return scoped_keys


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
    records = [dict(keys=physical_keys(row, identity), owned=owned_copy_history(row, identity))
               for row in rows]
    kept, dropped = cluster_copy_records(records)
    excluded = [dict(dropped_id=str(rows[i].get('id')),
        kept_id=str(rows[anchor].get('id')) if anchor is not None else None,
        reason_code=reason, source_url=rows[i].get('source_url'),
        reason=('本件历史' if reason == 'owned_copy_history' else
                '照片复用风险，独立性未证，仅计一次' if reason == 'same_card_photo_reuse_risk' else
                '同实卡或再上风险，仅保留一条独立样本'))
        for i, anchor, reason in dropped]
    return [rows[i] for i in kept], excluded
