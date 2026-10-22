"""Unqualified public source observations, separate from per-card evidence."""
from datetime import datetime, timezone
import hashlib
import json

from card_platform.public_image_ingestion import public_source_pair


CONTRACT = 'public-sold-api-observation-v1'
# Deliberately exclude accounts, search identities, private images and API headers.
PUBLIC_FIELDS = frozenset({
    'ID', 'Title', 'EffectiveDate', 'LowestPrice', 'NewPostageMoney',
    'OutCount', 'PriceCount', 'ByWay', 'Status', 'TitImg',
})
# Added after v1 captures already existed. Only absent, uninformative defaults
# are schema-compatible; captured values and original transaction facts stay exact.
LEGACY_OPTIONAL_DEFAULTS = {
    'seller_id': None, 'consignment_code': None,
    'source_metadata': {}, 'source_gallery': {},
}


def _row_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def observe_public_sale(row):
    from card_platform.ingestion.cardhobby import CardHobbyProvider
    if not isinstance(row, dict):
        return None
    public = {key: value for key, value in row.items() if key in PUBLIC_FIELDS}
    sale = CardHobbyProvider.parse_market_item(public)
    if sale is None:
        return None
    try:
        public_source_pair(dict(provider_code='cardhobby', source_url=sale.source_url,
                                image_url=sale.image_url))
    except ValueError:
        return None
    return dict(contract=CONTRACT, provider_code='cardhobby', discovery_only=True,
        captured_at=datetime.now(timezone.utc).isoformat(), raw_row=public,
        raw_row_sha256=_row_hash(public), sale=sale.model_dump(mode='json'))


def validated_sale(observation):
    from card_platform.ingestion.cardhobby import CardHobbyProvider
    try:
        captured = datetime.fromisoformat(observation['captured_at'])
        raw = observation['raw_row']
        if (observation['contract'] != CONTRACT or observation['provider_code'] != 'cardhobby'
                or observation['discovery_only'] is not True or captured.tzinfo is None
                or captured > datetime.now(timezone.utc) or set(raw) - PUBLIC_FIELDS
                or _row_hash(raw) != observation['raw_row_sha256']):
            raise ValueError('invalid_public_observation')
        sale = CardHobbyProvider.parse_market_item(raw)
        current = sale.model_dump(mode='json') if sale is not None else None
        captured_sale = observation['sale']
        if isinstance(current, dict) and isinstance(captured_sale, dict):
            for key, default in LEGACY_OPTIONAL_DEFAULTS.items():
                if key not in captured_sale and current.get(key) == default:
                    current.pop(key, None)
        if sale is None or current != captured_sale:
            raise ValueError('public_sale_facts_changed')
        public_source_pair(dict(provider_code='cardhobby', source_url=sale.source_url,
                                image_url=sale.image_url))
        return sale, captured
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('invalid_public_observation') from error


async def store_observations(connection, observations):
    from psycopg.types.json import Jsonb
    # Validate the complete batch before writes, including when called without a transaction.
    verified = [(obs, *validated_sale(obs)) for obs in observations]
    for observation, sale, captured in verified:
        await connection.execute('''
            INSERT INTO public_source_catalog
              (provider_code, external_sale_id, source_url, image_url, observation, captured_at)
            VALUES ('cardhobby', %s, %s, %s, %s, %s)
            ON CONFLICT (provider_code, external_sale_id) DO UPDATE SET
              source_url=EXCLUDED.source_url, image_url=EXCLUDED.image_url,
              observation=EXCLUDED.observation, captured_at=EXCLUDED.captured_at
            WHERE EXCLUDED.captured_at > public_source_catalog.captured_at
            ''', ('item:' + sale.item_id, sale.source_url, sale.image_url, Jsonb(observation), captured))
    return len(verified)


async def evidence_for_source_ids(connection, ids):
    from card_platform.market_data.models import EvidenceRecord
    if not ids:
        return []
    result = await connection.execute(
        "SELECT observation FROM public_source_catalog WHERE provider_code='cardhobby' "
        "AND external_sale_id IN (" + ','.join('%s' for _ in ids) + ")", tuple(ids))
    records = []
    for row in await result.fetchall():
        sale, captured = validated_sale(row['observation'])
        records.append(EvidenceRecord(provider_code='cardhobby', external_sale_id='item:' + sale.item_id,
            raw_title=sale.title, sale_price=sale.sale_price, shipping_price=sale.shipping_price,
            currency='CNY', occurred_at=sale.occurred_at, source_url=sale.source_url,
            evidence_level=sale.evidence_level, grade_company=sale.grade_company,
            grade_value=sale.grade_value, match_confidence=0, fetched_at=captured,
            identity_classification={'source_reliability': str(sale.source_reliability)},
            raw_payload={**sale.model_dump(mode='json'), 'public_observation': {
                'contract': CONTRACT, 'discovery_only': True,
                'raw_row_sha256': row['observation']['raw_row_sha256'],
                'captured_at': row['observation']['captured_at']}}))
    return records
