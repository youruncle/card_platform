import { requestKnowledgeBrowser } from "./api";
export type * from "../../../packages/card-core/src/knowledge-browser";

export const knowledgeEntityLabels: Record<string, string> = {
  manufacturer: "制造商", brand: "厂牌", license: "授权", product: "产品 / 系列",
  insert: "卡组 / Insert", section: "卡组", parallel: "平行版本", attribute: "属性",
  player: "球员", card: "具体卡片", team: "球队", sport: "运动", league: "联盟",
};
export function browserPath(path: string, filters: Record<string, string | number | null | undefined> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  return `${path}${query.size ? `?${query}` : ""}`;
}
export const fetchKnowledge = <T>(path: string, signal: AbortSignal) => requestKnowledgeBrowser<T>(path, signal);
export function seasonLabel(start: number | null, end?: number | null): string {
  if (start === null) return "赛季未知";
  return end && end !== start ? `${start}–${String(end).slice(-2)}` : String(start);
}
const designTypes = new Set(["design", "official_design", "official_product_image", "official_product_design", "manufacturer_design", "design_reference", "official_design_sample", "product_design", "source_design", "source_design_reference"]);
const specimenTypes = new Set(["verified_card_scan", "verified_card", "verified_specimen", "card_scan", "reviewed_card_scan", "specimen"]);
export function mediaCategory(media: { image_type: string }): "design" | "specimen" | "unknown" {
  return designTypes.has(media.image_type) ? "design" : specimenTypes.has(media.image_type) ? "specimen" : "unknown";
}
export function mediaKind(media: { card_id: string | null; image_type: string }): string {
  const category = mediaCategory(media);
  if (category === "design") return "设计样张";
  if (category === "specimen") return media.image_type.startsWith("verified_") ? "已核实实卡图" : "实卡参考图";
  return "类型待核对";
}
export function sideLabel(side: string): string {
  return side === "front" ? "正面" : side === "back" ? "背面" : "侧别未知";
}
export function sourceLabel(authority: string | null): string {
  return ({ official: "官方来源", manufacturer: "制造商来源", third_party: "第三方来源",
    official_host: "官方站点", manufacturer_document_third_party_host: "经销商托管原厂资料",
    official_checklist: "官方清单", official_product: "官方产品资料",
  } as Record<string, string>)[authority ?? ""] ?? (authority ? "来源类型待核对" : "来源级别未知");
}
/** Shorten a known capture caption for display; the full evidence remains on the media record. */
export function mediaTitle(media: { caption: string | null; card_id: string | null; image_type: string }, index = 0): string {
  const capture = media.caption?.match(/^Manufacturer sell-sheet design example; page (\d+), embedded image (\d+)\./);
  if (capture && mediaCategory(media) === "design") return `设计示例 · 第 ${capture[1]} 页（图 ${Number(capture[2]) + 1}）`;
  return media.caption ?? `${mediaKind(media)} ${index + 1}`;
}
/** Only our own query strings are valid return targets. */
export function returnQuery(value: string | null, fallback: URLSearchParams, remove: string[]): URLSearchParams {
  if (value?.startsWith("?") && !value.startsWith("?//")) return new URLSearchParams(value.slice(1));
  const next = new URLSearchParams(fallback);
  remove.forEach(key => next.delete(key));
  return next;
}
