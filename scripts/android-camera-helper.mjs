#!/usr/bin/env node
// Loopback hardware transport. No cloud credentials, persistent files, or phone-image deletion.
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createNativeCameraControls, captureNativeCamera } from './android-camera-native.mjs';

const ORIGINS = new Set(['https://cardsp.cn', 'https://www.cardsp.cn', 'http://localhost:3000', 'http://127.0.0.1:3000']);
const CLIENT = 'cardsp-web-v1';
const ADB_PATH = '/Users/song/Library/Android/sdk/platform-tools/adb';
const MAX_IMAGE_BYTES = 25 * 1024 * 1024;
// This script is fixed: neither HTTP input nor device filenames are interpolated into it.
const PHOTO_LIST = 'for f in /sdcard/DCIM/Camera/*; do [ -f "$f" ] && stat -c "%n|%s|%Y" "$f"; done; true';
const fault = (status, message) => Object.assign(new Error(message), { status });

export function runAdb(args, { adbPath = process.env.ANDROID_ADB_PATH || ADB_PATH, signal, timeoutMs = 15_000, maxBytes = 1024 * 1024 } = {}) {
  return new Promise((resolveResult, reject) => {
    if (signal?.aborted) return reject(signal.reason || fault(499, '拍照已取消。'));
    const child = spawn(adbPath, args, { stdio: ['ignore', 'pipe', 'pipe'], shell: false });
    const chunks = [];
    let length = 0, failure;
    const stop = error => { failure ??= error; child.kill('SIGKILL'); };
    const abort = () => stop(signal.reason || fault(499, '拍照已取消。'));
    const timer = setTimeout(() => stop(fault(504, 'ADB 通信超时，请检查 USB 连接。')), timeoutMs);
    timer.unref();
    signal?.addEventListener('abort', abort, { once: true });
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); };
    child.stdout.on('data', chunk => {
      length += chunk.length;
      if (length > maxBytes) stop(fault(413, '手机返回的数据过大，请使用不超过 25 MB 的照片。'));
      else chunks.push(chunk);
    });
    child.stderr.resume();
    child.on('error', () => { cleanup(); reject(fault(503, 'ADB 无法启动，请检查 ANDROID_ADB_PATH 或安装 Android platform-tools。')); });
    child.on('close', code => {
      cleanup();
      if (failure) return reject(failure);
      if (code !== 0) return reject(fault(503, '手机连接中断或 ADB 操作失败，请检查 USB 调试授权。'));
      resolveResult(Buffer.concat(chunks));
    });
  });
}

function parseDevices(output) {
  const devices = [], blocked = [];
  for (const line of String(output).split(/\r?\n/)) {
    const match = line.match(/^(\S+)\s+(device|unauthorized|offline)(?:\s|$)/);
    if (!match) continue;
    const [, serial, state] = match;
    if (state !== 'device') { blocked.push(state === 'unauthorized' ? '手机尚未授权，请在手机确认 USB 调试授权。' : '手机离线，请重新连接 USB。'); continue; }
    if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(serial)) continue;
    const model = line.match(/\bmodel:(\S+)/)?.[1];
    devices.push({ serial, name: (model || 'Android').replaceAll('_', ' ') });
  }
  if (!devices.length) blocked.push('未找到可用手机，请连接 USB、解锁手机并确认 USB 调试已授权。');
  return { devices, message: [...new Set(blocked)].join(' ') };
}

function parsePhotos(output) {
  const files = new Map();
  for (const line of String(output).split(/\r?\n/)) {
    const match = line.match(/^(\/sdcard\/DCIM\/Camera\/[A-Za-z0-9_.-]+\.(?:jpe?g|png))\|(\d+)\|(\d+)$/i);
    if (!match) continue;
    const [, path, bytes, modified] = match;
    const size = Number(bytes);
    if (Number.isSafeInteger(size)) files.set(path, { size, modified });
  }
  return files;
}

async function readPayload(request, controls = false) {
  let body = '';
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 1024) throw fault(413, '拍照请求过大。');
  }
  let value;
  try { value = JSON.parse(body); } catch { throw fault(422, '拍照参数无效。'); }
  if (!value || Array.isArray(value) || typeof value.serial !== 'string') throw fault(422, '请选择已连接的手机。');
  if (controls) {
    const keys = value.action === 'zoom' ? ['serial', 'action', 'zoom'] : ['serial', 'action'];
    if (!['preview', 'auto', 'zoom'].includes(value.action) || Object.keys(value).length !== keys.length || Object.keys(value).some(key => !keys.includes(key)) || (value.action === 'zoom' && (typeof value.zoom !== 'number' || !Number.isFinite(value.zoom) || value.zoom < .6 || value.zoom > 3))) throw fault(422, '相机操作或缩放倍率无效，倍率范围为 0.6–3×。');
  } else if (Object.keys(value).length !== 1) throw fault(422, '请选择已连接的手机。');
  return value;
}

