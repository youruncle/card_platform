const {test,afterEach}=require('node:test');const assert=require('node:assert/strict');const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost/knowledge'});global.window=dom.window;global.document=dom.window.document;global.HTMLElement=dom.window.HTMLElement;global.FormData=dom.window.FormData;global.Event=dom.window.Event;Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});global.React=require('react');
const {render,cleanup,fireEvent,waitFor,act}=require('@testing-library/react');const {KnowledgeBrowser}=require('../components/knowledge-browser.tsx');
const summary={version:19,version_id:'release/19',discovery_complete:false,summary:{manufacturers:2,products:1,card_facts:80,images:2,paired_cards:0},manufacturers:[{name:'Topps',product_count:1,card_count:80,image_count:2},{name:'Upper Deck',product_count:0,card_count:0,image_count:0}],seasons:[2025,2024],entity_types:[{type:'parallel',count:1},{type:'manufacturer',count:2}],issues:[{source_key:'UD archive',message:'来源暂不可访问'}]};
const product={id:'product/a',name:'Chrome Basketball',manufacturer:'Topps',brand:'Chrome',season_start:2025,season_end:2026,sport:'Basketball',league:'NBA',aliases:['铬金'],description:'官方产品说明',card_count:80,image_count:2,paired_card_count:0,text_status:'partial',source_url:'https://example.com/checklist',source_authority:'official',cover_image_url:null};
const card={id:'card/a',name:'Stephen Curry #30',product_id:'product/a',product_name:'Chrome Basketball',manufacturer:'Topps',season_start:2025,season_end:2026,player:'Stephen Curry',card_number:'30',section:'Base',parallel:'Refractor',serial_total:null,is_rookie:false,is_autograph:null,is_patch:null,image_count:0,front_image_url:null,back_image_url:null,sources:[{url:'https://example.com/checklist',locator:'第 2 页',authority:'official'}]};
const media={id:'media/a',product_id:'product/a',card_id:null,image_url:'/v1/knowledge/browse/media/media%2Fa/image?version_id=release%2F19',thumbnail_url:'/v1/knowledge/browse/media/media%2Fa/image?thumbnail=true&version_id=release%2F19',side:'unknown',image_type:'design',source_url:'https://example.com/design',source_authority:'official',caption:'Chrome 设计示例',player:null,card_number:null,parallel:null,pair_id:null};
const page=items=>({items,total:items.length,offset:0,limit:24,version:19});let requests=[];
function serve(override){requests=[];global.fetch=async(input,init)=>{const url=new URL(input,'http://localhost');requests.push({url,init});const custom=override&&await override(url,init);if(custom)return custom;let body;if(url.pathname.endsWith('/summary'))body=summary;else if(url.pathname.endsWith('/products/product%2Fa'))body={product,sections:[{name:'Base',card_count:60}],parallels:[{name:'Refractor',card_count:20}],version:19};else if(url.pathname.endsWith('/cards/card%2Fa'))body={card,images:[],related_versions:[{...card,id:'card/b',parallel:'Gold',serial_total:50}],version:19};else if(url.pathname.endsWith('/products'))body=page([product]);else if(url.pathname.endsWith('/cards'))body=page([card]);else if(url.pathname.endsWith('/media'))body=page([media,{...media,id:'media/b',caption:'另一个设计示例'}]);else if(url.pathname.endsWith('/entities'))body=page([{id:'term/a',entity_type:'parallel',canonical_name:'Refractor',aliases:['折射','银折'],description:'已审核平行名称',source_url:'https://example.com/term'}]);else throw new Error('Unexpected URL '+url);return {ok:true,status:200,json:async()=>body};};}
function open(query=''){window.history.replaceState({},'',`/knowledge${query}`);serve();return render(React.createElement(KnowledgeBrowser));}afterEach(()=>{cleanup();window.history.replaceState({},'','/knowledge');});
test('overview includes zero-product manufacturers, immutable version and source gaps',async()=>{const view=open();await view.findByText('Upper Deck');assert.match(view.container.textContent,/v19/);assert.match(view.container.textContent,/全量覆盖率未知/);assert.match(view.container.textContent,/来源暂不可访问/);assert.equal(view.getByRole('link',{name:/知识管理/}).getAttribute('href'),'/knowledge/manage');fireEvent.click(view.getByRole('button',{name:/Upper Deck/}));await waitFor(()=>assert.ok(requests.some(r=>r.url.searchParams.get('manufacturer')==='Upper Deck')));assert.equal(requests.at(-1).url.searchParams.get('version_id'),'release/19');assert.equal(requests.at(-1).init.credentials,'include');});
test('product search resets pagination and preserves filters when leaving detail',async()=>{const view=open('?view=products&offset=24&manufacturer=Topps&season=2025');await view.findByRole('button',{name:/打开产品 Chrome Basketball/});fireEvent.change(view.getByLabelText('搜索产品'),{target:{value:'铬金'}});fireEvent.submit(view.getByRole('search'));await waitFor(()=>assert.equal(new URLSearchParams(window.location.search).get('q'),'铬金'));assert.equal(new URLSearchParams(window.location.search).get('offset'),null);fireEvent.click(view.getByRole('button',{name:/打开产品 Chrome Basketball/}));await view.findByText('官方产品说明');assert.match(view.container.textContent,/Refractor/);assert.match(view.container.textContent,/部分文字/);fireEvent.click(view.getByRole('button',{name:/返回产品列表/}));await view.findByRole('button',{name:/打开产品 Chrome Basketball/});assert.equal(view.getByLabelText('搜索产品').value,'铬金');assert.equal(new URLSearchParams(window.location.search).get('season'),'2025');});
test('card filters use the contract and ignore delayed previous responses',async()=>{let finishOld;const view=open('?view=cards&offset=40');serve(async url=>{if(url.pathname.endsWith('/cards')&&url.searchParams.get('q')==='old')return new Promise(resolve=>{finishOld=()=>resolve({ok:true,status:200,json:async()=>page([{...card,name:'过期结果'}])});});});await view.findByRole('button',{name:/打开卡片 Stephen Curry/});fireEvent.change(view.getByLabelText('搜索卡片'),{target:{value:'old'}});fireEvent.submit(view.getByRole('search'));await waitFor(()=>assert.equal(typeof finishOld,'function'));fireEvent.change(view.getByLabelText('搜索卡片'),{target:{value:'Curry'}});fireEvent.change(view.getByLabelText('图片状态'),{target:{value:'missing_back'}});fireEvent.submit(view.getByRole('search'));await view.findByRole('button',{name:/打开卡片 Stephen Curry/});await act(async()=>{finishOld();});assert.doesNotMatch(view.container.textContent,/过期结果/);const current=requests.filter(r=>r.url.pathname.endsWith('/cards')).at(-1).url;assert.equal(current.searchParams.get('q'),'Curry');assert.equal(current.searchParams.get('image_state'),'missing_back');assert.equal(current.searchParams.get('offset'),'0');});
test('product facets and related card versions retain catalogue return context',async()=>{const view=open('?view=products&manufacturer=Topps&product_id=product%2Fa&product_tab=cards');await view.findByRole('button',{name:/打开卡片 Stephen Curry/});fireEvent.change(view.getByLabelText('卡组 / Insert'),{target:{value:'Base'}});fireEvent.change(view.getByLabelText('平行版本'),{target:{value:'Refractor'}});fireEvent.submit(view.getByRole('search'));await waitFor(()=>assert.ok(requests.some(r=>r.url.searchParams.get('section')==='Base'&&r.url.searchParams.get('parallel')==='Refractor')));fireEvent.click(view.getByRole('button',{name:/打开卡片 Stephen Curry/}));await view.findByText('相关版本');assert.match(view.container.textContent,/缺少正面图/);assert.match(view.container.textContent,/缺少背面图/);assert.match(view.container.textContent,/Gold/);assert.match(view.container.textContent,/第 2 页/);fireEvent.click(view.getByRole('button',{name:/返回卡片列表/}));await view.findByRole('button',{name:/打开卡片 Stephen Curry/});assert.equal(view.getByLabelText('平行版本').value,'Refractor');});
test('gallery labels design images and viewer supports comparison transforms and focus return',async()=>{const view=open('?view=gallery');await view.findByRole('button',{name:'查看图片 Chrome 设计示例'});assert.match(view.container.textContent,/设计样张/);assert.match(view.container.textContent,/侧别未知/);assert.doesNotMatch(view.container.textContent,/已配对/);fireEvent.click(view.getByLabelText('对照 Chrome 设计示例'));fireEvent.click(view.getByLabelText('对照 另一个设计示例'));fireEvent.click(view.getByRole('button',{name:'对照所选图片'}));const dialog=await view.findByRole('dialog',{name:'知识图片查看器'});assert.equal(dialog.querySelectorAll('img').length,2);fireEvent.click(view.getByRole('button',{name:'放大图片'}));fireEvent.click(view.getByRole('button',{name:'向右旋转图片'}));assert.match(dialog.querySelector('img').style.transform,/rotate\(90deg\).*scale\(1.25\)/);fireEvent.keyDown(dialog,{key:'Escape'});assert.equal(view.queryByRole('dialog'),null);assert.equal(document.activeElement.textContent,'对照所选图片');fireEvent.click(view.getByRole('button',{name:'查看图片 Chrome 设计示例'}));await view.findByRole('dialog');assert.equal(view.getByRole('link',{name:'查看大图'}).getAttribute('href'),media.image_url);assert.equal(view.getByRole('dialog').querySelector('img').getAttribute('src'),media.image_url);});
test('glossary filters types and presents known aliases and sources',async()=>{const view=open('?view=glossary');await view.findByText('已审核平行名称');assert.match(view.container.textContent,/折射/);assert.match(view.container.textContent,/银折/);fireEvent.change(view.getByLabelText('知识类型'),{target:{value:'parallel'}});fireEvent.submit(view.getByRole('search'));await waitFor(()=>assert.ok(requests.some(r=>r.url.searchParams.get('entity_type')==='parallel')));assert.equal(view.getByRole('link',{name:/查看来源/}).href,'https://example.com/term');});
test('index preparing is separate from empty content and supports manual retry',async()=>{window.history.replaceState({},'','/knowledge');let fail=true;serve(url=>url.pathname.endsWith('/summary')&&fail?{ok:false,status:503,json:async()=>({detail:'知识目录索引正在准备，请稍后重试'})}:null);const view=render(React.createElement(KnowledgeBrowser));await view.findByText(/知识目录索引正在准备/);assert.doesNotMatch(view.container.textContent,/暂无已发布产品/);fail=false;fireEvent.click(view.getByRole('button',{name:'立即重试'}));await view.findByText('Upper Deck');});

