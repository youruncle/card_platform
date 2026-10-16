import type { MobileApi } from "./api";
import type { CropRecipe } from "../../../packages/card-core/src/capture";
import { normalizePhoto } from "./capture";
/** Rebuild all edits from the immutable source, never from a previously encoded edit. */
export async function renderPhotoEdit(
  api: MobileApi,
  source: Blob,
  recipe: CropRecipe,
  serverCrop = true,
): Promise<Blob> {
  let image = source;
  if (recipe.quad && !serverCrop) {
    const [tl, tr, br, bl] = recipe.quad;
    if (
      tl[1] !== tr[1] ||
      tr[0] !== br[0] ||
      br[1] !== bl[1] ||
      bl[0] !== tl[0]
    )
      throw new Error("当前服务器不支持透视裁切，请保留原图或升级服务器");
    return normalizePhoto(source, recipe.rotation ?? 0, {
      x: tl[0],
      y: tl[1],
      width: tr[0] - tl[0],
      height: bl[1] - tl[1],
    });
  }
  if (recipe.quad) {
    const form = new FormData();
    form.append("image", source, "source.jpg");
    const p = await api.request<{ proposal_id: string }>(
      "/v1/capture/crop-proposals",
      { method: "POST", body: form },
    );
    const applied = await api.post<{ preview_url: string }>(
      `/v1/capture/crop-proposals/${p.proposal_id}/apply`,
      { quad: recipe.quad },
    );
    image = await api.image(applied.preview_url);
  }
  return recipe.rotation ? normalizePhoto(image, recipe.rotation) : image;
}
