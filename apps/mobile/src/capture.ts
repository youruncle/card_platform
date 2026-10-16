import { guidedCamera } from "./nativeCamera";
import { Camera, CameraResultType, CameraSource } from "@capacitor/camera";
export async function takePhoto(source: CameraSource): Promise<Blob> {
  if (source === CameraSource.Camera) {
    const guided = await guidedCamera();
    if (guided) return normalizePhoto(guided);
  }
  const photo = await Camera.getPhoto({
    quality: 100,
    resultType: CameraResultType.Uri,
    source,
    correctOrientation: true,
    saveToGallery: false,
  });
  if (!photo.webPath) throw new Error("未获取到照片，请重新选择");
  return normalizePhoto(await (await fetch(photo.webPath)).blob());
}
export interface Crop {
  x: number;
  y: number;
  width: number;
  height: number;
}
export function photoGeometry(
  w: number,
  h: number,
  rotation = 0,
  crop: Crop = { x: 0, y: 0, width: 1, height: 1 },
) {
  if (
    ![w, h, rotation, ...Object.values(crop)].every(Number.isFinite) ||
    w <= 0 ||
    h <= 0 ||
    crop.x < 0 ||
    crop.y < 0 ||
    crop.width <= 0 ||
    crop.height <= 0 ||
    crop.x + crop.width > 1.000001 ||
    crop.y + crop.height > 1.000001
  )
    throw new Error("裁切范围无效");
  const sw = w * crop.width,
    sh = h * crop.height;
  const scale = Math.min(1, 2560 / Math.max(sw, sh));
  const dw = Math.round(sw * scale),
    dh = Math.round(sh * scale);
  return {
    sx: w * crop.x,
    sy: h * crop.y,
    sw,
    sh,
    dw,
    dh,
    width: rotation % 180 ? dh : dw,
    height: rotation % 180 ? dw : dh,
  };
}
export async function pickPhotos(): Promise<Blob[]> {
  const selection = await Camera.pickImages({
    quality: 100,
    limit: 20,
    correctOrientation: true,
  });
  if (selection.photos.length > 20) throw new Error("每次最多选择 20 张照片");
  const photos: Blob[] = [];
  // Process serially to avoid decoding many full-resolution images at once.
  for (const photo of selection.photos) {
    photos.push(
      await normalizePhoto(await (await fetch(photo.webPath)).blob()),
    );
  }
  return photos;
}
export async function normalizePhoto(
  blob: Blob,
  rotation = 0,
  crop?: Crop,
): Promise<Blob> {
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    img.src = url;
    try {
      await img.decode();
    } catch {
      throw new Error(
        "无法读取此照片，请尝试从相册重新选择或转换为 JPEG / PNG",
      );
    }
    if (!img.naturalWidth || !img.naturalHeight)
      throw new Error("无法读取照片");
    // Only the selected upload copy is processed; the photo library stays unchanged.
    if (img.naturalWidth * img.naturalHeight > 60_000_000)
      throw new Error("照片尺寸过大，请选择较小的照片");
    const canvas = document.createElement("canvas");
    const geometry = photoGeometry(
      img.naturalWidth,
      img.naturalHeight,
      rotation,
      crop,
    );
    canvas.width = geometry.width;
    canvas.height = geometry.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("无法处理照片");
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate((rotation * Math.PI) / 180);
    const { sx, sy, sw, sh, dw, dh } = geometry;
    ctx.drawImage(img, sx, sy, sw, sh, -dw / 2, -dh / 2, dw, dh);
    const output = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error("照片转换失败"))),
        "image/jpeg",
        0.94,
      ),
    );
    if (output.size > 10 * 1024 * 1024)
      throw new Error("照片超过 10 MiB，请选择较小的照片");
    return output;
  } catch (error) {
    throw error instanceof Error
      ? error
      : new Error("无法处理照片，请选择 JPEG 或 PNG");
  } finally {
    URL.revokeObjectURL(url);
  }
}
