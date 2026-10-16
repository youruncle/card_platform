"""Keep ownership provenance separate from publicly verified auction evidence."""
import re
from uuid import UUID
from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url

CONTRACT='owned-purchase-source-v1'
REJECTION='own_purchase_not_independent_market_evidence'

# Read only persisted assignments; never infer ownership from seller text, cost,
# arbitrary reference URLs, or another card's match to this shared market source.
PURCHASE_SOURCE_LATERAL="""
LEFT JOIN LATERAL (
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'contract','owned-purchase-source-v1',
        'owner_id',assignment.owner_id,'collection_item_id',assignment.collection_item_id,
        'assignment_id',assignment.id,'evidence_hash',assignment.evidence_hash,
        'source_url',assignment.evidence->>'item_url') ORDER BY assignment.id),'[]'::jsonb) AS sources
    FROM cardhobby_cost_assignment assignment
    JOIN cardhobby_purchase_unit unit ON unit.id=assignment.unit_id AND unit.owner_id=assignment.owner_id
    JOIN card_transaction purchase_transaction ON purchase_transaction.id=assignment.transaction_id
        AND purchase_transaction.collection_item_id=assignment.collection_item_id
        AND purchase_transaction.transaction_type='buy'
    WHERE assignment.collection_item_id=ci.id AND assignment.owner_id=ci.owner_id
        AND assignment.confirmation IN ('automatic','user_confirmed','manual_override')
        AND assignment.evidence->>'item_id'=unit.item_id
        AND assignment.evidence->>'evidence_hash'=assignment.evidence_hash
) purchase_sources ON true
"""


def validated_purchase_sources(rows,*,owner_id,collection_item_id):
    if not owner_id or not collection_item_id or not isinstance(rows,list):return []
    result=[]
    for row in rows:
        if not isinstance(row,dict):continue
        if (row.get('contract')!=CONTRACT or str(row.get('owner_id'))!=str(owner_id)
                or str(row.get('collection_item_id'))!=str(collection_item_id)
                or not re.fullmatch('[0-9a-f]{64}',str(row.get('evidence_hash','')))):
            continue
        try:assignment=str(UUID(str(row.get('assignment_id'))))
        except ValueError:continue
        url=canonical_public_item_url(row.get('source_url'))
        if not url:continue
        result.append({'contract':CONTRACT,'owner_id':str(owner_id),'collection_item_id':str(collection_item_id),
                       'assignment_id':assignment,'evidence_hash':row['evidence_hash'],'source_url':url})
    return result


def is_owned_purchase_source(identity,payload):
    source=canonical_public_item_url((payload or {}).get('source_url'))
    if not source:return False
    bindings=validated_purchase_sources(identity.get('purchase_sources'),
        owner_id=identity.get('owner_id'),collection_item_id=identity.get('collection_item_id'))
    return any(x['source_url']==source for x in bindings)


def purchase_source_rejection_reasons(identity,payload):
    if not is_owned_purchase_source(identity,payload):return []
    # Ownership alone neither proves nor disproves a public market price.
    # Require public auction facts, never substitute the owner's recorded cost.
    from card_platform.ingestion.cardhobby import CardHobbySale, cardhobby_item_id
    from card_platform.domain.auction_evidence import public_auction_rejection_reasons
    try:
        sale=CardHobbySale.model_validate(payload)
    except (TypeError,ValueError):return [REJECTION]
    if cardhobby_item_id(sale.source_url)!=sale.item_id:return [REJECTION]
    reasons=public_auction_rejection_reasons(occurred_at=sale.occurred_at,
        bid_count=sale.bid_count,sale_type=sale.sale_type,evidence_level=sale.evidence_level,
        fx_rate_to_base=sale.fx_rate_to_base)
    return [REJECTION] if reasons else []
