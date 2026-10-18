const {test} = require('node:test');
const assert = require('node:assert/strict');
test('shared sorting parameters start a snapshot and retain opaque continuation tokens',()=>{
  const {portfolioSortQuery,DEFAULT_PORTFOLIO_SORT,PORTFOLIO_SORT_OPTIONS}=require('../../../packages/card-core/src/sorting.ts');
  assert.deepEqual(PORTFOLIO_SORT_OPTIONS.map(x=>x.value),['created_at','player_name','season_start','remaining_cost','fair_value','unrealized_profit','relevance']);
  const initial=new URLSearchParams(portfolioSortQuery(DEFAULT_PORTFOLIO_SORT).slice(1));
  assert.equal(initial.get('sort_by'),'created_at');
  assert.equal(initial.get('snapshot'),'true');
  const next=new URLSearchParams(portfolioSortQuery({sort_by:'fair_value',order:'asc'},'opaque+/=').slice(1));
  assert.equal(next.get('snapshot_token'),'opaque+/=');
  assert.equal(next.has('snapshot'),false);
});

const {JSDOM} = require('jsdom');
const dom = new JSDOM('<html><body></body></html>', {url:'http://localhost:3000'});
global.window=dom.window; global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;global.React=require('react');global.IS_REACT_ACT_ENVIRONMENT=true;
const {render,screen,fireEvent,cleanup,act}=require('@testing-library/react');
const Dashboard=require('../app/page.tsx').default;
require('./cost-legacy-fixture.cjs');
const response=data=>({ok:true,status:200,json:async()=>data});
test('web pagination keeps snapshot, sort resets page, and expiry retries the requested page',async()=>{
  const requests=[];
  let expired=false;
  let summaryCalls=0;
  global.fetch=async url=>{
    const path=new URL(url,'http://localhost');
    if(path.pathname==='/v1/auth/me')return response({id:'sort-owner',display_name:'Sort'});
    if(path.pathname==='/v1/scanner/jobs')return response([]);
    if(path.pathname==='/v1/portfolio/identity-corrections')return response({});
    if(path.pathname==='/v1/portfolio/summary'){summaryCalls++;return response({owner_id:'sort-owner',base_currency:'CNY',item_count:70,valued_item_count:0,unvalued_item_count:70,total_cost:'0',total_market_value:'0',unrealized_profit:'0',realized_profit:'0'});}
    if(path.pathname==='/v1/portfolio/items/page'){
      requests.push(path.searchParams);
      if(expired){expired=false;return {ok:false,status:409,json:async()=>({detail:{code:'snapshot_expired',message:'expired'}})};}
      const page=Number(path.searchParams.get('page'));
      return response({items:[],page,total:70,total_pages:3,page_size:30,snapshot_token:`snapshot-${path.searchParams.get('sort_by')}`,snapshot_at:'2026-10-07T00:00:00Z'});
    }
    throw new Error(path.pathname);
  };
  try {
    await act(async()=>render(React.createElement(Dashboard)));
    assert.equal(requests.at(-1).get('snapshot'),'true');
    const sortControl = screen.getByLabelText('卡库排序');
    const table = document.querySelector('#collection table');
    assert.ok(sortControl.compareDocumentPosition(table) & window.Node.DOCUMENT_POSITION_FOLLOWING, 'sorting is above the cards');
    assert.ok(screen.getByLabelText('每页显示数量').compareDocumentPosition(table) & window.Node.DOCUMENT_POSITION_FOLLOWING, 'page size is available before the cards');
    assert.equal(document.querySelector('.portfolio-pagination select'), null, 'footer is reserved for pagination');

    await act(async()=>fireEvent.click(screen.getByRole('button',{name:'下一页'})));
    assert.equal(requests.at(-1).get('page'),'2');
    assert.equal(requests.at(-1).get('snapshot_token'),'snapshot-created_at');
    assert.equal(summaryCalls,1,'continuations reuse summary instead of recomputing all valuations');
    await act(async()=>fireEvent.change(screen.getByLabelText('卡库排序'),{target:{value:'fair_value'}}));
    assert.equal(requests.at(-1).get('page'),'1');
    assert.equal(requests.at(-1).get('sort_by'),'fair_value');
    assert.equal(requests.at(-1).get('snapshot'),'true');
    expired=true;
    await act(async()=>fireEvent.click(screen.getByRole('button',{name:'下一页'})));
    assert.equal(requests.at(-2).get('snapshot_token'),'snapshot-fair_value');
    assert.equal(requests.at(-1).get('page'),'2');
    assert.equal(requests.at(-1).get('snapshot'),'true');
    assert.equal(requests.at(-1).get('snapshot_token'),null);
    assert.ok(screen.getByText('第 2 / 3 页'));
    assert.ok(screen.getByText(/结果更新于/));
    await act(async()=>fireEvent.change(screen.getByLabelText('搜索全部卡片'),{target:{value:'杜兰特 黑白'}}));
    await act(async()=>fireEvent.click(screen.getByRole('button',{name:'搜索',exact:true})));
    assert.equal(requests.at(-1).get('sort_by'),'relevance');
    assert.equal(requests.at(-1).get('order'),'desc');
    await act(async()=>fireEvent.change(screen.getByLabelText('卡库排序'),{target:{value:'unrealized_profit'}}));
    assert.equal(requests.at(-1).get('sort_by'),'unrealized_profit');
    assert.equal(JSON.parse(requests.at(-1).get('search')).q,'杜兰特 黑白');
    await act(async()=>fireEvent.click(screen.getByRole('button',{name:'清空条件'})));
    assert.equal(requests.at(-1).get('sort_by'),'created_at');
  } finally {cleanup();}
});


test('combined portfolio response avoids a duplicate summary computation', async()=>{
  const {getPortfolio}=require('../lib/api.ts');
  const summary={owner_id:'one',item_count:618};
  const requests=[];
  global.fetch=async url=>{
    requests.push(new URL(url,'http://localhost'));
    return response({items:[],page:1,total:618,summary,snapshot_token:'combined'});
  };
  const [stats,page]=await getPortfolio('one',1,{},30,{sort_by:'unrealized_profit',order:'desc'});
  assert.equal(requests.length,1);
  assert.equal(requests[0].searchParams.get('include_summary'),'true');
  assert.deepEqual(stats,summary);
  assert.equal(page.snapshot_token,'combined');
});

test('legacy combined-summary calls retain full snapshot mode across pages',async()=>{
 const {getPortfolio}=require('../lib/api.ts');
 const summary={owner_id:'legacy',item_count:50};const queries=[];
 global.fetch=async url=>{const query=new URL(url,'http://localhost').searchParams;queries.push(query);return response({items:[],page:1,total:50,summary,snapshot_token:'legacy-token'});};
 await getPortfolio('legacy',1,{},30,{sort_by:'created_at',order:'desc'});
 await getPortfolio('legacy',2,{},30,{sort_by:'created_at',order:'desc'},'legacy-token',summary);
 assert.equal(queries[0].get('include_summary'),'true');
 assert.equal(queries[0].get('snapshot_mode'),null);
 assert.equal(queries[1].get('snapshot_mode'),null);
 assert.equal(queries[1].get('snapshot_token'),'legacy-token');
 assert.equal(queries[1].has('include_summary'),false);
});
