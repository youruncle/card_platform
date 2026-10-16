const {test,afterEach}=require('node:test');const assert=require('node:assert/strict');const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');global.window=dom.window;global.document=dom.window.document;Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});global.React=require('react');global.HTMLElement=dom.window.HTMLElement;
const {render,screen,cleanup,waitFor}=require('@testing-library/react');const {PortfolioImage}=require('../components/portfolio-image.tsx');afterEach(cleanup);
test('waits for server capability then uses shared thumbnail URL for back-only cards',async()=>{
 let resolve,count=0;global.fetch=()=>{count++;return new Promise(done=>resolve=()=>done({ok:true,json:async()=>({image_variants:true})}));};
 render(React.createElement('div',{},React.createElement(PortfolioImage,{itemId:'one',side:'back',url:'/uploads/one-back-r.png',variant:'thumb',alt:'背面'}),React.createElement(PortfolioImage,{itemId:'two',side:'front',url:'/uploads/two-front-r.jpg',variant:'preview',alt:'正面'})));
 assert.equal(document.querySelectorAll('img').length,0);assert.equal(count,1);resolve();
 await waitFor(()=>assert.equal(document.querySelectorAll('img').length,2));
 assert.equal(screen.getByAltText('背面').getAttribute('src'),'/v1/portfolio/items/one/images/back?variant=thumb&revision=one-back-r.png');
 assert.equal(screen.getByAltText('正面').getAttribute('src'),'/v1/portfolio/items/two/images/front?variant=preview&revision=two-front-r.jpg');
});
