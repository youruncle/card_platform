#!/usr/bin/env node
// Loopback-only hardware transport. It never receives cloud cookies or stores cards.
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ORIGINS = new Set(['https://cardsp.cn', 'https://www.cardsp.cn', 'http://localhost:3000', 'http://127.0.0.1:3000']);
const CLIENT = 'cardsp-web-v1';
const fault = (status, message) => Object.assign(new Error(message), { status });

function runBridge(script, args, { timeoutMs, signal }) {
  return new Promise((resolveResult, reject) => {
    const child = spawn(script, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', timedOut = false, cancelled = false, killTimer;
    const interrupt = () => {
      child.kill('SIGINT');
      killTimer ??= setTimeout(() => child.kill('SIGKILL'), 5000);
      killTimer.unref();
    };
    const abort = () => { cancelled = true; interrupt(); };
    const timer = setTimeout(() => { timedOut = true; interrupt(); }, timeoutMs);
    timer.unref();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    const cleanup = () => { clearTimeout(timer); clearTimeout(killTimer); signal?.removeEventListener('abort', abort); };
    child.stdout.on('data', data => {
      stdout += data;
      if (stdout.length > 512_000) { cancelled = true; interrupt(); }
    });
    child.stderr.resume();
    child.on('error', () => { cleanup(); reject(fault(503, '本机扫描程序无法启动，请检查 Canon 扫描助手。')); });
    child.on('close', code => {
      cleanup();
      if (timedOut || code === 124) return reject(fault(504, '扫描超时，已释放设备，请重试。'));
      if (cancelled) return reject(fault(499, '扫描已取消。'));
      if (code === 75) return reject(fault(409, '扫描仪正忙，请关闭其他扫描程序后重试。'));
      if (code === 69) return reject(fault(503, '未找到 Canon 扫描仪，请检查 USB 连接。'));
      if (code !== 0) return reject(fault(503, 'Canon 扫描失败，请检查设备连接后重试。'));
      try { resolveResult(stdout.split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line))); }
      catch { reject(fault(502, '扫描程序返回无效结果，请重连扫描仪。')); }
    });
  });
}

async function readPayload(request) {
  let body = '';
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 1024) throw fault(413, '扫描请求过大。');
  }
  let value;
  try { value = JSON.parse(body); } catch { throw fault(422, '扫描参数无效。'); }
  if (!value || Array.isArray(value) || ![300, 600].includes(value.dpi)
    || typeof value.preview !== 'boolean' || Object.keys(value).some(key => !['dpi', 'preview'].includes(key))) {
    throw fault(422, '扫描仅支持 300/600 DPI 和概览模式。');
  }
  return value;
}

export function createScannerHelper({ bridgeScript = join(ROOT, 'scripts/canon_lide400_bridge.swift'), scanTimeoutMs } = {}) {
  const token = randomBytes(32).toString('hex');
  let busy = false;
  return createServer(async (request, response) => {
    const origin = request.headers.origin;
    const port = request.socket.localPort;
    const allowedHost = [`127.0.0.1:${port}`, `localhost:${port}`].includes(request.headers.host);
    const json = (status, detail) => {
      if (response.destroyed) return;
      response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify(detail));
    };
    response.setHeader('Cache-Control', 'no-store');
    if (!allowedHost || !ORIGINS.has(origin)) return json(403, { detail: '请求来源未获授权。' });
    response.setHeader('Access-Control-Allow-Origin', origin);
    response.setHeader('Vary', 'Origin');
    response.setHeader('Access-Control-Expose-Headers', 'X-Scan-Resolution');
    if (request.method === 'OPTIONS') {
      const method = request.headers['access-control-request-method'];
      const headers = String(request.headers['access-control-request-headers'] || '').toLowerCase().split(',').map(v => v.trim()).filter(Boolean);
      if (!['GET', 'POST'].includes(method) || headers.some(v => !['content-type', 'x-scanner-client', 'x-scanner-token'].includes(v))) {
        return json(403, { detail: '预检请求未获授权。' });
      }
      response.writeHead(204, {
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, X-Scanner-Client, X-Scanner-Token',
        'Access-Control-Allow-Private-Network': 'true',
      });
      return response.end();
    }
    if (request.headers['x-scanner-client'] !== CLIENT) return json(403, { detail: '请从卡牌网页连接扫描仪。' });
    const isDevice = request.url === '/v1/device' && request.method === 'GET';
    const isScan = request.url === '/v1/scan' && request.method === 'POST';
    if (!isDevice && !isScan) return json(404, { detail: '接口不存在。' });
    if (isScan) {
      const supplied = Buffer.from(String(request.headers['x-scanner-token'] || ''));
      const expected = Buffer.from(token);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return json(403, { detail: '扫描连接已失效，请重新连接扫描仪。' });
    }
    if (busy) return json(409, { detail: '扫描仪正忙，请等待当前扫描完成。' });
    busy = true;
    const controller = new AbortController();
    response.on('close', () => { if (!response.writableFinished) controller.abort(); });
    let directory;
    try {
      if (isDevice) {
        const events = await runBridge(bridgeScript, ['capabilities', '--timeout', '15'], { timeoutMs: 20_000, signal: controller.signal });
        const capabilities = events.findLast(event => event.event === 'capabilities');
        if (!capabilities) throw fault(502, '未获得扫描仪能力，请重新连接。');
        return json(200, { name: 'Canon CanoScan LiDE 400', serial: '4706E5', capabilities, token });
      }
      const { dpi, preview } = await readPayload(request);
      directory = await mkdtemp(join(tmpdir(), 'card-platform-canon-'));
      const output = join(directory, 'scan.png');
      const timeout = preview ? 90 : 240;
      const events = await runBridge(bridgeScript, [preview ? 'preview' : 'scan', '--dpi', String(dpi), '--timeout', String(timeout), '--output', output], { timeoutMs: scanTimeoutMs ?? (timeout + 5) * 1000, signal: controller.signal });
      const completed = events.findLast(event => event.event === 'scan_complete');
      const path = resolve(completed?.path || output);
      const within = relative(directory, path);
      if (within.startsWith('..') || isAbsolute(within)) throw fault(502, '扫描程序返回无效文件路径。');
      const image = await readFile(path);
      if (image.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw fault(502, '扫描程序未生成 PNG 图片。');
      await rm(directory, { recursive: true, force: true });
      directory = undefined;
      if (response.destroyed) return;
      response.writeHead(200, {
        'Content-Type': 'image/png', 'Content-Length': image.length,
        'X-Scan-Resolution': JSON.stringify(completed?.source_resolution || {}),
      });
      response.end(image);
    } catch (error) {
      if (directory) await rm(directory, { recursive: true, force: true }).catch(() => {});
      directory = undefined;
      json(error.status || 503, { detail: error.status ? error.message : '本机扫描失败，请重新连接扫描仪。' });
    } finally {
      if (directory) await rm(directory, { recursive: true, force: true }).catch(() => {});
      busy = false;
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const server = createScannerHelper();
  server.requestTimeout = 260_000;
  server.listen(8766, '127.0.0.1', () => console.log('Canon scanner helper listening on 127.0.0.1:8766'));
  server.on('error', error => { console.error(`Canon helper: ${error.code}`); process.exit(1); });
}
