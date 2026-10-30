const HELPER_URL = 'http://127.0.0.1:8767';
const CLIENT_HEADERS = { 'X-Camera-Client': 'cardsp-web-v1' };
export type CameraDevice = { serial: string; name: string };
export type CameraConnection = { devices: CameraDevice[]; token: string };
async function helperRequest(path: string, init: RequestInit, signal?: AbortSignal) {
  let response: Response;
  try {
    response = await fetch(`${HELPER_URL}${path}`, { ...init, credentials: 'omit', redirect: 'error', headers: { ...CLIENT_HEADERS, ...init.headers }, signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(95_000)]) : AbortSignal.timeout(95_000) });
  } catch { throw new Error('无法连接手机拍照助手。请启动 scripts/android-camera-helper.mjs，允许浏览器访问本机网络，并确认 USB 调试已授权。'); }
  if (!response.ok) { if (response.status === 404 && path === '/v1/camera') throw new Error('请更新并重启本机手机助手，以使用取景和缩放功能。'); const body = await response.json().catch(() => ({})); throw new Error(body.detail || body.error || `手机拍照失败（${response.status}）`); }
  return response;
}
export async function connectLocalCamera(signal?: AbortSignal): Promise<CameraConnection> {
  const result = await (await helperRequest('/v1/devices', {}, signal)).json();
  if (!Array.isArray(result.devices) || !result.devices.every((device: CameraDevice) => typeof device.serial === 'string' && typeof device.name === 'string') || typeof result.token !== 'string' || !result.token) throw new Error('拍照助手响应无效，请更新助手');
  if (!result.devices.length) throw new Error('没有已授权的手机。请解锁 Redmi，连接 USB 并允许 USB 调试。');
  return result;
}
export async function captureLocalCamera(connection: CameraConnection, serial: string, signal?: AbortSignal): Promise<File> {
  if (!connection.devices.some(device => device.serial === serial)) throw new Error('请选择已连接的手机');
  const response = await helperRequest('/v1/capture', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Camera-Token': connection.token }, body: JSON.stringify({ serial }) }, signal);
  const type = response.headers.get('content-type')?.split(';')[0];
  if (type !== 'image/jpeg' && type !== 'image/png') throw new Error('手机未返回有效照片');
  const blob = await response.blob(); if (!blob.size || blob.size > 30 * 1024 * 1024) throw new Error('手机照片为空或超过 30 MB');
  return new File([blob], `redmi-${Date.now()}.${type === 'image/png' ? 'png' : 'jpg'}`, { type });
}
export type CameraRectangle = { x: number; y: number; width: number; height: number; confidence: number };
export type CameraFrame = { width: number; height: number; zoom: number; min_zoom?: number; max_zoom?: number; presets: number[]; preview: string; rectangles: CameraRectangle[]; card?: CameraRectangle | null; message: string; adjusted?: boolean };
export type CameraControlAction = 'preview' | 'zoom' | 'auto';
export async function controlLocalCamera(connection: CameraConnection, serial: string, action: CameraControlAction, zoom?: number, signal?: AbortSignal): Promise<CameraFrame> {
  if (!connection.devices.some(device => device.serial === serial)) throw new Error('请选择已连接的手机');
  if (!['preview', 'zoom', 'auto'].includes(action) || (action === 'zoom' && (typeof zoom !== 'number' || !Number.isFinite(zoom) || zoom < .6 || zoom > 3))) throw new Error('缩放倍率应在 0.6–3× 之间');
  const result = await (await helperRequest('/v1/camera', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Camera-Token': connection.token }, body: JSON.stringify({ serial, action, ...(action === 'zoom' ? { zoom } : {}) }) }, signal)).json();
  if (!Number.isInteger(result.width) || !Number.isInteger(result.height) || result.width < 16 || result.height < 16 || result.width > 10000 || result.height > 10000 || !Number.isFinite(result.zoom) || result.zoom < .1 || result.zoom > 100 || !Array.isArray(result.presets) || !result.presets.every((n: number) => Number.isFinite(n) && n >= .6 && n <= 3) || typeof result.preview !== 'string' || result.preview.length > 3 * 1024 * 1024 || !/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(result.preview) || !Array.isArray(result.rectangles) || typeof result.message !== 'string') throw new Error('相机预览或实际倍率响应无效，请更新助手后重试');
  if ((result.min_zoom !== undefined && (!Number.isFinite(result.min_zoom) || result.min_zoom < .6 || result.min_zoom > 3)) || (result.max_zoom !== undefined && (!Number.isFinite(result.max_zoom) || result.max_zoom > 3 || result.max_zoom < (result.min_zoom ?? .6)))) throw new Error('相机倍率范围响应无效');
  const validRect = (r: CameraRectangle) => r && [r.x,r.y,r.width,r.height,r.confidence].every(Number.isFinite) && r.x >= 0 && r.y >= 0 && r.width > 0 && r.height > 0 && r.x + r.width <= 1.001 && r.y + r.height <= 1.001;
  return { ...result, rectangles: result.rectangles.filter(validRect), card: validRect(result.card) ? result.card : null };
}
