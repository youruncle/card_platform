import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {deflateSync} from 'node:zlib';
function crc32(bytes){let c=0xffffffff;for(const b of bytes){c^=b;for(let i=0;i<8;i++)c=(c>>>1)^((c&1)?0xedb88320:0);}return(c^0xffffffff)>>>0;}
function png(rectangles=[]){const w=640,h=480,rows=Buffer.alloc((w*3+1)*h,40);for(let y=0;y<h;y++){rows[y*(w*3+1)]=0;for(let x=0;x<w;x++){const v=rectangles.some(r=>x>=r.x&&x<r.x+r.w&&y>=r.y&&y<r.y+r.h)?220:40;rows.fill(v,y*(w*3+1)+1+x*3,y*(w*3+1)+4+x*3);}}const chunk=(type,bytes)=>{const name=Buffer.from(type),size=Buffer.alloc(4),crc=Buffer.alloc(4);size.writeUInt32BE(bytes.length);crc.writeUInt32BE(crc32(Buffer.concat([name,bytes])));return Buffer.concat([size,name,bytes,crc]);};const header=Buffer.alloc(13);header.writeUInt32BE(w);header.writeUInt32BE(h,4);header[8]=8;header[9]=2;return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',header),chunk('IDAT',deflateSync(rows)),chunk('IEND',Buffer.alloc(0))]);}
test('native preview crops pixels and finds rectangular card candidates without inventing a card in blank frames',{skip:process.platform!=='darwin',timeout:90000},t=>{
 const root=mkdtempSync(join(tmpdir(),'cardsp-frame-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 const binary=join(root,'frame');const compile=spawnSync('/usr/bin/xcrun',['swiftc','scripts/android-camera-frame.swift','-O','-o',binary],{encoding:'utf8',timeout:60000});assert.equal(compile.status,0,compile.stderr);
 const run=(image,args=['0','0','640','480'])=>spawnSync(binary,args,{input:image,maxBuffer:3*1024*1024,timeout:15000});
 const frame=run(png([{x:200,y:70,w:220,h:330}]));assert.equal(frame.status,0,String(frame.stderr));const result=JSON.parse(String(frame.stdout));assert.match(result.preview,/^data:image\/jpeg;base64,/);assert.equal(result.width,640);assert.equal(result.height,480);assert.ok(result.rectangles.some(r=>Math.abs(r.x-.3125)<.03&&Math.abs(r.y-.1458)<.03&&r.width>.3));
 assert.equal(JSON.parse(String(run(png()).stdout)).rectangles.length,0);
 const two=JSON.parse(String(run(png([{x:60,y:70,w:170,h:250},{x:380,y:100,w:170,h:250}])).stdout));assert.ok(two.rectangles.length>=2);
 assert.notEqual(run(png(),['-1','0','640','480']).status,0);
 assert.notEqual(run(Buffer.from('not an image')).status,0);
 const real=JSON.parse(String(run(readFileSync('scripts/tests/fixtures/redmi-card-camera.png'),['0','240','1080','1440']).stdout));
 assert.ok(real.rectangles.some(r=>r.x>.3&&r.x<.5&&r.y>.2&&r.y<.4&&r.width>.4&&r.height>.5),'real transparent-holder card should be detected');
});
