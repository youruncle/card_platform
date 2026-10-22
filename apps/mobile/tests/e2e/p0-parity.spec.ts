import {test,expect} from '@playwright/test';

test('phone P0: cost, valuation, identity, transaction, trash restore and permanent delete',async({page})=>{
 const user={id:'fixture-owner',email:'fixture@example.com',display_name:'Fixture',base_currency:'CNY'};
 const identity={player_name:'Fixture Player',season_start:2024,season_end:null,brand_name:'Panini',product_name:'Prizm',set_name:null,card_number:'1',insert_name:null,parallel_name:null,serial_number:null,serial_total:null,is_rookie:false,is_autograph:false,autograph_type:'none',is_memorabilia:false,memorabilia_types:[],grade_company:null,grade_value:null};
 let card={...identity,id:'fixture-card',remaining_cost:'0',holding_status:'held',purchase_price:null as string|null,purchase_currency:'CNY',fair_value:null as string|null,valuation_currency:'CNY',valuation_status:null as string|null};
 let trashed=false,deleted=false,transaction:any=null,valuationReads=0;
 const mutations:{path:string;body:any}[]=[];
 await page.route('https://cardsp.cn/**',async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
  const headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'content-type,authorization','Access-Control-Allow-Methods':'GET,POST,PATCH,DELETE,OPTIONS'};
  if(method==='OPTIONS'){await route.fulfill({status:204,headers});return;}
  if(!path.endsWith('/auth/login'))expect(req.headers()['authorization']).toBe('Bearer fixture-token');
  const body=req.postData()?req.postDataJSON():undefined;
  if(['POST','PATCH','DELETE'].includes(method))mutations.push({path,body});
  let result:any={};let status=200;
  if(path.endsWith('/auth/login'))result={user,token:'fixture-token',expires_at:'2099-01-01T00:00:00Z'};
  else if(path.endsWith('client-capabilities'))result={portfolio_sort:true};
  else if(path.endsWith('/items/page'))result={items:trashed||deleted?[]:[card],total:trashed||deleted?0:1,page:1,total_pages:1};
  else if(path.endsWith('/detail'))result={item:card,transactions:transaction?[transaction]:[],valuations:[]};
  else if(path.endsWith('/cardhobby/status'))result={saved:true,state:'connected'};
  else if(path.endsWith('/search-plan'))result={item_id:card.id,label:'Fixture Player',status:'ready',keywords:['Fixture'],fallback_keywords:[],reason:''};
  else if(path.endsWith('/cardhobby/search'))result={orders:[],complete:true};
  else if(path.endsWith('/search-result')){card={...card,purchase_price:'100',remaining_cost:'104'};result={status:'recorded',reason:'已录入成本',candidates:[],rejected:[]};}
  else if(path.endsWith('/audit'))result=null;
  else if(path.endsWith('/automated-valuation/retry')){card.valuation_status='queued';result={id:'valuation-job'};}
  else if(path.endsWith('/automated-valuation')){
    if(card.valuation_status==='queued' && ++valuationReads>=2){card.valuation_status='succeeded';card.fair_value='150';}
    result={status:card.valuation_status,error_message:null,comparables:[],valuation:card.fair_value?{fair_value:'150',currency:'CNY',low_value:'130',high_value:'170'}:null};
  }
  else if(path==='/v1/portfolio/identity-review/items/fixture-card')result={item_id:card.id,current_identity:identity,suggested_identity:identity,latest_version:null,field_provenance:{card_number:['fixture image']},conflicts:[],review_reasons:[],market_evidence:[],research:null,candidates:[],latest_shadow:null};
  else if(path.endsWith('/identity-review/items/fixture-card/confirm')){expect(body.identity.card_number).toBe('2');card={...card,...body.identity};result={identity_version_id:'v2',accepted_count:0,rejected_count:0,needs_review_count:0};}
  else if(path.endsWith('/transactions')){expect(body.unit_price).toBeTruthy();transaction={...body,id:'transaction'};result={id:'transaction'};}
  else if(path.endsWith('/trash'))result=trashed?[card]:[];
  else if(path.endsWith('/restore')){trashed=false;status=204;}
  else if(path.endsWith('/permanent')){expect(trashed).toBe(true);deleted=true;trashed=false;status=204;}
  else if(path==='/v1/portfolio/items/fixture-card' && method==='DELETE'){expect(body.owner_id).toBe(user.id);trashed=true;status=204;}
  else if(path==='/v1/portfolio/items/fixture-card' && method==='PATCH'){card={...card,...body};status=204;}
  else if(path.endsWith('/candidates'))result={candidates:[],rejected:[],reason:'暂无候选'};
  else {status=404;result={detail:'fixture endpoint absent'};}
  if(status===204)await route.fulfill({status,headers});else await route.fulfill({status,headers,json:result});
 });
 const noOverflow=async()=>expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.goto('/');
 await page.getByLabel('用户名（邮箱）').fill(user.email);
 await page.getByLabel('密码',{exact:true}).fill('fixture-password');
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByText('Fixture Player',{exact:true})).toBeVisible();
 await noOverflow();
 await page.getByLabel('选择 Fixture Player').check();
 await page.getByRole('button',{name:'获取选中成本 (1)',exact:true}).click();
 await expect(page.getByText(/批量处理完成/)).toBeVisible();
 await expect(page.getByText('CNY 100',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'估值与管理',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Fixture Player',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'发起/重试估值',exact:true}).click();
 await expect.poll(()=>mutations.filter(m=>m.path.endsWith('/automated-valuation/retry')).length).toBe(1);
 await page.getByRole('button',{name:'刷新估值状态',exact:true}).click();
 // Management sections are disclosed on demand on narrow screens.
 const openSection=async(text:string)=>{const summary=page.locator('summary').filter({hasText:text}).first();if(await summary.count() && !(await summary.evaluate(el=>el.parentElement?.hasAttribute('open'))))await summary.click();};
 await openSection('身份审核');
 await page.getByLabel('卡号',{exact:true}).fill('2');
 await page.getByLabel('确认理由',{exact:true}).fill('核对卡面确认编号');
 await page.getByRole('button',{name:'确认身份',exact:true}).click();
 await expect.poll(()=>mutations.some(m=>m.path.endsWith('/identity-review/items/fixture-card/confirm'))).toBe(true);
 await openSection('交易');
 await page.getByLabel('交易日期',{exact:true}).fill('2026-10-17T10:00');
 await page.getByLabel('单价',{exact:true}).fill('180');
 await page.getByRole('button',{name:'保存交易',exact:true}).click();
 await expect.poll(()=>mutations.some(m=>m.path.endsWith('/transactions'))).toBe(true);
 await noOverflow();
 await page.screenshot({path:'../../specs/022-mobile-p0-parity/phone-detail.png',fullPage:true});
 page.on('dialog',dialog=>dialog.accept());
 await page.getByRole('button',{name:'移入回收站',exact:true}).click();
 await expect(page.getByRole('heading',{name:'我的卡库',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'回收站',exact:true}).click();
 await page.getByRole('button',{name:'恢复卡片',exact:true}).click();
 await expect(page.getByText('回收站为空')).toBeVisible();
 await page.getByRole('button',{name:'‹ 返回卡库',exact:true}).click();
 await page.getByLabel('选择 Fixture Player').check();
 await page.getByRole('button',{name:'删除选中 (1)',exact:true}).click();
 await expect(page.getByText(/已移入回收站 1 张/)).toBeVisible();
 await page.getByRole('button',{name:'回收站',exact:true}).click();
 await page.getByRole('button',{name:'永久删除',exact:true}).click();
 await expect(page.getByText('回收站为空')).toBeVisible();
 expect(deleted).toBe(true);
 await noOverflow();
 await page.screenshot({path:'../../specs/022-mobile-p0-parity/phone-trash.png'});
});
