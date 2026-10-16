const {test}=require('node:test');const assert=require('node:assert/strict');
const {captureCapabilities}=require('../lib/capture-capabilities.ts');
test('capability network failures remain retryable and never declare legacy support',async()=>{
 const paths=[];global.fetch=async url=>{paths.push(url);return {ok:false,status:503};};
 await assert.rejects(captureCapabilities(),/503/);await assert.rejects(captureCapabilities(),/503/);
 global.fetch=async url=>{paths.push(url);return {ok:true,status:200,json:async()=>({image_variants:true,capture_jobs:true})};};
 assert.equal((await captureCapabilities()).image_variants,true);
 assert.deepEqual(paths,['/v1/client-capabilities','/v1/client-capabilities','/v1/client-capabilities']);
 await captureCapabilities();assert.equal(paths.length,3);
});
