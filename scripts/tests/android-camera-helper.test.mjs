import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, writeFile, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// ADB is the only fake boundary. HTTP, authorization, capture polling and bytes are real.
const jpeg = Buffer.from('ffd8ffe000104a46494600010100000100010000ffd9', 'hex');
const old = '/sdcard/DCIM/Camera/old.jpg';
const fresh = '/sdcard/DCIM/Camera/IMG_new.jpg';
async function fixture(t, options = {}) {
  const { createAndroidCameraHelper } = await import('../android-camera-helper.mjs');
  const calls = [];
  let shutter = false, afterLists = 0;
  const adbRunner = async (args, { signal } = {}) => {
    calls.push(args);
    assert.ok(!signal?.aborted);
    if (args[0] === 'devices') return options.devices ?? 'List of devices attached\nredmi-1 device product:foo model:Redmi_Note_13 device:foo transport_id:1\nlocked unauthorized usb:1\ngone offline usb:2\n';
    assert.deepEqual(args.slice(0, 2), ['-s', 'redmi-1']);
    if (args.includes('dumpsys')) return options.unknownLock ? 'unexpected policy' : options.screenOff ? 'mKeyguardShowing=false\nscreenState=SCREEN_STATE_OFF' : options.locked ? 'mKeyguardShowing=true' : 'mKeyguardShowing=false';
    if (args.includes('am')) return options.cameraFailure ? 'Error: Activity not started, unable to resolve Intent' : 'Starting: Intent { act=android.media.action.STILL_IMAGE_CAMERA }\nStatus: ok\nActivity: com.android.camera/.Camera\n';
    if (args.includes('keyevent')) { shutter = true; options.onShutter?.(); return ''; }
    if (args[2] === 'shell' && args[3].includes('/sdcard/DCIM/Camera/*')) {
      if (!shutter) return `${old}|100|100\n`;
      afterLists++;
      if (options.waitForCancel) return await new Promise((_, reject) => signal.addEventListener('abort', () => reject(Object.assign(new Error('cancelled'), { status: 499 })), { once: true }));
      if (options.disconnect) throw new Error('device offline');
      if (options.oldOnly) return `${old}|100|200\n`; // An overwritten old filename is not new.
      if (options.unsafePath) return `${old}|100|100\n/sdcard/DCIM/Camera/x;reboot.jpg|25|200\n`;
      return `${old}|100|100\n${fresh}|${options.tooLarge ? 99999999 : afterLists === 1 ? 8 : jpeg.length}|200\n${options.multiple ? '/sdcard/DCIM/Camera/IMG_extra.jpg|25|201\n' : ''}`;
    }
    if (args[2] === 'exec-out') {
      assert.deepEqual(args.slice(2), ['exec-out', 'cat', fresh]);
      return options.badImage ? Buffer.alloc(jpeg.length, 65) : jpeg;
    }
    throw new Error(`Unexpected ADB args ${JSON.stringify(args)}`);
  };
  const server = createAndroidCameraHelper({ adbRunner, pollMs: 3, cameraReadyMs: 0, captureTimeoutMs: 70, ...options.helper });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const headers = { Origin: 'https://cardsp.cn', 'X-Camera-Client': 'cardsp-web-v1' };
  const devices = () => fetch(`${url}/v1/devices`, { headers });
  const capture = (token, payload = { serial: 'redmi-1' }, extra = {}) => fetch(`${url}/v1/capture`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json', 'X-Camera-Token': token }, body: JSON.stringify(payload), ...extra });
  const connect = async () => { const response = await devices(); assert.equal(response.status, 200); return response.json(); };
  return { server, url, headers, devices, capture, connect, calls };
}

test('only authorized devices are selectable; blocked states have useful feedback', async t => {
  const f = await fixture(t); const body = await f.connect();
  assert.deepEqual(body.devices, [{ serial: 'redmi-1', name: 'Redmi Note 13' }]);
  assert.match(body.message, /授权/); assert.match(body.message, /离线/);
  assert.ok(body.token.length >= 32);
  const empty = await fixture(t, { devices: 'List of devices attached\n' });
  assert.match((await empty.connect()).message, /USB/);
});

test('origin, client, token and DNS rebinding checks stop ADB access', async t => {
  const f = await fixture(t);
  for (const headers of [{ ...f.headers, Origin: 'https://evil.example' }, { Origin: 'https://cardsp.cn' }, {}]) {
    assert.equal((await fetch(`${f.url}/v1/devices`, { headers })).status, 403);
  }
  const status = await new Promise((resolve, reject) => { const req = http.get(`${f.url}/v1/devices`, { headers: { ...f.headers, Host: 'evil.example' } }, res => { res.resume(); resolve(res.statusCode); }); req.on('error', reject); });
  assert.equal(status, 403); assert.equal(f.calls.length, 0);
  assert.equal((await f.capture('wrong')).status, 403); assert.equal(f.calls.length, 0);
});

test('private network preflight allows only known origins, methods and headers', async t => {
  const f = await fixture(t);
  const options = headers => fetch(`${f.url}/v1/capture`, { method: 'OPTIONS', headers: { Origin: 'https://cardsp.cn', 'Access-Control-Request-Method': 'POST', ...headers } });
  const good = await options({ 'Access-Control-Request-Headers': 'content-type,x-camera-token,x-camera-client', 'Access-Control-Request-Private-Network': 'true' });
  assert.equal(good.status, 204); assert.equal(good.headers.get('access-control-allow-private-network'), 'true');
  assert.equal((await options({ 'Access-Control-Request-Headers': 'authorization' })).status, 403);
  assert.equal((await options({ 'Access-Control-Request-Method': 'DELETE' })).status, 403);
});

test('capture requires an enumerated serial and rejects arbitrary parameters before shutter', async t => {
  const f = await fixture(t); const { token } = await f.connect();
  for (const payload of [{ serial: 'unknown' }, { serial: 'redmi-1;reboot' }, { serial: 'redmi-1', command: 'reboot' }, {}, []]) assert.equal((await f.capture(token, payload)).status, 422);
  assert.equal(f.calls.filter(args => args.includes('keyevent')).length, 0);
});

test('single shutter returns only a newly created stable JPEG from selected device', async t => {
  const f = await fixture(t); const { token } = await f.connect();
  const response = await f.capture(token);
  assert.equal(response.status, 200); assert.equal(response.headers.get('content-type'), 'image/jpeg');
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), jpeg);
  assert.equal(f.calls.filter(args => args.includes('keyevent')).length, 1);
  assert.equal(f.calls.some(args => args.includes('rm')), false);
});

