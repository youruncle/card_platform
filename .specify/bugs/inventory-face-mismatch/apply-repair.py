"""Apply an explicitly reviewed, image-bound recovery manifest; default rolls back.

Arguments: manifest.json assets_dir [--apply]. Never alters transactions or old files.
"""
import asyncio,hashlib,json,sys
from pathlib import Path
from uuid import UUID,uuid4
from psycopg.types.json import Jsonb
from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.domain.identity_repair import prepare_identity_repair
from card_platform.identity_review_api import _item_identity,_identity
from card_platform.schemas import CardIdentityInput
from card_platform.market_data.models import MarketIdentity
from card_platform.market_data.repository import MarketDataRepository
from card_platform.purchase_cost_service import owner_lock

REASON='2026-10-19 user-authorized inventory-face-mismatch recovery; reviewed original scan sources; field-scoped, not whole-card identity confirmation'

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

async def definition(c,identity,before,print_run):
 from card_platform.repository import normalize_name
 p=await(await c.execute('SELECT id FROM player WHERE normalized_name=%s AND birth_date IS NULL',(normalize_name(identity['player_name']),))).fetchone()
 if not p:p=await(await c.execute('INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id',(identity['player_name'],normalize_name(identity['player_name'])))).fetchone()
 b=await(await c.execute('SELECT id FROM brand WHERE name=%s',(identity['brand_name'],))).fetchone()
 if not b:b=await(await c.execute('INSERT INTO brand(name) VALUES(%s) RETURNING id',(identity['brand_name'],))).fetchone()
 sets=await(await c.execute('SELECT id FROM card_set WHERE brand_id=%s AND season_start=%s AND season_end IS NOT DISTINCT FROM %s AND product_name=%s AND set_name IS NOT DISTINCT FROM %s',(b['id'],identity['season_start'],identity['season_end'],identity['product_name'],identity['set_name']))).fetchall()
 fields={k:identity.get(k,before.get(k)) for k in CardIdentityInput.model_fields}
 fields={k:v for k,v in fields.items() if v is not None};fields.update(print_run=print_run)
 fields['is_patch']=before.get('is_patch',False)
 card=CardIdentityInput.model_validate(fields)
 attrs={k:getattr(card,k) for k in ['is_rookie','is_autograph','autograph_type','has_inscription','inscription_text','is_memorabilia','memorabilia_types','memorabilia_usage','memorabilia_player','is_patch','is_case_hit','print_run']}
 for cs in sets:
  old=await(await c.execute('SELECT * FROM card_definition WHERE card_set_id=%s AND player_id=%s AND card_number IS NOT DISTINCT FROM %s AND insert_name IS NOT DISTINCT FROM %s AND parallel_name IS NOT DISTINCT FROM %s',(cs['id'],p['id'],identity['card_number'],identity['insert_name'],identity['parallel_name']))).fetchone()
  if old and all(old.get(k)==v for k,v in attrs.items()):return old['id']
  if not old:
   set_id=cs['id'];break
 else:
  # A new equivalent set isolates a conflicting shared definition; no shared row is edited.
  assert identity['set_name'] is None,'cannot isolate non-null catalog set without review'
  cs=await(await c.execute('INSERT INTO card_set(brand_id,season_start,season_end,product_name,set_name) VALUES(%s,%s,%s,%s,%s) RETURNING id',(b['id'],identity['season_start'],identity['season_end'],identity['product_name'],identity['set_name']))).fetchone();set_id=cs['id']
 columns=['card_set_id','player_id','card_number','insert_name','parallel_name',*attrs]
 values=[set_id,p['id'],identity['card_number'],identity['insert_name'],identity['parallel_name'],*attrs.values()]
 return(await(await c.execute('INSERT INTO card_definition('+','.join(columns)+') VALUES('+','.join(['%s']*len(columns))+') RETURNING id',values)).fetchone())['id']