export function createAndroidCameraHelper({ adbRunner = runAdb, captureTimeoutMs = 35_000, pollMs = 500, cameraReadyMs = 1200, maxImageBytes = MAX_IMAGE_BYTES, cameraControlsFactory = createNativeCameraControls, nativeCaptureFactory = captureNativeCamera } = {}) {
  const token = randomBytes(32).toString('hex');
  let enumerated = new Set(), busy = false, activeController;
  const server = createServer(async (request, response) => {
    const json = (status, body) => {
      if (response.destroyed) return;
      response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify(body));
    };
    response.setHeader('Cache-Control', 'no-store');
    const origin = request.headers.origin;
    if (![`127.0.0.1:${request.socket.localPort}`, `localhost:${request.socket.localPort}`].includes(request.headers.host) || !ORIGINS.has(origin)) return json(403, { detail: '请求来源未获授权。' });
    response.setHeader('Access-Control-Allow-Origin', origin);
    response.setHeader('Vary', 'Origin');
    if (request.method === 'OPTIONS') {
      const headers = String(request.headers['access-control-request-headers'] || '').toLowerCase().split(',').map(value => value.trim()).filter(Boolean);
      if (!['GET', 'POST'].includes(request.headers['access-control-request-method']) || headers.some(value => !['content-type', 'x-camera-client', 'x-camera-token'].includes(value))) return json(403, { detail: '预检请求未获授权。' });
      response.writeHead(204, { 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type, X-Camera-Client, X-Camera-Token', 'Access-Control-Allow-Private-Network': 'true' });
      return response.end();
    }
    if (request.headers['x-camera-client'] !== CLIENT) return json(403, { detail: '请从卡牌网页连接手机。' });
    const isDevices = request.method === 'GET' && request.url === '/v1/devices';
    const isCapture = request.method === 'POST' && request.url === '/v1/capture';
    const isCamera = request.method === 'POST' && request.url === '/v1/camera';
    if (!isDevices && !isCapture && !isCamera) return json(404, { detail: '接口不存在。' });
    if (isCapture || isCamera) {
      const supplied = Buffer.from(String(request.headers['x-camera-token'] || '')), expected = Buffer.from(token);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return json(403, { detail: '拍照连接已失效，请重新连接手机。' });
    }
    if (busy) return json(409, { detail: '手机正忙，请等待当前拍照完成。' });
    busy = true;
    const controller = new AbortController();
    activeController = controller;
    response.on('close', () => { if (!response.writableFinished) controller.abort(fault(499, '拍照已取消。')); });
    const timer = setTimeout(() => controller.abort(fault(504, isCamera ? '相机调整超时，请保持手机解锁、关闭弹窗后重试。' : '拍照超时：未收到本次新照片。请解锁手机、确认相机权限及照片保存为 JPEG/PNG 后重试。')), isDevices ? 20_000 : isCamera ? 85_000 : captureTimeoutMs);
    timer.unref();
    const adb = async (args, maxBytes) => {
      controller.signal.throwIfAborted();
      const result = await adbRunner(args, { signal: controller.signal, maxBytes });
      controller.signal.throwIfAborted();
      return result;
    };
    try {
      if (isDevices) {
        const result = parseDevices(await adb(['devices', '-l']));
        enumerated = new Set(result.devices.map(device => device.serial));
        return json(200, { ...result, token, camera_controls: true });
      }
      const { serial, action, zoom } = await readPayload(request, isCamera);
      if (!enumerated.has(serial)) throw fault(422, '手机未在已授权设备列表中，请重新连接并选择手机。');
      const connected = parseDevices(await adb(['devices', '-l']));
      if (!connected.devices.some(device => device.serial === serial)) { enumerated.delete(serial); throw fault(503, '所选手机已断开、离线或失去 USB 调试授权，请重新连接。'); }
      const onDevice = (args, maxBytes) => adb(['-s', serial, ...args], maxBytes);
      const checkUnlocked = async () => {
        const policy = String(await onDevice(['shell', 'dumpsys', 'window', 'policy']));
        if (/(?:mKeyguardShowing|mShowingLockscreen|isStatusBarKeyguard|mDreamingLockscreen)\s*=\s*true|(?:showing|isKeyguardShowing)\s*=\s*true/i.test(policy)) throw fault(409, '手机处于锁屏状态，请手动解锁后重试。');
        if (/screenState\s*=\s*SCREEN_STATE_OFF|mScreenOnFully\s*=\s*false/i.test(policy)) throw fault(409, '手机屏幕未点亮，请手动点亮并解锁手机后重试。');
        if (!/(?:mKeyguardShowing|mShowingLockscreen|isStatusBarKeyguard|mDreamingLockscreen|showing|isKeyguardShowing)\s*=\s*false/i.test(policy)) throw fault(409, '无法确认手机锁屏状态，请解锁手机后重试。');
      };
      await checkUnlocked();
      const cleanupDevice = args => adbRunner(['-s', serial, ...args], { timeoutMs: 2000 });
      if (isCamera) {
        const controls = cameraControlsFactory(onDevice, { signal: controller.signal, cleanupDevice, serial });
        return json(200, await controls(action, zoom));
      }
      const nativeInstalled = String(await onDevice(['shell', 'pm', 'list', 'packages', 'cn.cardsp.camera']));
      if (/^package:cn\.cardsp\.camera\s*$/m.test(nativeInstalled)) {
        const image = await nativeCaptureFactory(onDevice, { signal: controller.signal, cleanupDevice, serial });
        if (image.length > maxImageBytes) throw fault(413, '手机照片超过 25 MB。');
        response.writeHead(200, { 'Content-Type': 'image/jpeg', 'Content-Length': image.length });
        return response.end(image);
      }
      const launched = String(await onDevice(['shell', 'am', 'start', '-W', '-a', 'android.media.action.STILL_IMAGE_CAMERA']));
      if (/Error:|Exception|Status:\s*(?!ok\b)\S+/i.test(launched) || !/Status:\s*ok\b/i.test(launched)) throw fault(409, '手机相机无法启动，请在手机完成相机权限确认后重试。');
      await delay(cameraReadyMs, undefined, { signal: controller.signal });
      await checkUnlocked();
      const list = async () => parsePhotos(await onDevice(['shell', PHOTO_LIST]));
      const before = await list();
      await onDevice(['shell', 'input', 'keyevent', '27']); // Exactly one shutter. No retries.
      let previous = new Map();
      while (true) {
        await delay(pollMs, undefined, { signal: controller.signal });
        const current = await list();
        const candidates = [...current].filter(([path, data]) => !before.has(path) && data.size > 0);
        if (candidates.length > 1) throw fault(409, '检测到多张新照片，无法确认本次快门照片；请关闭连拍并重试。');
        for (const [path, data] of candidates) {
          if (data.size > maxImageBytes) throw fault(413, '本次照片超过 25 MB，请调低相机照片大小后重试。');
          const prior = previous.get(path);
          if (!prior || prior.size !== data.size || prior.modified !== data.modified) continue;
          // Safe path alphabet excludes remote shell metacharacters; no HTTP input is a command.
          const image = Buffer.from(await onDevice(['exec-out', 'cat', path], maxImageBytes));
          const after = (await list()).get(path);
          if (!after || after.size !== data.size || after.modified !== data.modified || image.length !== data.size) { previous = current; continue; }
          const jpeg = image.length >= 4 && image[0] === 0xff && image[1] === 0xd8 && image[2] === 0xff && image.subarray(-2).toString('hex') === 'ffd9';
          const png = image.subarray(0, 8).toString('hex') === '89504e470d0a1a0a';
          if (!jpeg && !png) throw fault(503, '本次新文件不是有效的 JPEG/PNG 照片，请检查相机保存格式。');
          if (response.destroyed) return;
          response.writeHead(200, { 'Content-Type': jpeg ? 'image/jpeg' : 'image/png', 'Content-Length': image.length });
          return response.end(image);
        }
        previous = current;
      }
    } catch (error) {
      const reason = controller.signal.aborted ? controller.signal.reason : error;
      json(reason?.status || 503, { detail: reason?.status ? reason.message : '手机相机操作失败，请检查 USB 连接、相机权限及手机解锁状态后重试。' });
    } finally {
      clearTimeout(timer);
      activeController = undefined;
      busy = false;
    }
  });
  const close = server.close.bind(server);
  server.close = callback => { activeController?.abort(fault(499, '手机助手已关闭，拍照已取消。')); return close(callback); };
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const server = createAndroidCameraHelper();
  server.requestTimeout = 90_000;
  server.listen(8767, '127.0.0.1', () => console.log('Android camera helper listening on 127.0.0.1:8767'));
  server.on('error', error => { console.error(`Android camera helper: ${error.code}`); process.exit(1); });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(1), 2000).unref();
  });
}
