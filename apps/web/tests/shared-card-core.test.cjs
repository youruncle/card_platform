const {test} = require('node:test');
const assert = require('node:assert/strict');
const {confirmationPayload} = require('../../../packages/card-core/src/confirmation.ts');
const fields = {player_name:'Player',brand_name:'Brand',product_name:'Product',season_start:'2025'};
test('both clients share recognition corrections without retaining removed autograph evidence',()=>{
 const payload=confirmationPayload('owner',{confidence:'.8',is_autograph:true,autograph_type:'on_card',has_inscription:true,inscription_text:'test'},{...fields,is_autograph:'false'});
 assert.equal(payload.card.autograph_type,'none');
 assert.equal(payload.card.has_inscription,false);
 assert.equal(payload.card.inscription_text,null);
});
test('explicit season correction is shared and invalid season ranges are rejected',()=>{
 const result={confidence:'.8',season_end:2026};
 assert.equal(confirmationPayload('owner',result,{...fields,season_end:'2025'}).card.season_end,2025);
 assert.throws(()=>confirmationPayload('owner',result,{...fields,season_end:'2029'}));
});
