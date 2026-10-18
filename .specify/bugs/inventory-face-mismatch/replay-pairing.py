"""Read-only reproduction from frozen local flatbed input signals.

PYTHONPATH=apps/api/src .venv/bin/python .specify/bugs/inventory-face-mismatch/replay-pairing.py
Requires private .runtime/inventory-face-mismatch/provenance.json.
"""
import json
from pathlib import Path
from card_platform.flatbed_matching import CardRegion,propose_pairs
ROOT=Path(__file__).resolve().parents[3]
rows=json.loads((ROOT/'.runtime/inventory-face-mismatch/provenance.json').read_text())
for num in [657,809,846]:
 sample=next(r for r in rows if r['display_id']==num)
 group=[r for r in rows if r['pair'] and r['pair']['session_id']==sample['pair']['session_id']]
 def region(r):
  b=r['bounds']
  return CardRegion(r['id'],b['x'],b['y'],b['width'],b['height'],r['confidence'],r['signals'] or {},r['failure_reason'])
 fronts=sorted({r['front_region']['id']:r['front_region'] for r in group}.values(),key=lambda r:r['ordinal'])
 backs=sorted({r['back_region']['id']:r['back_region'] for r in group}.values(),key=lambda r:r['ordinal'])
 proposal=propose_pairs([region(r) for r in fronts],[region(r) for r in backs])
 before={(r['front_region']['id'],r['back_region']['id']) for r in group}
 after={(r['front_id'],r['back_id']) for r in proposal['pairs']}
 assert before==after, f'{num}: historical proposal changed'
 print(json.dumps(dict(representative_id=num,pair_count=len(after),transform=proposal['transform'],historical_pairs_reproduced=True,requires_review=proposal['requires_review'])))
