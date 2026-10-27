import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,act} from '@testing-library/react';
import type {MobileApi} from '../src/api';
import {AutomaticCostNotice} from '../src/AutomaticCostNotice';
afterEach(cleanup);
const blocked={blocked_count:25,notice_key:'first-batch',latest_blocked_at:'2026-10-28T00:00:20Z',code:'LOGIN_REQUIRED',worker_enabled:true};
const makeApi=(read:(path:string,options?:RequestInit)=>Promise<unknown>)=>({request:vi.fn(read)}) as unknown as MobileApi;

it('opens login for a background block and keeps a reopenable reminder without repeat popups',async()=>{
 const api=makeApi(async()=>blocked);
 render(<AutomaticCostNotice api={api} ownerId="owner"/>);
 await screen.findByRole('dialog');expect(screen.getByLabelText('卡淘密码')).toBeTruthy();
 expect(screen.getByRole('dialog').textContent).toContain('25');
 fireEvent.click(screen.getByRole('button',{name:'稍后处理'}));
 fireEvent(window,new Event('focus'));
 await waitFor(()=>expect(api.request).toHaveBeenCalledTimes(2));
 expect(screen.queryByRole('dialog')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'登录或验证卡淘'}));expect(screen.getByRole('dialog')).toBeTruthy();
 expect(vi.mocked(api.request).mock.calls.every(([path])=>path.endsWith('/automatic/attention'))).toBe(true);
});

it('detects delayed verification failures and never sends a login or SMS on detection',async()=>{
 let state={...blocked,blocked_count:0,notice_key:null as string|null,code:null as string|null};
 const api=makeApi(async path=>path.endsWith('/verification')?{sms_sent:false,retry_after_seconds:0,captcha_url:'https://cms.cardhobby.com.cn/checkcode/index?mobile=13800000000'}:state);
 render(<AutomaticCostNotice api={api} ownerId="owner"/>);
 await waitFor(()=>expect(api.request).toHaveBeenCalledTimes(1));
 state={...blocked,code:'VERIFICATION_REQUIRED'};fireEvent(window,new Event('focus'));
 await screen.findByText('尚未确认发送短信，请先完成卡淘滑块验证。');
 expect(vi.mocked(api.request).mock.calls.some(([path])=>path.endsWith('/send-sms')||path.endsWith('/login'))).toBe(false);
});

it('finishes login then refreshes the notice without issuing per-card searches',async()=>{
 let connected=false;
 const api=makeApi(async path=>{if(path.endsWith('/login')){connected=true;return {saved:true,state:'connected'};}if(!path.endsWith('/automatic/attention'))throw Error(path);return connected?{...blocked,blocked_count:0,notice_key:null,code:null}:blocked;});
 render(<AutomaticCostNotice api={api} ownerId="owner"/>);
 await screen.findByRole('dialog');
 fireEvent.change(screen.getByLabelText('卡淘用户名 / 手机号'),{target:{value:'fixture'}});
 fireEvent.change(screen.getByLabelText('卡淘密码'),{target:{value:'fixture-password'}});
 fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 await waitFor(()=>expect(screen.queryByRole('button',{name:'登录或验证卡淘'})).toBeNull());
});

it('aborts old-owner requests and ignores their late results',async()=>{
 let finish!:()=>void,signal:AbortSignal|undefined;
 const first=makeApi((path,options)=>{signal=options?.signal??undefined;return new Promise(resolve=>{finish=()=>resolve(blocked);});});
 const second=makeApi(async()=>({...blocked,blocked_count:0,notice_key:null,code:null}));
 const view=render(<AutomaticCostNotice api={first} ownerId="first"/>);
 await waitFor(()=>expect(finish).toBeTruthy());
 view.rerender(<AutomaticCostNotice api={second} ownerId="second"/>);
 expect(signal?.aborted).toBe(true);await act(async()=>finish());
 expect(screen.queryByRole('dialog')).toBeNull();
});

it('polls an already-open foreground app when a worker fails later',async()=>{
 vi.useFakeTimers();
 const hidden=Object.getOwnPropertyDescriptor(document,'hidden');
 Object.defineProperty(document,'hidden',{configurable:true,value:false});
 let state={...blocked,blocked_count:0,notice_key:null as string|null,code:null as string|null};
 const api=makeApi(async()=>state);
 try {
  render(<AutomaticCostNotice api={api} ownerId="owner"/>);
  await act(async()=>{});
  expect(screen.queryByRole('dialog')).toBeNull();
  state=blocked;
  await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});
  expect(screen.getByRole('dialog')).toBeTruthy();
 } finally {
  cleanup();vi.useRealTimers();
  if(hidden)Object.defineProperty(document,'hidden',hidden);else delete (document as unknown as {hidden?:boolean}).hidden;
 }
});

it('keeps a dismissed notice closed when existing blocks are removed',async()=>{
 let state=blocked;
 const api=makeApi(async()=>state);
 render(<AutomaticCostNotice api={api} ownerId="owner"/>);
 await screen.findByRole('dialog');fireEvent.click(screen.getByRole('button',{name:'稍后处理'}));
 state={...blocked,blocked_count:24,notice_key:'older-remaining-block',latest_blocked_at:'2026-10-28T00:00:19Z'};
 await act(async()=>{fireEvent(window,new Event('focus'));});
 expect(screen.queryByRole('dialog')).toBeNull();
 expect(screen.getByRole('status').textContent).toContain('24');
});
