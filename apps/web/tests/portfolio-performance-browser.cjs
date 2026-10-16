// Build an isolated web copy first. Run with PERFORMANCE_WEB_DIR and NODE_PATH
// pointing to a Next build and installed Playwright package respectively.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const {test}=require('node:test');
const assert=require('node:assert/strict');

test('built browser renders core views independently of auxiliary requests',async()=>{
 const cwd=process.env.PERFORMANCE_WEB_DIR;
 assert.ok(cwd,'PERFORMANCE_WEB_DIR must reference an isolated build');
 const server=spawn(process.execPath,['node_modules/next/dist/bin/next','start','-H','127.0.0.1','-p','13001'],{cwd,stdio:'ignore'});
 let browser;
 const held=[];
 try {
  let ready=false;
  for(let attempt=0;attempt<100;attempt++){
   try{ready=(await fetch('http://127.0.0.1:13001')).ok;}catch{}
   if(ready)break;
   await new Promise(resolve=>setTimeout(resolve,100));
  }
  assert.ok(ready,'isolated web server started');
  browser=await chromium.launch({headless:true, ...(process.env.PERFORMANCE_BROWSER_CHANNEL ? {channel:process.env.PERFORMANCE_BROWSER_CHANNEL} : {})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const summary={owner_id:'browser-owner',base_currency:'CNY',item_count:1,valued_item_count:1,unvalued_item_count:0,total_cost:'100',total_market_value:'200',unrealized_profit:'100',realized_profit:'0'};
  const card={id:'browser-card',player_name:'Browser Card',player_id:'browser-player',season_start:2024,product_name:'Browser Product',brand_name:'Test',strategy_tags:[],recognition_quality_issues:[],valuation_status:'succeeded',quantity_held:'1',remaining_cost:'100',fair_value:'200',valuation_currency:'CNY',unrealized_profit:'100'};
  const pages=[];let summaryCalls=0;
  await page.route('**/v1/**',async route=>{
   const url=new URL(route.request().url()),path=url.pathname;
   const respond=data=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
   if(path==='/v1/auth/me')return respond({id:'browser-owner',display_name:'Browser Fixture'});
   if(path==='/v1/scanner/jobs')return respond([]);
   if(path==='/v1/portfolio/identity-corrections'){held.push(route);return;}
   if(path==='/v1/portfolio/summary'){summaryCalls++;return respond(summary);}
   if(path==='/v1/portfolio/items/page'){
    pages.push(url.searchParams);
    return respond({items:[card],page:1,total:1,total_pages:1,page_size:30,snapshot_token:'browser-snapshot',...(url.searchParams.get('include_summary')==='true'?{summary}:{})});
   }
   if(path.endsWith('/browser-card/detail'))return respond({item:card,transactions:[],valuations:[]});
   if(path.endsWith('/browser-card/identity-corrections')||path.endsWith('/identity-review/items/browser-card')){held.push(route);return;}
   if(path.endsWith('/browser-card/catalog-enrichment'))return respond({confidence:0.9,player_name:'Browser Card'});
   if(path.includes('/purchase-cost/'))return respond(null);
   return respond({});
  });
  await page.goto('http://127.0.0.1:13001');
  await page.getByRole('button',{name:'Browser Card',exact:true}).waitFor();
  assert.equal(summaryCalls,0,'initial combined response computes summary once');
  await page.getByLabel('搜索全部卡片').fill('Browser');
  await page.getByRole('button',{name:'搜索',exact:true}).click();
  await page.getByRole('button',{name:'Browser Card',exact:true}).waitFor();
  assert.equal(pages.at(-1).has('include_summary'),false);
  assert.equal(summaryCalls,0,'search reuses metrics');
  await page.getByRole('button',{name:'Browser Card',exact:true}).click();
  await page.getByText('CARD DOSSIER / 卡片档案',{exact:true}).waitFor();
  await page.getByText('正在读取纠错与身份复核…',{exact:true}).waitFor();
  const review=held.find(route=>route.request().url().includes('/identity-review/items/'));
  assert.ok(review);
  await review.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Browser review temporarily unavailable'})});
  await page.getByRole('button',{name:'重试详情辅助信息',exact:true}).waitFor();
  await page.screenshot({path:process.env.PERFORMANCE_SCREENSHOT||'/tmp/card-performance-browser.png',fullPage:true});
  await page.getByRole('button',{name:'关闭卡片详情',exact:true}).click();
  assert.equal(await page.getByText('CARD DOSSIER / 卡片档案',{exact:true}).count(),0);
  assert.deepEqual(errors,[]);
 } finally {
  if(browser)await browser.close();
  server.kill('SIGTERM');
 }
});
