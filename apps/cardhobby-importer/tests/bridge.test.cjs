const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const source=readFileSync(path.join(__dirname,'../background.js'),'utf8');
function worker() {
 let listener;const calls=[];
 vm.runInNewContext(source,{URL,Set,Map,chrome:{runtime:{onMessage:{addListener(fn){listener=fn;}}},
 tabs:{query:async q=>{calls.push(['query',q]);return [{id:17,url:'https://cardhobby.com.cn/Order/MyOrders'}];},sendMessage:async(id,message)=>{calls.push(['send',id,message]);return {result:{orders:[],complete:true}};}},
 scripting:{executeScript:async payload=>calls.push(['inject',payload])}}});
 return {listener,calls};
}
const request={type:'CARD_PLATFORM_COST_SEARCH',requestId:'00000000-0000-4000-8000-000000000001',keywords:['Test Player']};
test('background rejects messages from remote sites and different local ports',()=>{
 const {listener,calls}=worker();
 for(const url of ['https://evil.example/','https://cardhobby.com.cn/Order/MyOrders','http://localhost:8080/']) {
  assert.equal(listener(request,{url,tab:{id:1}},()=>assert.fail('untrusted response')),undefined);
 }
 assert.equal(calls.length,0);
});
test('local UI may request only fixed order-search operation in a CardHobby tab',async()=>{
 const {listener,calls}=worker();
 const result=await new Promise(resolve=>listener(request,{url:'http://localhost:3000/',tab:{id:1}},resolve));
 assert.equal(result.result.complete,true);
 assert.equal(calls[1][1].files.join(','),'cost-capture.js,cost-search.js');
 assert.equal(calls[2][2].type,'CARDHOBBY_SEARCH_ORDERS');
 assert.equal(calls[2][2].keywords[0],'Test Player');
 assert.equal(listener({...request,type:'ARBITRARY_FETCH',url:'https://evil.example/'},{url:'http://localhost:3000/',tab:{id:1}},()=>assert.fail()),undefined);
});
