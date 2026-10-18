import {expect, it, vi} from 'vitest';
import {MobileApi} from '../src/api';

it('honors caller cancellation without expiring the session', async () => {
  const expired = vi.fn();
  const controller = new AbortController();
  const transport = vi.fn(async (_url: unknown, options?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    options?.signal?.addEventListener('abort', () => reject(new DOMException('cancelled','AbortError')));
  }));
  const api = new MobileApi('https://cards.example','token',expired,transport);
  const pending = api.request('/v1/purchase-cost/cardhobby/search', {signal: controller.signal});
  controller.abort();
  await expect(pending).rejects.toThrow('请求已取消');
  expect(expired).not.toHaveBeenCalled();
});
it('does not send an already cancelled discovery request', async () => {
  const transport = vi.fn();
  const api = new MobileApi('https://cards.example','token',()=>{},transport);
  const controller = new AbortController(); controller.abort();
  await expect(api.request('/v1/purchase-cost/items/a/search-plan', {signal:controller.signal})).rejects.toThrow();
  expect(transport).not.toHaveBeenCalled();
});
it('supports the longer order search timeout', async () => {
  vi.useFakeTimers();
  try {
    let aborted = false;
    const api = new MobileApi('https://cards.example','token',()=>{}, async (_url, options) => new Promise<Response>((resolve, reject) => {
      options?.signal?.addEventListener('abort', () => {aborted=true; reject(new DOMException('timeout','AbortError'));});
      setTimeout(() => resolve(new Response('{}')), 45000);
    }));
    const pending = api.request('/v1/purchase-cost/cardhobby/search', {}, false, 180000);
    const checked = expect(pending).resolves.toEqual({});
    await vi.advanceTimersByTimeAsync(45000);
    await checked;
    expect(aborted).toBe(false);
    api.dispose();
  } finally {vi.useRealTimers();}
});

it('preserves authentication failure even though expiring the session aborts its requests', async () => {
  const expired=vi.fn();
  const api=new MobileApi('https://cards.example','token',expired,async()=>new Response(JSON.stringify({detail:'expired'}),{status:401}));
  await expect(api.get('/v1/auth/me')).rejects.toMatchObject({status:401,message:'expired'});
  expect(expired).toHaveBeenCalledOnce();
});