async def main():
 manifest=json.loads(Path(sys.argv[1]).read_text());assets=Path(sys.argv[2]);apply='--apply' in sys.argv
 settings=get_settings();assert settings.purchase_cost_worker_enabled is True,'worker must stay enabled'
 owner=UUID(manifest['owner_id']);db=Database(settings);await db.open();output=[]
 try:
  async with db.connection() as c:
   await owner_lock(c,owner)
   ids=[UUID(r['collection_item_id']) for r in manifest['records']]
   tx_before=await(await c.execute('SELECT t.* FROM card_transaction t JOIN collection_item ci ON ci.id=t.collection_item_id WHERE ci.owner_id=%s ORDER BY t.id',(owner,))).fetchall()
   assignments_before=await(await c.execute('SELECT a.* FROM cardhobby_cost_assignment a WHERE owner_id=%s ORDER BY id',(owner,))).fetchall()
   for plan in manifest['records']:
    item_id=UUID(plan['collection_item_id'])
    # Idempotence uses recorded recovery ID, never a loose image/name match.
    existing=await(await c.execute('SELECT id FROM card_identity_change_audit WHERE collection_item_id=%s AND after_state->>\'recovery_id\'=%s',(item_id,manifest['recovery_id']))).fetchone()
    if existing:output.append({'display_id':plan['display_id'],'status':'already_applied'});continue
    ci=await(await c.execute('SELECT * FROM collection_item WHERE id=%s AND owner_id=%s AND deleted_at IS NULL FOR UPDATE',(item_id,owner))).fetchone()
    assert ci and ci['display_id']==plan['display_id'],'wrong owner/item'
    expected=plan['expected_item']
    for key in ['card_definition_id','image_front_url','image_back_url','serial_number','serial_total','notes','updated_at']:
     assert str(ci[key])==str(expected[key]),f'changed since snapshot {plan["display_id"]}:{key}'
    hashes={}
    for side in ['front','back']:
     url=ci['image_'+side+'_url'];p=settings.resolved_upload_dir/Path(url).name if url else None
     hashes[side]=digest(p) if p else None
    assert hashes==plan['old_image_hashes'],'original image changed'
    row=dict(await _item_identity(c,item_id,owner));before=_identity(row)
    for k,v in plan['before_identity'].items():assert before.get(k)==v,f'identity changed:{k}'
    confirmed=await(await c.execute("SELECT id FROM card_identity_version iv WHERE collection_item_id=%s AND review_status='confirmed' AND NOT EXISTS(SELECT 1 FROM card_identity_version rev WHERE rev.supersedes_id=iv.id AND rev.review_status='rejected') ORDER BY version DESC LIMIT 1",(item_id,))).fetchone()
    actual_confirmed=str(confirmed['id']) if confirmed else None
    assert actual_confirmed==plan['expected_confirmed_identity_version_id'],'confirmed identity changed'
    new_back_url=ci['image_back_url'] if plan.get('back_action')=='keep' else None
    new_hash=hashes['back'] if plan.get('back_action')=='keep' else None
    if plan.get('back_asset'):
     path=assets/plan['back_asset'];assert path.parent.resolve()==assets.resolve() and path.is_file()
     new_hash=digest(path);assert new_hash==plan['new_back_sha256'],'recovery image changed'
     name=f'{item_id}-back-recovered-{new_hash[:20]}.jpg';dest=settings.resolved_upload_dir/name
     new_back_url='/uploads/'+name
     if apply:
      if dest.exists():assert digest(dest)==new_hash
      else:
       with dest.open('xb') as f:f.write(path.read_bytes())
    source_hashes={'front':hashes['front']}
    if new_hash:source_hashes['back']=new_hash
    if plan['proposed_patch']:
     proposal={'collection_item_id':str(item_id),'expected_confirmed_identity_version_id':actual_confirmed,
      'proposed_patch':plan['proposed_patch'],'before':plan['before_identity'],
      'source_images':{side:{'sha256':h} for side,h in source_hashes.items()},
      'visual_observations':plan['visual_observations'],'official_sources':plan.get('official_sources',[]),
      'reference_sources':plan.get('reference_sources',[]),'remaining_uncertainty':plan['remaining_uncertainty']}
     repair=prepare_identity_repair({**before,'collection_item_id':str(item_id),'confirmed_identity_version_id':actual_confirmed},proposal,source_hashes)
     identity=repair['identity'];provenance=repair['field_provenance']
     def_id=await definition(c,identity,plan['expected_full_item'],plan.get('print_run',identity['serial_total']))
    else:
     identity=before;provenance={};def_id=ci['card_definition_id']
     if 'print_run' in plan:
      def_id=await definition(c,identity,plan['expected_full_item'],plan['print_run'])
    if 'print_run' in plan and plan['print_run']!=plan['expected_full_item'].get('print_run'):
     provenance['print_run']={'source':'original_scan_print_run_review','before':plan['expected_full_item'].get('print_run'),'value':plan['print_run'],'observation':plan['visual_observations']}
    provenance['_image_back_url']={'source':'original_flatbed_source_recovery','before':ci['image_back_url'],'value':new_back_url,'before_sha256':hashes['back'],'sha256':new_hash,'source_evidence':plan['source_evidence'],'observation':plan['visual_observations']}
    latest=await(await c.execute('SELECT id,version FROM card_identity_version WHERE collection_item_id=%s ORDER BY version DESC LIMIT 1',(item_id,))).fetchone()
    version=await(await c.execute("INSERT INTO card_identity_version(collection_item_id,version,identity,field_provenance,conflicts,review_status,source,supersedes_id) VALUES(%s,%s,%s,%s,%s,'pending','inventory_face_recovery',%s) RETURNING id",(item_id,latest['version']+1 if latest else 1,Jsonb(identity),Jsonb(provenance),Jsonb([plan['remaining_uncertainty']]),latest['id'] if latest else None))).fetchone()
    numbered=identity['serial_total'] is not None if {'serial_number','serial_total'} & set(plan['proposed_patch']) else ci['is_numbered']
    note='【2026-10-19 正反面恢复】'+('同批原图核验：保留原配图，纠正将印量误作实卡限编的字段。' if plan.get('back_action')=='keep' else '已从原始扫描证据恢复配对，身份字段按可见证据修正。' if new_back_url else '原背面照片实际仍为正面；已清除错误背面引用，等待补扫真实背面。')
    await c.execute('UPDATE collection_item SET card_definition_id=%s,image_back_url=%s,serial_number=%s,serial_total=%s,is_numbered=%s,notes=%s,updated_at=now() WHERE id=%s AND owner_id=%s',(def_id,new_back_url,identity['serial_number'],identity['serial_total'],numbered,(ci['notes'] or '')+'\n'+note,item_id,owner))
    after_state={**identity,'image_front_url':ci['image_front_url'],'image_back_url':new_back_url,'image_content_hashes':source_hashes,'recovery_id':manifest['recovery_id'],'remaining_uncertainty':plan['remaining_uncertainty']}
    await c.execute('INSERT INTO card_identity_change_audit(collection_item_id,identity_version_id,actor_id,reason,before_state,after_state) VALUES(%s,%s,%s,%s,%s,%s)',(item_id,version['id'],owner,REASON,Jsonb({**before,'image_front_url':ci['image_front_url'],'image_back_url':ci['image_back_url'],'image_content_hashes':hashes,'collection_item':json.loads(json.dumps(ci,default=str))}),Jsonb(after_state)))
    invalidated=0
    if new_back_url:
     invalidated=(await c.execute("UPDATE valuation_snapshot SET identity_compatibility='incompatible',identity_compatibility_reasons=%s WHERE collection_item_id=%s AND identity_compatibility<>'incompatible'",(Jsonb(['inventory_face_pair_recovered',f'recovery_id:{manifest["recovery_id"]}']),item_id))).rowcount
     fingerprint=await MarketDataRepository().ensure_identity(c,MarketIdentity.model_validate({**identity,'print_run':plan.get('print_run',identity['serial_total'])}),item_id)
     await c.execute('INSERT INTO market_identity_rebind_audit(collection_item_id,identity_version_id,new_fingerprint,actor_id,details) VALUES(%s,%s,%s,%s,%s)',(item_id,version['id'],fingerprint,owner,Jsonb({'recovery_id':manifest['recovery_id'],'old_evidence_not_adopted':True})))
     await c.execute("INSERT INTO valuation_job(collection_item_id,status) VALUES(%s,'queued') ON CONFLICT(collection_item_id) WHERE status IN ('queued','identifying','searching','calculating') DO NOTHING",(item_id,))
    after=dict(await _item_identity(c,item_id,owner))
    for k,v in identity.items():
     actual=_identity(after)[k]
     assert (str(actual).casefold()==str(v).casefold() if k=='player_name' else actual==v),f'identity did not bind {plan["display_id"]}:{k}'
    assert after['image_back_url']==new_back_url
    output.append({'display_id':plan['display_id'],'status':'applied' if apply else 'validated_rolled_back','image_back_url':new_back_url,'identity_changed_fields':list(plan['proposed_patch']),'invalidated_valuations':invalidated,'identity_version_id':str(version['id'])})
   tx_after=await(await c.execute('SELECT t.* FROM card_transaction t JOIN collection_item ci ON ci.id=t.collection_item_id WHERE ci.owner_id=%s ORDER BY t.id',(owner,))).fetchall()
   assignments_after=await(await c.execute('SELECT a.* FROM cardhobby_cost_assignment a WHERE owner_id=%s ORDER BY id',(owner,))).fetchall()
   assert tx_before==tx_after and assignments_before==assignments_after,'transaction/assignment mutation forbidden'
   if apply:await c.commit()
   else:await c.rollback()
  print(json.dumps({'applied':apply,'results':output,'transactions_unchanged':True,'assignments_unchanged':True,'worker_enabled':settings.purchase_cost_worker_enabled},ensure_ascii=False))
 finally:await db.close()
asyncio.run(main())
