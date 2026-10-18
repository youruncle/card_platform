"""Assemble the reviewed recovery manifest from frozen evidence (no DB writes)."""
import hashlib,json,shutil
from pathlib import Path
from card_platform.identity_review_api import _identity
from card_platform.domain.identity_repair import prepare_identity_repair
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'.runtime/inventory-face-mismatch'
ASSETS=P/'repair-stage/assets';ASSETS.mkdir(parents=True,exist_ok=True)
mapping=json.loads((P/'recovery-mapping-review.json').read_text())
mid={r['display_id']:r for r in json.loads((P/'recovery-identity-middle.json').read_text())['records']}
repairs={r['display_id']:r for r in mapping['recommended_repairs']}
missing={r['display_id']:r for r in mapping['unrecoverable_missing_backs']}
PATCHES={
846:dict(brand_name='Topps',season_start=2025,season_end=2026,product_name='Bowman Chrome',card_number='HS-13',insert_name='Hobby Stars',parallel_name=None),
847:dict(brand_name='Topps',season_start=2025,season_end=2026,product_name='Topps Chrome Update',card_number='142',insert_name=None,parallel_name=None),
848:dict(player_name='Kon Knueppel',brand_name='Topps',season_start=2025,season_end=2026,product_name='Topps Basketball',card_number='GN-4',insert_name='Generation Now',parallel_name=None),
849:dict(player_name='Kobe Bryant / Corey Benjamin',brand_name="Collector's Edge",season_start=1998,season_end=1999,product_name='Impulse',card_number='61',insert_name=None,parallel_name=None),
850:dict(brand_name='Topps',season_start=2025,season_end=2026,product_name='Topps Basketball',card_number='GN-16',insert_name='Generation Now',parallel_name=None),
851:dict(brand_name='Panini',season_start=2025,season_end=2026,product_name='Origins',card_number='16',insert_name=None,parallel_name=None,serial_number=None,serial_total=None),
852:dict(brand_name='Panini',product_name='Flux',card_number='208',insert_name=None,parallel_name=None,serial_number=None,serial_total=None),
853:dict(brand_name='Upper Deck',card_number='60',insert_name=None,parallel_name=None),
}
OBS={
846:'Reviewed original matching back shows HOBBY STARS, HS-13, LeBron James, Topps and Bowman Chrome trademark; front is KING JAMES Hobby shop design. Official Bowman Basketball product page establishes2025-26 release; catalogue#HS-13 agrees. No Trophy Club or Panini Status on either correct side.',
847:'Reviewed front and correct back both Shaquille O\'Neal/Lakers with Chrome marks; back explicitly#142 and Topps. Official2025-26 Topps Chrome Updates checklist lists142 Shaquille O\'Neal Los Angeles Lakers. Removed Kyrie Donruss fields inherited from unrelated back.',
848:'Correct original back explicitly KON KNUEPPEL, Charlotte Hornets and GN-4 with Topps mark; front GENERATION NOW and rookie logo. Official2025-26 Topps Basketball checklist listsGN-4 Kon Knueppel. No Hugo Gonzalez or Block by Block on corrected pair.',
849:'Original opposite faces show Kobe Bryant and Corey Benjamin, matching Collector\'s Edge Impulse98 designs. Front clearly61, opposite saysPickNo28 Chicago (draft pick, notcatalogcardnumber). Catalogue1998-99 Collector\'s Edge Impulse#61 identifiesCoreyBenjamin/KobeBryant. This is legitimate dual-subject; noJoshHart/Mosaic fields belong.',
850:'Correct original back explicitly YANG HANSEN, PortlandTrailBlazers, GN-16 and Topps mark; front GENERATION NOW and rookie logo. Official2025-26 ToppsBasketball checklist listsGN-16 YangHansen. Removed SteveNash#41 and2010 season inherited from wrongback.',
851:'Correct original back explicitly ORIGINS, WALTER CLAYTON JR.,MEMPHIS,NO.16 and2025-26 PANINI-ORIGINS BASKETBALL. Front matchesWalterClaytonJr and Origins withRC. Neither reviewed side supports4/21 or21print run; these were inherited fromJoshHartMosaic wrongback. No namedparallel verified.',
852:'Correct back recropped fromfulloriginal showsTYRESE MAXEY,NO.208,PANINI,2020-21 PANINI-FLUX BASKETBALL. Front isMaxey76ers/FLUX/RC. No49/99 stamp oncorrectedfront/back; old49/99 camefromCurryCourtKings wrongback. GenericPRIZM isnotnamedparallel proof.',
853:'Correct fulloriginalback clearly AllenIverson,UpperDecklogo,card60 and careerstatsmatchingfront1999-2000 SPAUTHENTIC. FrontandbackareIverson76ers; noToppsNow/Giannis/PPS-4 information belongs. Onlydirectlyvisiblebrandandcatalogcardnumber changed; existing1999-2000 retained.',
}
OFFICIAL={846:['https://www.topps.com/pages/bowman-basketball'],847:['https://www.topps.com/pages/topps-chrome-updates-basketball','https://cdn.shopify.com/s/files/1/0662/9749/5709/files/Final_CheckList_26CUBK.pdf?v=1783360479'],848:['https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Topps_Basketball_Checklist.pdf?v=1759329649'],850:['https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Topps_Basketball_Checklist.pdf?v=1759329649']}
REFERENCES={846:['https://www.beckett.com/news/2025-26-bowman-basketball-cards/'],849:['https://www.tcdb.com/Checklist.cfm/sid/9257/1998-Collector%27s-Edge-Impulse','https://www.ebay.com/p/28056522788']}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for env in ['local','cloud']:
 snap=json.loads((P/f'repair-before-{env}.json').read_text());items={r['display_id']:r for r in snap['items']};records=[]
 for num in sorted(set(repairs)|set(missing)|{658}):
  item=items[num];before=_identity(item);back_action='replace';asset=None;newhash=None
  patch=dict(mid[num]['identity_patch']) if num in mid else dict(PATCHES.get(num,{}))
  if num in missing:
   patch={};back_action='clear';obs='Original full front and back scan photographs show this physical card still faceup in the left column. Both inventory slots are fronts, not a truefront/backpair. No verified trueback exists among reviewed sources. Clearing onlybackURL; originalphotosandidentityretained.';evidence=missing[num]
  elif num==658:
   back_action='keep';obs=mid[num]['proposal']['visual_observations'];evidence=mid[num]['proposal'].get('full_scan_sources',[]);newhash=item['image_content_hashes']['back']
  else:
   replacement=repairs[num]['replacement'];source=Path(replacement['asset_path']);assert sha(source)==replacement['asset_sha256']
   asset=f'{num}-back.jpg';dest=ASSETS/asset
   if not dest.exists():shutil.copyfile(source,dest)
   assert sha(dest)==replacement['asset_sha256'];newhash=sha(dest)
   evidence=repairs[num];obs=mid[num]['proposal']['visual_observations'] if num in mid else OBS[num]
  # Only changed, explicitly reviewed identityfields; keepunknown flags untouched.
  patch={k:v for k,v in patch.items() if before.get(k)!=v}
  versions=[r for r in snap['tables']['card_identity_version'] if r['collection_item_id']==item['id'] and r['review_status']=='confirmed'];confirmed=max(versions,key=lambda r:r['version'])['id'] if versions else None
  rec={'display_id':num,'collection_item_id':item['id'],'expected_item':{k:item[k] for k in ['card_definition_id','image_front_url','image_back_url','serial_number','serial_total','notes','updated_at']},'expected_full_item':item,'before_identity':before,'old_image_hashes':item['image_content_hashes'],'expected_confirmed_identity_version_id':confirmed,'back_action':back_action,'back_asset':asset,'new_back_sha256':newhash,'proposed_patch':patch,'visual_observations':obs,'source_evidence':evidence,'official_sources':[{'url':url}for url in OFFICIAL.get(num,[])],'reference_sources':[{'url':url}for url in REFERENCES.get(num,[])],'remaining_uncertainty':'Only reviewed pairing and changedidentityfields corrected; namedparallel/condition notnewlyconfirmed. Wholeidentityremainspending. FiveidenticalInstantcopies lackuniquephysicalserial; noattempt toinfercopyidentity.' if num not in missing else 'Truebackmissing; requiresactualbackscan. Existingowner-confirmedidentityretained; nofabricatedreplacement.'}
  if num in mid:
   metadata=mid[num]['item_metadata_patch']
   if 'print_run' in metadata:rec['print_run']=metadata['print_run']
   elif num==658:rec['print_run']=11941
  if num in [851,852]:rec['print_run']=None
  if patch:
   hashes={'front':item['image_content_hashes']['front'],'back':newhash}
   result=prepare_identity_repair({**before,'collection_item_id':item['id'],'confirmed_identity_version_id':confirmed},{'collection_item_id':item['id'],'expected_confirmed_identity_version_id':confirmed,'proposed_patch':patch,'before':before,'source_images':{s:{'sha256':h}for s,h in hashes.items()},'visual_observations':obs},hashes)
   assert result['changed_fields']
  records.append(rec)
 manifest={'recovery_id':'inventory-face-mismatch-20261019-v1','owner_id':items[846]['owner_id'],'environment':env,'records':records,'leave_unchanged_ids':[659],'source_snapshot':str(P/f'repair-before-{env}.json')}
 path=P/'repair-stage'/f'manifest-{env}.json';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2));path.chmod(0o600)
 print(env,'records',len(records),'replace',sum(r['back_action']=='replace'for r in records),'clear',sum(r['back_action']=='clear'for r in records),'metadata_only',sum(r['back_action']=='keep'for r in records))