test('preparing retries stop after a bounded interval and explain manual recovery',async t=>{
 const scheduled=[];const actualTimeout=global.setTimeout;
 global.setTimeout=(callback,ms,...args)=>{if(ms===3000){scheduled.push(callback);return 100000+scheduled.length;}return actualTimeout(callback,ms,...args);};
 t.after(()=>{global.setTimeout=actualTimeout;});
 window.history.replaceState({},'','/knowledge');serve(()=>({ok:false,status:503,json:async()=>({detail:'知识目录索引正在准备，请稍后重试'})}));
 const view=render(React.createElement(KnowledgeBrowser));await view.findByText(/知识目录索引正在准备/);
 for(let i=0;i<10;i++){assert.ok(scheduled.length);await act(async()=>{scheduled.shift()();});}
 assert.equal(requests.length,11);assert.equal(scheduled.length,0);assert.match(view.container.textContent,/自动重试已结束/);
});

test('a failed thumbnail keeps accessible media controls and an original-image retry',async()=>{
 const view=open('?view=gallery');const opener=await view.findByRole('button',{name:'查看图片 Chrome 设计示例'});
 fireEvent.error(opener.querySelector('img'));assert.equal(view.container.querySelectorAll('button button').length,0);
 fireEvent.click(opener);const dialog=await view.findByRole('dialog');fireEvent.error(dialog.querySelector('img'));
 fireEvent.click(view.getByRole('button',{name:'重试图片'}));assert.match(dialog.querySelector('img').getAttribute('src'),/_retry=1/);
});

