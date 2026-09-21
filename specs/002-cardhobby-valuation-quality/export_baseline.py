"""Read-only audit export. Run from repository root; writes only a NEW local directory.

PYTHONPATH=apps/api/src .venv/bin/python specs/002-cardhobby-valuation-quality/export_baseline.py --out work/audits/valuation-design-20260921/frozen
Contains private collection evidence: keep output under ignored work/, never publish it.
"""
import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from card_platform.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    out = args.out.resolve()
    if not out.is_relative_to(root / 'work'):
        raise SystemExit('Output must be in ignored work/')
    out.mkdir(parents=True, exist_ok=False)
    with psycopg.connect(Settings().database_url, row_factory=dict_row,
                         options='-c default_transaction_read_only=on -c statement_timeout=30000') as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        owners = conn.execute('SELECT DISTINCT owner_id FROM collection_item WHERE deleted_at IS NULL AND disposed_at IS NULL').fetchall()
        if len(owners) != 1:
            raise SystemExit('Requires explicit owner selection when multiple owners exist')
        owner = owners[0]['owner_id']
        items = conn.execute('''
          SELECT i.id AS collection_item_id, i.display_id, i.card_definition_id,
            i.image_front_url, i.image_back_url, i.serial_number, i.serial_total,
            i.grade_company, i.grade_value, i.condition_note, i.is_numbered,
            p.full_name AS player_name, s.season_start, s.season_end,
            b.name AS brand_name, s.product_name, s.set_name,
            d.card_number, d.insert_name, d.parallel_name, d.is_rookie,
            d.is_autograph, d.is_memorabilia, d.is_patch, d.print_run,
            d.metadata AS identity_metadata, i.created_at,
            row_to_json(j) AS latest_job, row_to_json(v) AS latest_snapshot,
            (SELECT count(*) FROM valuation_snapshot h WHERE h.collection_item_id=i.id) AS snapshot_count
          FROM collection_item i JOIN card_definition d ON d.id=i.card_definition_id
          JOIN card_set s ON s.id=d.card_set_id JOIN brand b ON b.id=s.brand_id
          JOIN player p ON p.id=d.player_id
          LEFT JOIN LATERAL (SELECT * FROM valuation_job WHERE collection_item_id=i.id
            ORDER BY created_at DESC,id DESC LIMIT 1) j ON true
          LEFT JOIN LATERAL (SELECT * FROM valuation_snapshot WHERE collection_item_id=i.id
            ORDER BY valued_at DESC,created_at DESC,id DESC LIMIT 1) v ON true
          WHERE i.owner_id=%s AND i.deleted_at IS NULL AND i.disposed_at IS NULL
          ORDER BY i.display_id
        ''', (owner,)).fetchall()
        definitions = list({i['card_definition_id'] for i in items})
        comps = conn.execute('''SELECT c.*, ds.code AS source_code,
            e.raw_payload AS provider_raw_payload, e.fetched_at AS evidence_fetched_at
          FROM market_comp c JOIN data_source ds ON ds.id=c.data_source_id
          LEFT JOIN market_evidence e ON e.id=c.market_evidence_id
          WHERE c.card_definition_id=ANY(%s) AND ds.code='cardhobby'
          ORDER BY c.id''', (definitions,)).fetchall()
        job_ids = [i['latest_job']['result_summary'].get('audit_baseline_job_id')
                   for i in items if i['latest_job'] and i['latest_job'].get('result_summary')]
        jobs = conn.execute('SELECT * FROM valuation_job WHERE id=ANY(%s::uuid[]) ORDER BY id',
                            ([j for j in job_ids if j],)).fetchall()
        item_ids = [i['collection_item_id'] for i in items]
        identity_versions = conn.execute('SELECT * FROM card_identity_version WHERE collection_item_id=ANY(%s) ORDER BY collection_item_id,version', (item_ids,)).fetchall()
        recognition = conn.execute('SELECT DISTINCT ON (collection_item_id) collection_item_id, result, created_at FROM scanner_recognition_job WHERE collection_item_id=ANY(%s) AND result IS NOT NULL ORDER BY collection_item_id,created_at DESC', (item_ids,)).fetchall()
        frozen_at = conn.execute('SELECT transaction_timestamp() AS time').fetchone()['time']
    for item in items:
        item['image_hashes'] = {}
        for side in ('front', 'back'):
            url = item.get('image_' + side + '_url') or ''
            name = url.removeprefix('/uploads/')
            candidates = [root/'uploads'/name, root/'apps/api/uploads'/name]
            path = next((p for p in candidates if p.is_file()), None) if '/' not in name else None
            item['image_hashes'][side] = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        job = item['latest_job'] or {}
        snap = item['latest_snapshot']
        item['effective_status'] = ('succeeded' if snap and str(snap['valued_at']) > str(job.get('created_at') or '') else job.get('status', 'unvalued'))
        item['current_fair_value'] = snap['fair_value'] if snap and item['effective_status']=='succeeded' else None
        item['historical_model_cost'] = None
        item['historical_model_cost_missing_reason'] = 'No per-call token usage and price-version ledger in exported job/snapshot schema'
    counts = Counter(i['effective_status'] for i in items)
    stats = {'active_cards':len(items),'effective_status':dict(counts),
             'never_had_snapshot':sum(i['snapshot_count']==0 for i in items),
             'cardhobby_comp_rows':len(comps),
             'latest_job_status':dict(Counter((i['latest_job'] or {}).get('status','none') for i in items)),
             'latest_failure_stage':dict(Counter((i['latest_job'] or {}).get('result_summary',{}).get('valuation_failure_diagnostics',{}).get('stage','unrecorded') for i in items if i['effective_status']!='succeeded'))}
    payload = {'frozen_at':frozen_at,'scope':'single active owner; nondeleted, nondisposed',
               'transaction':'REPEATABLE READ READ ONLY','git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
               'stats':stats,'items':items,'cardhobby_comps':comps,'prior_audit_jobs':jobs,
               'identity_versions':identity_versions,'latest_scanner_results':recognition}
    baseline = out/'baseline.json'
    baseline.write_text(json.dumps(payload,default=str,ensure_ascii=False,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({'baseline_sha256':hashlib.sha256(baseline.read_bytes()).hexdigest(),
        'exported_at':datetime.now(timezone.utc).isoformat(),'stats':stats},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
