const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,cleanup,act}=require('@testing-library/react');
const {NewsRiskAlerts}=require('../components/news-risk-alerts.tsx');
afterEach(cleanup);
function alert(direction,expires=Date.now()+100000) {
 return {id:direction,direction,headline:'官方新闻',facts:'已确认事实',attribution:'影响需求但价格未确认',
 confidence:'medium',source_url:'https://www.nba.com/news/example',published_at:new Date().toISOString(),
 created_at:new Date().toISOString(),expires_at:new Date(expires).toISOString()};
}
test('positive and negative coexist with evidence and source links',()=>{
 const {container}=render(React.createElement(NewsRiskAlerts,{alerts:[alert('positive'),alert('negative')]}));
 assert.match(container.textContent,/利好/);assert.match(container.textContent,/利空/);
 assert.match(container.textContent,/影响需求但价格未确认/);
 assert.equal(container.querySelectorAll('a[href="https://www.nba.com/news/example"]').length,2);
});
test('expired alerts never render',()=>{
 const {container}=render(React.createElement(NewsRiskAlerts,{alerts:[alert('negative',Date.now()-1)]}));
 assert.equal(container.textContent,'—');
});
test('open page removes a marker automatically at expiry',async()=>{
 const {container}=render(React.createElement(NewsRiskAlerts,{alerts:[alert('negative',Date.now()+80)]}));
 assert.match(container.textContent,/利空/);
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,120));});
 assert.equal(container.textContent,'—');
});
