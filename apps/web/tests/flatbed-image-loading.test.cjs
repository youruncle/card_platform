const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<html><body></body></html>', { url: 'http://localhost:3000' });
global.window = dom.window; global.document = dom.window.document;
Object.defineProperty(global, 'navigator', { value: dom.window.navigator, configurable: true });
global.HTMLElement = dom.window.HTMLElement; global.React = require('react');
const { render, screen, fireEvent, cleanup, waitFor } = require('@testing-library/react');
const { FlatbedBatchPanel } = require('../components/flatbed-batch-panel.tsx');
afterEach(() => { cleanup(); dom.reconfigure({ url: 'http://localhost:3000' }); });

test('scanner uses lightweight images until zoom and retains loaded overview across UI updates', async () => {
  const response = data => ({ ok: true, json: async () => data });
  const region = (id, side) => ({ id, side, ordinal: 0, bounds: { x: .1, y: .1, width: .4, height: .7 }, confidence: .9 });
  global.fetch = async url => {
    const path = new URL(url, 'http://localhost').pathname;
    if (path === '/v1/flatbed/device') return response({ name: 'Canon', serial: 'fixture' });
    if (path === '/v1/flatbed/sessions') return response([{ id: 'session', submitted_at: null }]);
    if (path === '/v1/flatbed/sessions/session') return response({
      id: 'session', dpi: 300, regions: [region('front', 'front'), region('back', 'back')],
      sheets: [{ id: 'front-sheet', side: 'front', revision: 1 }, { id: 'back-sheet', side: 'back', revision: 1 }],
      pairs: [{ front_region_id: 'front', back_region_id: 'back', score: .9, confidence_level: 'high', identity_status: 'consistent', selected: true, conflicts: [], signals: {} }],
    });
    throw Error(`Unexpected request ${path}`);
  };
  const { container } = render(React.createElement(FlatbedBatchPanel));
  await screen.findByRole('button', { name: '确认配对并录入 1 张卡片' });
  await waitFor(() => assert.equal(screen.getByRole('button', { name: '校正正面 1' }).disabled, false));
  const overview = screen.getByAltText('正面整版扫描缩略图');
  assert.equal(new URL(overview.src).searchParams.get('variant'), 'preview');
  for (const img of container.querySelectorAll('.flatbed-region-thumbs img, .flatbed-pair-visuals img')) {
    assert.equal(new URL(img.src).searchParams.get('variant'), 'thumb');
  }
  fireEvent.click(screen.getAllByRole('button', { name: '查看原图' })[0]);
  const full = screen.getByAltText('正面整版扫描');
  assert.equal(new URL(full.src).searchParams.get('variant'), null);
  assert.equal(screen.getByAltText('正面整版扫描缩略图'), overview);
  fireEvent.click(screen.getByRole('button', { name: /关闭/ }));
  fireEvent.click(container.querySelector('.flatbed-pair-visuals > button'));
  const zoom = container.querySelector('.flatbed-lightbox img');
  assert.equal(new URL(zoom.src).searchParams.get('variant'), null);
});

test('local scan is visible while cloud upload and OCR are still pending', async () => {
  dom.reconfigure({ url: 'https://cardsp.cn/' });
  const response = data => ({ ok: true, json: async () => data });
  let finishUpload;
  global.fetch = async url => {
    const path = new URL(url, 'https://cardsp.cn').pathname;
    if (String(url).startsWith('http://127.0.0.1:8766')) {
      if (path.endsWith('/device')) return response({ name: 'Canon', serial: 'fixture', token: 'local-token' });
      return new Response(new Uint8Array([137, 80, 78, 71]), { headers: { 'Content-Type': 'image/png' } });
    }
    if (path === '/v1/flatbed/sessions') return response([{ id: 'session', submitted_at: null }]);
    if (path === '/v1/flatbed/sessions/session') return response({ id: 'session', dpi: 300, regions: [], sheets: [], pairs: [] });
    if (path.endsWith('/sheets/front')) return new Promise(resolve => {
      finishUpload = () => resolve({ ok: false, status: 503, json: async () => ({ detail: '稍后重试' }) });
    });
    throw Error(`Unexpected request ${path}`);
  };
  render(React.createElement(FlatbedBatchPanel));
  const scan = (await screen.findAllByRole('button', { name: '正式扫描' }))[0];
  await waitFor(() => assert.equal(scan.disabled, false));
  fireEvent.click(scan);
  await waitFor(() => assert.ok(finishUpload));
  const preview = await screen.findByAltText('正面本机扫描预览');
  assert.match(preview.src, /^blob:/);
  assert.match(screen.getByRole('status').textContent, /上传.*裁剪识别/);
  finishUpload();
  await screen.findByRole('button', { name: '重试上传扫描图' });
  assert.equal(screen.getByAltText('正面本机扫描预览'), preview);
});