test('card-bound front/back switching retains exact identity and only explicit pairs are marked',async()=>{
 window.history.replaceState({},'','/knowledge?view=cards&card_id=card%2Fa');
 const front={...media,id:'front',card_id:'card/a',caption:'Curry 正面',side:'front',image_type:'specimen',pair_id:'pair1'};
 const back={...front,id:'back',caption:'Curry 背面',side:'back',image_url:'/v1/knowledge/browse/media/back/image?version_id=release%2F19'};
 serve(url=>url.pathname.endsWith('/cards/card%2Fa')?{ok:true,status:200,json:async()=>({card:{...card,image_count:2,front_image_url:front.image_url,back_image_url:back.image_url},images:[front,back],related_versions:[],version:19})}:null);
 const view=render(React.createElement(KnowledgeBrowser));fireEvent.click(await view.findByRole('button',{name:'查看图片 Curry 正面'}));
 const dialog=await view.findByRole('dialog');fireEvent.click(view.getByRole('button',{name:'背面'}));
 assert.equal(dialog.querySelector('img').getAttribute('src'),back.image_url);
 fireEvent.keyDown(dialog,{key:'Escape'});assert.match(view.container.textContent,/已核实配对/);
});

test('an empty published list differs from an API error and pagination preserves URL filters',async()=>{
 window.history.replaceState({},'','/knowledge?view=products&manufacturer=Topps');let mode='pages';
 serve(url=>url.pathname.endsWith('/products')?{ok:mode!=='error',status:mode==='error'?500:200,json:async()=>mode==='error'?{detail:'来源读取失败'}:{...page(mode==='empty'?[]:[product]),total:mode==='empty'?0:60,offset:Number(url.searchParams.get('offset')),limit:24}}:null);
 const view=render(React.createElement(KnowledgeBrowser));await view.findByRole('button',{name:/打开产品 Chrome/});
 fireEvent.click(view.getByRole('button',{name:'下一页'}));await waitFor(()=>assert.equal(new URLSearchParams(window.location.search).get('offset'),'24'));assert.equal(new URLSearchParams(window.location.search).get('manufacturer'),'Topps');
 mode='error';fireEvent.click(view.getByRole('button',{name:'下一页'}));await view.findByRole('alert');assert.doesNotMatch(view.container.textContent,/暂无已发布产品符合/);
 mode='empty';fireEvent.click(view.getByRole('button',{name:'立即重试'}));await view.findByText('暂无已发布产品符合当前筛选');assert.equal(view.queryByRole('alert'),null);
});

