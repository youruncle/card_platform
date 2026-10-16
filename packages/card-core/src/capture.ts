/** Shared capture payload/side semantics; camera and storage belong to each client. */
export type Side = "front" | "back";
export type Quad = [
  [number, number],
  [number, number],
  [number, number],
  [number, number],
];
export interface CropProposal {
  proposal_id: string;
  source_hash: string;
  source_url: string;
  preview_url: string;
  quad: Quad | null;
  confidence: number;
  width: number;
  height: number;
  target?: "card" | "slab";
  algorithm_version?: string;
  status?: string;
}
export interface CropRecipe {
  rotation?: number;
  quad?: Quad | null;
  source_hash?: string;
  confirmed?: boolean;
  target?: "card" | "slab";
  algorithm_version?: string;
}
export interface CaptureDraft {
  requestId: string;
  front?: Blob;
  back?: Blob;
  frontSource?: Blob;
  backSource?: Blob;
  recipes?: Partial<Record<Side, CropRecipe>>;
  singleSide?: boolean;
  targetItemId?: string;
  expectedRevision?: string;
  missingSide?: Side;
}
export interface ClientCapabilities {
  version: number;
  capture_jobs: boolean;
  single_side: boolean;
  supplement: boolean;
  crop_proposals: boolean;
  image_variants: boolean;
  portfolio_sort: boolean;
}
export const LEGACY_CAPABILITIES: ClientCapabilities = {
  version: 0,
  capture_jobs: false,
  single_side: false,
  supplement: false,
  crop_proposals: false,
  image_variants: false,
  portfolio_sort: false,
};
export function canSubmitCapture(
  d: Partial<CaptureDraft>,
  singleEnabled: boolean,
) {
  if (d.targetItemId)
    return (
      singleEnabled &&
      !!d.expectedRevision &&
      !!d.missingSide &&
      !!d[d.missingSide]
    );
  return (
    !!(d.front && d.back) ||
    (singleEnabled && !!d.singleSide && !!(d.front || d.back))
  );
}
export function captureForm(d: Partial<CaptureDraft>): FormData {
  if (!d.front && !d.back) throw new Error("请先选择至少一面照片");
  const f = new FormData();
  for (const side of ["front", "back"] as const) {
    if (d[side]) f.append(side, d[side]!, `${side}.jpg`);
    const source = d[side === "front" ? "frontSource" : "backSource"];
    if (d[side] && source)
      f.append(`${side}_source`, source, `${side}-source.jpg`);
  }
  if (d.targetItemId) {
    if (!d.expectedRevision) throw new Error("请重新打开卡片后补拍");
    f.append("target_item_id", d.targetItemId);
    f.append("expected_revision", d.expectedRevision);
  }
  if (d.recipes) f.append("crop_recipe", JSON.stringify(d.recipes));
  return f;
}
export function batchDrafts(
  photos: Blob[],
  mode: "pairs" | Side,
): CaptureDraft[] {
  if (mode === "pairs")
    return Array.from({ length: Math.ceil(photos.length / 2) }, (_, i) => ({
      requestId: crypto.randomUUID(),
      front: photos[i * 2],
      frontSource: photos[i * 2],
      back: photos[i * 2 + 1],
      backSource: photos[i * 2 + 1],
    }));
  return photos.map((photo) => ({
    requestId: crypto.randomUUID(),
    [mode]: photo,
    [mode === "front" ? "frontSource" : "backSource"]: photo,
    singleSide: true,
  }));
}
export function swapDraftSides(d: CaptureDraft): CaptureDraft {
  return {
    ...d,
    requestId: crypto.randomUUID(),
    front: d.back,
    back: d.front,
    frontSource: d.backSource,
    backSource: d.frontSource,
    recipes: d.recipes
      ? { front: d.recipes.back, back: d.recipes.front }
      : undefined,
  };
}
export function imageVariantPath(
  id: string,
  side: Side,
  url: string | null | undefined,
  variant: "thumb" | "preview" | "original",
): string | null {
  if (!url) return null;
  if (!url.startsWith("/uploads/") || url.includes("\\")) return url;
  const filename = url.split("?")[0].split("/").pop()!;
  return `/v1/portfolio/items/${encodeURIComponent(id)}/images/${side}?${new URLSearchParams({ variant, revision: decodeURIComponent(filename) })}`;
}
