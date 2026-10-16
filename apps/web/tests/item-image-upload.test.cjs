const {test}=require('node:test');
const assert=require('node:assert/strict');
const {uploadItemImages}=require('../lib/item-image-upload.ts');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

test('both sides start immediately and completion waits for both',async()=>{
 const front=deferred(),back=deferred(),calls=[];
 let done=false;
 const pending=uploadItemImages('card','owner',[{side:'front',file:'front-file'},{side:'back',file:'back-file'}],
  async (...args)=>{calls.push(args);return args[2]==='front'?front.promise:back.promise;}).then(()=>{done=true;});
 assert.deepEqual(calls.map(x=>x[2]),['front','back']);
 front.resolve({});await Promise.resolve();assert.equal(done,false);
 back.resolve({});await pending;assert.equal(done,true);
});

test('one failed upload waits for the other before reporting failure',async()=>{
 const front=deferred(),back=deferred();let settled=false;
 const pending=uploadItemImages('card','owner',[{side:'front',file:'front-file'},{side:'back',file:'back-file'}],
  async (id,owner,side)=>side==='front'?front.promise:back.promise);
 const observed=pending.catch(error=>{settled=true;return error;});
 front.reject(new Error('front failed'));await Promise.resolve();await Promise.resolve();assert.equal(settled,false);
 back.resolve({});assert.match((await observed).message,/front failed/);
});

test('empty and single-side input do not add uploads',async()=>{
 const calls=[];const upload=async(...args)=>{calls.push(args);return {};};
 await uploadItemImages('card','owner',[],upload);assert.equal(calls.length,0);
 await uploadItemImages('card','owner',[{side:'back',file:'back-file'}],upload);assert.equal(calls.length,1);
});
