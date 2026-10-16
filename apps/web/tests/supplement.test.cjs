const {test}=require('node:test');const assert=require('node:assert/strict');
const {identitySuggestions,acceptIdentitySuggestions}=require('../../../packages/card-core/src/supplement.ts');
test('supplement selection preserves every unselected confirmed field and excludes unknown suggestions',()=>{
 const current={player_name:'Human',brand_name:'Panini',product_name:'Test',season_start:2026,season_end:2027,parallel_name:'Gold',manufacturer:'Confirmed manufacturer',is_autograph:true,autograph_type:'on_card',inscription_text:'MVP',is_memorabilia:true,memorabilia_types:['patch'],print_run:99};
 const suggestions=identitySuggestions(current,{player_name:'Suggested',brand_name:null,parallel_name:'',card_number:'10',is_autograph:null});
 assert.deepEqual(suggestions.map(s=>s.key),['player_name','card_number']);
 const merged=acceptIdentitySuggestions(current,suggestions,['card_number']);
 assert.deepEqual(merged,{...current,card_number:'10'});assert.deepEqual(acceptIdentitySuggestions(current,suggestions,[]),current);
 assert.equal(current.card_number,undefined);
});
test('only accepting incompatible season start explains dependent field correction',()=>{
 const current={season_start:2026,season_end:2027};const suggestions=identitySuggestions(current,{season_start:2025,season_end:2026});
 assert.throws(()=>acceptIdentitySuggestions(current,suggestions,['season_start']),/赛季/);
 assert.deepEqual(acceptIdentitySuggestions(current,suggestions,['season_start','season_end']),{season_start:2025,season_end:2026});
});
