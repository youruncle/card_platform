"""Promote existing independent Codex analyses when no current valuation exists.

No model or market requests are made here. The immutable analysis remains the
source of truth; this only makes its price available to portfolio valuation.
"""
import asyncio
import hashlib
import logging
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)
MODEL_VERSION = 'codex-independent-fallback-v1'


async def promote_codex_valuation(connection, item_id: UUID, upload_dir: Path, *, dry_run=False) -> dict:
    async with connection.transaction():
        item = await (await connection.execute("""
            SELECT ci.*, u.base_currency FROM collection_item ci
            JOIN app_user u ON u.id=ci.owner_id
            WHERE ci.id=%s AND ci.deleted_at IS NULL AND ci.holding_status='held'
            FOR UPDATE OF ci SKIP LOCKED
        """, (item_id,))).fetchone()
        result = {'collection_item_id': str(item_id)}
        if not item:
            return {**result, 'status': 'unavailable_or_locked'}
        result['display_id'] = item['display_id']
        current = await (await connection.execute("""
            SELECT id FROM valuation_snapshot WHERE collection_item_id=%s
              AND identity_compatibility<>'incompatible' LIMIT 1
        """, (item_id,))).fetchone()
        if current:
            return {**result, 'status': 'already_valued'}
        active = await (await connection.execute("""
            SELECT id FROM valuation_job WHERE collection_item_id=%s
              AND status IN ('queued','identifying','searching','calculating') LIMIT 1
        """, (item_id,))).fetchone()
        if active:
            return {**result, 'status': 'market_job_active'}
        # Select the latest analysis first; never fall back to an older price
        # when the latest Codex analysis failed or removed its price.
        source = await (await connection.execute("""
            SELECT s.*, j.owner_id AS analysis_owner_id,
                   j.collection_item_id AS job_item_id
            FROM card_analysis_snapshot s JOIN card_analysis_job j ON j.id=s.job_id
            WHERE s.collection_item_id=%s AND s.source='codex_multimodal'
            ORDER BY s.analysis_version DESC LIMIT 1
        """, (item_id,))).fetchone()
        if not source:
            return {**result, 'status': 'no_codex_analysis'}
        result['source_analysis_id'] = str(source['id'])
        if (source['status'] != 'succeeded' or source['analysis_owner_id'] != item['owner_id']
                or source['job_item_id'] != item_id):
            return {**result, 'status': 'invalid_source'}
        raw = source['raw_result'] or {}
        if raw.get('collection_item_id') and str(raw['collection_item_id']) != str(item_id):
            return {**result, 'status': 'wrong_item_binding'}
        prices = [source[k] for k in ['fair_value_low', 'fair_value', 'fair_value_high']]
        if (any(p is None or not p.is_finite() for p in prices)
                or not Decimal(0) <= prices[0] <= prices[1] <= prices[2]):
            return {**result, 'status': 'invalid_price_range'}
        if source['currency'] != item['base_currency']:
            return {**result, 'status': 'currency_mismatch'}
        previous = await (await connection.execute("""
            SELECT id FROM valuation_snapshot WHERE collection_item_id=%s
              AND evidence->>'source_analysis_id'=%s LIMIT 1
        """, (item_id, str(source['id'])))).fetchone()
        if previous:
            return {**result, 'status': 'source_already_used'}
        if source['identity_version_id']:
            identity = await (await connection.execute("""
                SELECT iv.id FROM card_identity_version iv WHERE iv.collection_item_id=%s
                  AND iv.review_status='confirmed'
                  AND NOT EXISTS (SELECT 1 FROM card_identity_version revoked
                      WHERE revoked.supersedes_id=iv.id AND revoked.review_status='rejected')
                ORDER BY iv.version DESC LIMIT 1
            """, (item_id,))).fetchone()
            if not identity or identity['id'] != source['identity_version_id']:
                return {**result, 'status': 'identity_changed'}
        # Older imported analyses have no identity-version binding. Their
        # current original-image hashes bind them to the physical card instead.
        hashes = {}
        root = upload_dir.resolve()
        for side in ['front', 'back']:
            url = item[f'image_{side}_url']
            expected = raw.get(f'{side}_sha256')
            if not url or not url.startswith('/uploads/') or not expected:
                return {**result, 'status': f'{side}_image_unbound'}
            path = (root / url.removeprefix('/uploads/')).resolve()
            if not path.is_relative_to(root):
                return {**result, 'status': f'{side}_image_unbound'}
            try:
                content = await asyncio.to_thread(path.read_bytes)
            except OSError:
                return {**result, 'status': f'{side}_image_unavailable'}
            actual = hashlib.sha256(content).hexdigest()
            if actual != expected:
                return {**result, 'status': f'{side}_image_changed'}
            hashes[side] = actual
        confidence = min(source['valuation_confidence'] if source['valuation_confidence'] is not None
                         else Decimal('.25'), Decimal('.35'))
        evidence = {
            'strategy': '自动估值暂无可用结果，采用已有 Codex 独立估值（低置信参考）',
            'pricing_basis': 'codex_independent_fallback',
            'source': 'codex_multimodal',
            'source_analysis_id': str(source['id']),
            'source_analysis_version': source['analysis_version'],
            'source_model_name': source['model_name'],
            'source_analyzed_at': source['created_at'].isoformat(),
            'source_valuation_confidence': str(source['valuation_confidence']) if source['valuation_confidence'] is not None else None,
            'identity_version_id': str(source['identity_version_id']) if source['identity_version_id'] else None,
            'source_identity_summary': source['identity_summary'],
            'image_sha256': hashes,
            'formula': '直接沿用 Codex 原始低值、公允值及高值；不作成交价换算。',
            'used_comparables': [], 'comparables': [], 'evidence_count': 0,
            'notes': [
                f"来源：Codex 第 {source['analysis_version']} 版独立分析，分析时间 {source['created_at'].isoformat()}。",
                '此为已有 Codex 判断，未经本轮市场成交验证；置信度不高于35%，后续市场估值成功后可更新。',
                *[str(r) for r in (source['risks_and_conflicts'] or [])],
            ],
        }
        result.update(low_value=str(prices[0]), fair_value=str(prices[1]), high_value=str(prices[2]),
                      confidence=str(confidence), currency=source['currency'])
        if dry_run:
            return {**result, 'status': 'eligible'}
        inserted = await (await connection.execute("""
            INSERT INTO valuation_snapshot(collection_item_id,method,currency,
                low_value,fair_value,high_value,confidence,evidence,model_version)
            VALUES(%s,'ai_estimate',%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (item_id, source['currency'], *prices, confidence, Jsonb(evidence), MODEL_VERSION))).fetchone()
        return {**result, 'status': 'promoted', 'valuation_id': str(inserted['id'])}


async def backfill_codex_valuations(database, upload_dir: Path, *, dry_run=False) -> list[dict]:
    async with database.connection() as connection:
        rows = await (await connection.execute("""
            SELECT ci.id FROM collection_item ci
            WHERE ci.deleted_at IS NULL AND ci.holding_status='held'
              AND NOT EXISTS (SELECT 1 FROM valuation_snapshot v
                  WHERE v.collection_item_id=ci.id AND v.identity_compatibility<>'incompatible')
              AND EXISTS (SELECT 1 FROM card_analysis_snapshot s
                  WHERE s.collection_item_id=ci.id AND s.source='codex_multimodal')
            ORDER BY ci.display_id
        """)).fetchall()
    results = []
    for row in rows:
        try:
            async with database.connection() as connection:
                results.append(await promote_codex_valuation(connection, row['id'], upload_dir, dry_run=dry_run))
        except Exception:
            logger.exception('Codex fallback failed for item %s', row['id'])
            results.append({'collection_item_id': str(row['id']), 'status': 'error'})
    return results


async def run_codex_fallback_worker(database, upload_dir: Path) -> None:
    """Also handles analyses imported after an automated valuation failed."""
    while True:
        try:
            results = await backfill_codex_valuations(database, upload_dir)
            count = sum(r['status'] == 'promoted' for r in results)
            if count:
                logger.info('Filled %s missing current valuations from Codex analyses', count)
        except Exception:
            logger.exception('Codex fallback sweep failed')
        await asyncio.sleep(60)
