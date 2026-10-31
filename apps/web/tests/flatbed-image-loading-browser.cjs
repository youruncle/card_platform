// Run against an isolated built Next server with FLATBED_BROWSER_URL set.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('../../mobile/node_modules/@playwright/test');

test('scanner browser requests thumbnails first and originals only when opened', async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.FLATBED_BROWSER_CHANNEL ? { channel: process.env.FLATBED_BROWSER_CHANNEL } : {}),
  });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const imageRequests = [];
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const region = (id, side) => ({ id, side, ordinal: 0, bounds: { x: .1, y: .1, width: .4, height: .7 }, confidence: .9 });
      await page.route('**/v1/**', async route => {
        const url = new URL(route.request().url()), path = url.pathname;
        const respond = data => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
        if (path.endsWith('/image')) {
          imageRequests.push(url);
          return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="960"><rect width="640" height="960" fill="#cde5d4"/><rect x="64" y="96" width="256" height="672" fill="#476a8a"/></svg>' });
        }
        if (path === '/v1/auth/me') return respond({ id: 'browser-owner', display_name: 'Scanner Browser' });
        if (path === '/v1/portfolio/items/page') return respond({ items: [], page: 1, total: 0, total_pages: 1, page_size: 30, snapshot_token: 'fixture' });
        if (path === '/v1/portfolio/summary') return respond({ owner_id: 'browser-owner', base_currency: 'CNY', item_count: 0, total_cost: '0', total_market_value: '0', unrealized_profit: '0', realized_profit: '0' });
        if (path === '/v1/flatbed/device') return respond({ name: 'Canon', serial: 'fixture' });
        if (path === '/v1/flatbed/sessions') return respond([{ id: 'session', submitted_at: null }]);
        if (path === '/v1/flatbed/sessions/session') return respond({
          id: 'session', dpi: 300, regions: [region('front', 'front'), region('back', 'back')],
          sheets: [{ id: 'front-sheet', side: 'front', revision: 1 }, { id: 'back-sheet', side: 'back', revision: 1 }],
          pairs: [{ front_region_id: 'front', back_region_id: 'back', score: .9, confidence_level: 'high', identity_status: 'consistent', selected: true, conflicts: [], signals: {} }],
        });
        if (path === '/v1/scanner/jobs' || path.includes('identity-corrections')) return respond([]);
        return respond({});
      });
      await page.goto(process.env.FLATBED_BROWSER_URL ?? 'http://127.0.0.1:18767');
      await page.getByRole('button', { name: '图片识别', exact: true }).click();
      await page.getByRole('button', { name: /Canon 扫描仪/ }).click();
      const overview = page.getByAltText('正面整版扫描缩略图');
      await overview.evaluate(img => img.decode());
      await page.locator('.flatbed-panel img').evaluateAll(imgs => Promise.all(imgs.map(img => img.decode())));
      assert.equal(imageRequests.length, 4, 'the overview and pair list share each region thumbnail');
      assert.ok(imageRequests.every(url => ['thumb', 'preview'].includes(url.searchParams.get('variant'))));
      await overview.evaluate(img => { img.dataset.loadedNode = 'retained'; });
      await page.getByRole('button', { name: '查看原图', exact: true }).first().click();
      const lightbox = page.locator('.flatbed-lightbox img');
      await lightbox.evaluate(img => img.decode());
      assert.equal(imageRequests.at(-1).searchParams.get('variant'), null);
      assert.equal(await overview.getAttribute('data-loaded-node'), 'retained');
      await page.getByRole('button', { name: '关闭大图' }).click();
      await page.getByRole('button', { name: '校正正面 1' }).click();
      const editor = page.getByAltText('整版布局校正');
      await editor.evaluate(img => img.decode());
      assert.equal(new URL(await editor.getAttribute('src'), 'http://test').searchParams.get('variant'), 'preview');
      assert.deepEqual(errors, []);
      await page.screenshot({ path: `/tmp/flatbed-image-loading-${width}.png`, fullPage: true });
      await page.close();
    }
  } finally { await browser.close(); }
});
