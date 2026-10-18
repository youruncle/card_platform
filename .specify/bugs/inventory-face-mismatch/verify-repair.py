"""Read-only verification of actual DB images/identity against reviewed recovery."""
import asyncio,json,hashlib,sys
from pathlib import Path
from uuid import UUID
from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.identity_review_api import _item_identity,_identity
async def main():
 manifest=json.loads(Path(sys.argv[1]).read_text());settings=get_settings();db=Database(settings);await db.open();errors=[];checked=[]
 try:
  async with db.connection() as c:
   await c.execute('SET TRANSACTION READ ONLY')
   for p in manifest['records']:
    row=dict(await _item_identity(c,UUID(p['collection_item_id']),UUID(manifest['owner_id'])));identity=_identity(row)
    if p['back_action']=='clear':
     if row['image_back_url'] is not None:errors.append([p['display_id'],'back_not_cleared'])
    else:
     dest=settings.resolved_upload_dir/Path(row['image_back_url']).name
     actual=hashlib.sha256(dest.read_bytes()).hexdigest()
     if actual!=p['new_back_sha256']:errors.append([p['display_id'],'back_not_recovered'])
    for field,value in p['proposed_patch'].items():
     actual=identity[field]
     if (str(actual).casefold()!=str(value).casefold() if field=='player_name' else actual!=value):errors.append([p['display_id'],'identity:'+field])
    assert row['image_front_url']==p['expected_item']['image_front_url'],'frontchanged'
    for side in ['front','back']:
     old=settings.resolved_upload_dir/Path(p['expected_item']['image_'+side+'_url']).name
     assert old.exists() and hashlib.sha256(old.read_bytes()).hexdigest()==p['old_image_hashes'][side],'originalimage lost'
    checked.append(p['display_id'])
   await c.rollback()
 finally:await db.close()
 print(json.dumps({'checked':checked,'failure_count':len(errors),'errors':errors,'worker_enabled':settings.purchase_cost_worker_enabled}))
 if errors:raise SystemExit(1)
asyncio.run(main())
