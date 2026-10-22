const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('../../mobile/node_modules/@playwright/test');

// Run against a built Next server: KNOWLEDGE_BROWSER_URL=http://127.0.0.1:18766 node --test tests/knowledge-image-viewer-browser.cjs
// Synthetic dimensions isolate browser geometry; real captured-media smoke is separate.
test('390px viewer can scroll to every corner at 3x zoom after a 90 degree rotation',async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});
  const image={id:'geometry',product_id:null,card_id:null,image_url:'/v1/knowledge/browse/media/geometry/image',thumbnail_url:'/v1/knowledge/browse/media/geometry/image?thumbnail=true',side:'unknown',image_type:'official_design',source_url:null,source_authority:null,caption:'缩放回归图片',player:null,card_number:null,parallel:null,pair_id:null};
  await page.route('**/v1/knowledge/browse/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname.endsWith('/image'))return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="#cde5d4"/><rect width="30" height="30" fill="red"/><rect x="370" width="30" height="30" fill="green"/><rect y="570" width="30" height="30" fill="blue"/><rect x="370" y="570" width="30" height="30" fill="black"/></svg>'});
   const body=url.pathname.endsWith('/summary')?{version:19,version_id:'fixture',discovery_complete:false,summary:{manufacturers:0,products:0,card_facts:0,images:1,paired_cards:0},manufacturers:[],seasons:[],entity_types:[],issues:[]}:{items:[image],total:1,offset:0,limit:24,version:19};
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto(`${process.env.KNOWLEDGE_BROWSER_URL??'http://127.0.0.1:18766'}/knowledge?view=gallery`);
  await page.getByRole('button',{name:'查看图片 缩放回归图片'}).click();
  const dialog=page.getByRole('dialog',{name:'知识图片查看器'});
  await dialog.locator('img').evaluate(img=>img.decode());
  for(let i=0;i<8;i++)await page.getByRole('button',{name:'放大图片',exact:true}).click();
  await page.getByRole('button',{name:'向右旋转图片'}).click();
  await page.waitForTimeout(180);
  for(const [x,y] of [['left','top'],['right','top'],['left','bottom'],['right','bottom']]){
   const geometry=await dialog.locator('.kb-viewer-image').evaluate((port,{x,y})=>{
    port.scrollLeft=x==='left'?0:port.scrollWidth;port.scrollTop=y==='top'?0:port.scrollHeight;
    const image=port.querySelector('img').getBoundingClientRect(),frame=port.getBoundingClientRect();
    return {image:{left:image.left,top:image.top,right:image.right,bottom:image.bottom,width:image.width,height:image.height},frame:{left:frame.left,top:frame.top,right:frame.right,bottom:frame.bottom},scrollWidth:port.scrollWidth,scrollHeight:port.scrollHeight};
   },{x,y});
   assert.ok(geometry.image.width>(geometry.frame.right-geometry.frame.left)*2,'3x rotated image must actually exceed the viewport width');
   assert.ok(geometry.image.height>geometry.frame.bottom-geometry.frame.top,'3x rotated image must actually exceed the viewport height');
   assert.ok(x==='left'?geometry.image.left>=geometry.frame.left-2:geometry.image.right<=geometry.frame.right+2,`${x}/${y} horizontal corner unreachable: ${JSON.stringify(geometry)}`);
   assert.ok(y==='top'?geometry.image.top>=geometry.frame.top-2:geometry.image.bottom<=geometry.frame.bottom+2,`${x}/${y} vertical corner unreachable: ${JSON.stringify(geometry)}`);
  }
 }finally{await browser.close();}
});