test('browser popstate restores a shareable search and filter state',async()=>{
 const view=open('?view=products&q=Chrome&season=2025');await view.findByRole('button',{name:/打开产品 Chrome/});
 await act(async()=>{window.history.replaceState({},'','/knowledge?view=cards&q=Curry&image_state=missing_front');window.dispatchEvent(new Event('popstate'));});
 await view.findByRole('button',{name:/打开卡片 Stephen Curry/});assert.equal(view.getByLabelText('搜索卡片').value,'Curry');assert.equal(view.getByLabelText('图片状态').value,'missing_front');
});

test('comparison keeps two distinct images when the primary selection changes',async()=>{
 const view=open('?view=gallery');await view.findByRole('button',{name:'查看图片 Chrome 设计示例'});
 fireEvent.click(view.getByLabelText('对照 Chrome 设计示例'));fireEvent.click(view.getByLabelText('对照 另一个设计示例'));fireEvent.click(view.getByRole('button',{name:'对照所选图片'}));
 const dialog=await view.findByRole('dialog');fireEvent.change(view.getByLabelText('当前图片'),{target:{value:'1'}});
 assert.deepEqual([...dialog.querySelectorAll('img')].map(item=>item.alt).sort(),['Chrome 设计示例','另一个设计示例']);
});

