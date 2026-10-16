import {uploadItemImage} from './api';

type ImageInput = {side: 'front' | 'back'; file: File};

// Wait for every write before reporting success/failure or consuming a scanner job.
export async function uploadItemImages(
  itemId: string, ownerId: string, images: ImageInput[],
  upload = uploadItemImage,
): Promise<void> {
  const results = await Promise.allSettled(images.map(image => upload(itemId, ownerId, image.side, image.file)));
  const failures = results.flatMap((result, index) => result.status === 'rejected'
    ? [`${images[index].side === 'front' ? '正面' : '背面'}上传失败：${result.reason instanceof Error ? result.reason.message : String(result.reason)}`]
    : []);
  if (failures.length) throw new Error(failures.join('；'));
}