test('locked screen and camera launch failure stop before triggering shutter', async t => {
  for (const options of [{ locked: true }, { cameraFailure: true }]) {
    const f = await fixture(t, options); const { token } = await f.connect();
    const response = await f.capture(token); assert.equal(response.status, 409);
    assert.equal(f.calls.filter(args => args.includes('keyevent')).length, 0);
    assert.equal((await f.devices()).status, 200);
  }
});

test('old photos and unsafe filenames never become capture results; timeout releases busy', async t => {
  for (const options of [{ oldOnly: true }, { unsafePath: true }]) {
    const f = await fixture(t, options); const { token } = await f.connect();
    assert.equal((await f.capture(token)).status, 504);
    assert.equal(f.calls.filter(args => args[2] === 'exec-out').length, 0);
    assert.equal((await f.devices()).status, 200);
  }
});

test('disconnect and invalid image fail safely and release device', async t => {
  for (const options of [{ disconnect: true }, { badImage: true }]) {
    const f = await fixture(t, options); const { token } = await f.connect();
    assert.equal((await f.capture(token)).status, 503);
    assert.equal((await f.devices()).status, 200);
  }
});

test('concurrent capture is rejected and client cancellation releases busy', async t => {
  let fired; const shutter = new Promise(resolve => { fired = resolve; });
  const f = await fixture(t, { waitForCancel: true, onShutter: fired }); const { token } = await f.connect();
  const controller = new AbortController();
  const pending = f.capture(token, undefined, { signal: controller.signal }).catch(() => null);
  await shutter;
  assert.equal((await f.capture(token)).status, 409);
  controller.abort(); await pending;
  let status;
  for (let i = 0; i < 30; i++) { await new Promise(resolve => setTimeout(resolve, 5)); status = (await f.devices()).status; if (status === 200) break; }
  assert.equal(status, 200);
});


test('unknown lock state or sleeping screen cannot trigger a shutter', async t => {
  for (const options of [{ unknownLock: true }, { screenOff: true }]) {
    const f = await fixture(t, options); const { token } = await f.connect();
    assert.equal((await f.capture(token)).status, 409);
    assert.equal(f.calls.filter(args => args.includes('keyevent')).length, 0);
  }
});

test('oversized or ambiguous new photos are rejected before image transfer', async t => {
  for (const [options, status] of [[{ tooLarge: true }, 413], [{ multiple: true }, 409]]) {
    const f = await fixture(t, options); const { token } = await f.connect();
    assert.equal((await f.capture(token)).status, status);
    assert.equal(f.calls.filter(args => args[2] === 'exec-out').length, 0);
    assert.equal((await f.devices()).status, 200);
  }
});

test('helper shutdown cancels active capture', async t => {
  let fired; const shutter = new Promise(resolve => { fired = resolve; });
  const f = await fixture(t, { waitForCancel: true, onShutter: fired }); const { token } = await f.connect();
  const pending = f.capture(token); await shutter;
  const closing = new Promise(resolve => f.server.close(resolve));
  assert.equal((await pending).status, 499); await closing;
});

test('real subprocess runner bounds bytes, honors cancellation, times out and preserves literal argv', async t => {
  const { runAdb } = await import('../android-camera-helper.mjs');
  const root = await mkdtemp(join(tmpdir(), 'android-adb-test-'));
  const executable = join(root, 'fake-adb');
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(executable, `#!${process.execPath}\nconst args=process.argv.slice(2);if(args[0]==='hang'){setInterval(()=>{},100);}else if(args[0]==='big'){process.stdout.write('x'.repeat(4096));}else{process.stdout.write(JSON.stringify(args));}`);
  await chmod(executable, 0o700);
  const output = await runAdb(['-s', 'serial;echo bad', 'devices'], { adbPath: executable });
  assert.deepEqual(JSON.parse(String(output)), ['-s', 'serial;echo bad', 'devices']);
  await assert.rejects(runAdb(['big'], { adbPath: executable, maxBytes: 100 }), error => error.status === 413);
  await assert.rejects(runAdb(['hang'], { adbPath: executable, timeoutMs: 30 }), error => error.status === 504);
  const controller = new AbortController();
  const pending = runAdb(['hang'], { adbPath: executable, signal: controller.signal });
  controller.abort(); await assert.rejects(pending);
  await assert.rejects(runAdb(['devices'], { adbPath: join(root, 'missing') }), error => error.status === 503);
});
