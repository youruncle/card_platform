"""Read-only target evidence snapshot; writes JSON to stdout, no credentials."""
import asyncio,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.identity_review_api import _identity
TARGETS=[306,308,311,312,*range(657,665),806,809,*range(846,854)]
async def main():
 s=get_settings();db=Database(s);await db.open()
 try:
  async with db.connection() as c:
   await c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
   rows=await(await c.execute('''SELECT ci.*,p.full_name player_name,b.name brand_name,
    cs.season_start,cs.season_end,cs.product_name,cs.set_name,
    cd.card_number,cd.insert_name,cd.parallel_name,cd.is_rookie,cd.is_autograph,cd.is_memorabilia,
    cd.is_patch,cd.print_run,cd.autograph_type,cd.has_inscription,cd.inscription_text,
    cd.memorabilia_types,cd.memorabilia_usage,cd.memorabilia_player
    FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id
    JOIN card_set cs ON cs.id=cd.card_set_id JOIN player p ON p.id=cd.player_id
    JOIN brand b ON b.id=cs.brand_id WHERE ci.display_id=ANY(%s) AND ci.deleted_at IS NULL
    ORDER BY ci.display_id''',(TARGETS,))).fetchall()
   assert len(rows)==len(TARGETS) and len({r['owner_id'] for r in rows})==1
   for r in rows:
    r['image_content_hashes']={side:hashlib.sha256((s.resolved_upload_dir/Path(r['image_'+side+'_url']).name).read_bytes()).hexdigest() for side in ['front','back'] if r['image_'+side+'_url']}
   ids=[r['id'] for r in rows];owner=rows[0]['owner_id']
   out={'captured_at':datetime.now(timezone.utc).isoformat(),'worker_enabled':s.purchase_cost_worker_enabled,'items':rows,'tables':{}}
   for table,col in [('collection_item','id'),('card_transaction','collection_item_id'),('cardhobby_cost_assignment','collection_item_id'),('cardhobby_cost_visual_inference','collection_item_id'),('cardhobby_cost_refresh_history','collection_item_id'),('automatic_purchase_cost','item_id'),('card_identity_version','collection_item_id'),('card_identity_change_audit','collection_item_id'),('valuation_job','collection_item_id'),('valuation_snapshot','collection_item_id'),('valuation_shadow_result','collection_item_id'),('card_analysis_job','collection_item_id'),('card_analysis_snapshot','collection_item_id'),('market_identity_binding','collection_item_id'),('market_identity_rebind_audit','collection_item_id')]:
    exists=await(await c.execute('SELECT to_regclass(%s) AS name',(table,))).fetchone()
    if not exists['name']:
     out['tables'][table]=[];continue
    out['tables'][table]=await(await c.execute(f'SELECT * FROM {table} WHERE {col}=ANY(%s)',(ids,))).fetchall()
   out['all_item_state']=await(await c.execute('SELECT id,display_id,card_definition_id,serial_number,serial_total,image_front_url,image_back_url,notes FROM collection_item WHERE owner_id=%s ORDER BY display_id',(owner,))).fetchall()
   out['all_transactions']=await(await c.execute('SELECT t.* FROM card_transaction t JOIN collection_item ci ON ci.id=t.collection_item_id WHERE ci.owner_id=%s ORDER BY t.id',(owner,))).fetchall()
   print(json.dumps(out,default=str,ensure_ascii=False))
   await c.rollback()
 finally:await db.close()
asyncio.run(main())
