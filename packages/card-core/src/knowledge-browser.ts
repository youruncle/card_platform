/** Read-only, published knowledge catalogue shared by web and mobile. */
export type KnowledgePage<T> = { items: T[]; total: number; offset: number; limit: number; version: number | null };
export type KnowledgeSource = { url: string | null; locator: string | null; authority: string | null };
export type KnowledgeMedia = {
  id: string; product_id: string | null; card_id: string | null;
  product_name?: string | null; manufacturer?: string | null;
  season_start?: number | null; season_end?: number | null;
  image_url: string; thumbnail_url: string; side: "front" | "back" | "unknown";
  image_type: string; source_url: string | null; source_authority: string | null;
  caption: string | null; player: string | null; card_number: string | null;
  parallel: string | null; pair_id: string | null;
};
export type KnowledgeProduct = {
  id: string; name: string; manufacturer: string | null; brand: string | null;
  season_start: number | null; season_end: number | null; sport: string | null;
  league: string | null; aliases: string[]; description: string | null;
  card_count: number; image_count: number; paired_card_count: number;
  text_status: "available" | "partial" | "missing";
  source_url: string | null; source_authority: string | null;
  cover_image_url: string | null;
};
export type KnowledgeCard = {
  id: string; name: string; product_id: string | null; product_name: string | null;
  manufacturer: string | null; season_start: number | null; season_end: number | null;
  player: string | null; card_number: string | null; section: string | null;
  parallel: string | null; serial_total: number | string | null;
  is_rookie: boolean | null; is_autograph: boolean | null; is_patch: boolean | null;
  image_count: number; front_image_url: string | null; back_image_url: string | null;
  sources: KnowledgeSource[];
};
export type KnowledgeBrowserSummary = {
  version: number | null; version_id: string | null; discovery_complete: false;
  summary: { manufacturers: number; products: number; card_facts: number; images: number; paired_cards: number };
  manufacturers: { name: string; product_count: number; card_count: number; image_count: number }[];
  seasons: number[]; entity_types: { type: string; count: number }[];
  issues: { source_key: string; message: string }[];
};
export type KnowledgeProductDetail = {
  product: KnowledgeProduct; sections: { name: string; card_count: number }[];
  parallels: { name: string; card_count: number }[]; version: number | null;
};
export type KnowledgeCardDetail = {
  card: KnowledgeCard; images: KnowledgeMedia[]; related_versions: KnowledgeCard[];
  version: number | null;
};
export type KnowledgeGlossaryEntry = {
  id: string; entity_type: string; canonical_name: string; aliases: string[];
  description: string | null; source_url: string | null;
};
