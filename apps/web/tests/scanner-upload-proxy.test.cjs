const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Readable}=require('node:stream');
const {createHash}=require('node:crypto');
const {getCloneableBody}=require('next/dist/server/body-streams');
const bytes=require('next/dist/compiled/bytes');
const config=require('../next.config.ts').default;
const {uploadFlatbedSheet}=require('../lib/api.ts');

test('Next rewrite preserves a formal scanner multipart body larger than 10 MB',async()=>{
 const image=Buffer.alloc(16*1024*1024,137);
 const body=Buffer.concat([Buffer.from('--scan\r\nContent-Disposition: form-data; name="image"; filename="back.png"\r\nContent-Type: image/png\r\n\r\n'),image,Buffer.from('\r\n--scan--\r\n')]);
 const request=Readable.from((function*(){for(let i=0;i<body.length;i+=65536)yield body.subarray(i,i+65536);})());
 request.url='/v1/flatbed/sessions/batch/sheets/back';
 const configured=config.experimental?.middlewareClientMaxBodySize;
 const clone=getCloneableBody(request,typeof configured==='string'?bytes.parse(configured):configured).cloneBodyStream();
 const received=[];for await(const chunk of clone)received.push(chunk);
 const result=Buffer.concat(received);
 assert.equal(result.length,body.length,'scanner PNG must not be truncated by the rewrite proxy');
 assert.equal(createHash('sha256').update(result).digest('hex'),createHash('sha256').update(body).digest('hex'));
});

test('a stalled upload ends with a recoverable timeout instead of waiting forever',async()=>{
 const controller=new AbortController();const originalTimeout=AbortSignal.timeout;const originalFetch=global.fetch;
 AbortSignal.timeout=()=>controller.signal;
 let requestSignal;
 global.fetch=async(_url,options)=>{requestSignal=options.signal;return new Promise((_resolve,reject)=>options.signal?.addEventListener('abort',()=>reject(options.signal.reason)));};
 const uploading=uploadFlatbedSheet('batch','back',new File(['scan'],'back.png',{type:'image/png'}));
 try {
  assert.ok(requestSignal,'cloud upload must have a deadline');
  controller.abort(new DOMException('timed out','TimeoutError'));
  await assert.rejects(uploading,/上传或处理超时.*重试/);
 }finally{AbortSignal.timeout=originalTimeout;global.fetch=originalFetch;}
});