test('captured sell-sheet evidence has readable titles and source labels without losing original captions',async()=>{
 window.history.replaceState({},'','/knowledge?view=gallery');
 const caption='Manufacturer sell-sheet design example; page 5, embedded image 2. Identity and variant not asserted.';
 serve(url=>url.pathname.endsWith('/media')?{ok:true,status:200,json:async()=>page([{...media,caption,source_authority:'manufacturer_document_third_party_host'}])}:null);
 const view=render(React.createElement(KnowledgeBrowser));await view.findByRole('heading',{name:'设计示例 · 第 5 页（图 3）'});
 assert.match(view.container.textContent,/经销商托管原厂资料/);assert.doesNotMatch(view.container.textContent,/manufacturer_document_third_party_host/);
 fireEvent.click(view.getByRole('button',{name:'查看图片 设计示例 · 第 5 页（图 3）'}));const dialog=await view.findByRole('dialog');assert.match(dialog.textContent,/Identity and variant not asserted/);
});

test('global media shows its series and returns to the same gallery filters after visiting it',async()=>{
 window.history.replaceState({},'','/knowledge?view=gallery&kind=design&offset=24');
 serve(url=>url.pathname.endsWith('/media')?{ok:true,status:200,json:async()=>({...page([{...media,product_name:'Chrome Basketball',manufacturer:'Topps',season_start:2025,season_end:2026}]),offset:24,total:60})}:null);
 const view=render(React.createElement(KnowledgeBrowser));await view.findByText('Chrome Basketball');
 assert.match(view.container.textContent,/Topps · 2025–26/);
 fireEvent.click(view.getByRole('button',{name:/查看所属系列/}));await view.findByText('官方产品说明');
 fireEvent.click(view.getByRole('button',{name:/返回产品列表/}));await view.findByRole('button',{name:'查看图片 Chrome 设计示例'});
 assert.equal(new URLSearchParams(window.location.search).get('kind'),'design');assert.equal(new URLSearchParams(window.location.search).get('offset'),'24');
});

test('missing optional series labels still allow navigation through a known product binding',async()=>{
 const view=open('?view=gallery');await view.findByRole('button',{name:'查看图片 Chrome 设计示例'});
 assert.match(view.container.textContent,/系列名称待补充/);
 fireEvent.click(view.getAllByRole('button',{name:/查看所属系列/})[0]);await view.findByText('官方产品说明');
});

test('media type is independent of card binding and preserves unknown or unbound specimen states',async()=>{
 window.history.replaceState({},'','/knowledge?view=gallery');
 serve(url=>url.pathname.endsWith('/media')?{ok:true,status:200,json:async()=>page([
  {...media,id:'design-product',caption:'原厂产品图',image_type:'official_product_image'},
  {...media,id:'unbound-scan',caption:'未绑定扫描图',image_type:'verified_card_scan'},
  {...media,id:'unknown-type',caption:'未知类型图片',image_type:'new_capture_type',card_id:'card/a'},
 ])}:null);
 const view=render(React.createElement(KnowledgeBrowser));await view.findByRole('button',{name:'查看图片 未绑定扫描图'});
 const design=view.getByRole('button',{name:'查看图片 原厂产品图'}).closest('article');assert.match(design.textContent,/设计样张/);
 const scan=view.getByRole('button',{name:'查看图片 未绑定扫描图'}).closest('article');assert.match(scan.textContent,/已核实实卡图/);assert.match(scan.textContent,/未绑定具体卡牌/);assert.doesNotMatch(scan.textContent,/设计样张/);
 const unknown=view.getByRole('button',{name:'查看图片 未知类型图片'}).closest('article');assert.match(unknown.textContent,/类型待核对/);assert.doesNotMatch(unknown.textContent,/设计样张/);
 fireEvent.click(view.getByRole('button',{name:'查看图片 未绑定扫描图'}));const dialog=await view.findByRole('dialog');assert.match(dialog.textContent,/已核实实卡图/);assert.match(dialog.textContent,/未绑定具体卡牌/);
});
