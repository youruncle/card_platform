export type PortfolioSummary = {
  owner_id: string;
  base_currency: string;
  item_count: number;
  valued_item_count: number;
  unvalued_item_count: number;
  total_cost: string;
  total_market_value: string;
  unrealized_profit: string;
  realized_profit: string;
};

export type AuthUser = {
  id: string;
  email: string;
  display_name: string;
  base_currency: string;
  role: "admin" | "member";
};

export type AuthSession = {
  id: string;
  current: boolean;
  user_agent: string | null;
  ip_address: string | null;
  last_seen_at: string;
  expires_at: string;
  created_at: string;
};

export type ScannerRecognitionJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  front_image_url: string;
  back_image_url: string;
  front_crop_confidence: string;
  back_crop_confidence: string;
  front_transformed: boolean;
  back_transformed: boolean;
  front_rotation_degrees: 0 | 90 | 180 | 270;
  back_rotation_degrees: 0 | 90 | 180 | 270;
  front_orientation_confidence: string;
  back_orientation_confidence: string;
  front_orientation_method: "pending" | "apple_vision" | "vision_model" | "manual" | "unresolved";
  back_orientation_method: "pending" | "apple_vision" | "vision_model" | "manual" | "unresolved";
  result: CardRecognition | null;
  error_message: string | null;
  attempt_count: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ScannerCameraProperty = {
  minimum: number;
  maximum: number;
  step: number;
  default: number;
  current: number;
  writable: boolean;
};

export type ScannerCameraControls = {
  device_name: string;
  exposure_mode: "auto" | "manual";
  exposure_time: ScannerCameraProperty;
  gain: ScannerCameraProperty | null;
};

export type RecognitionBenchmarkMetrics = {
  sample_count: number;
  labeled_count: number;
  scored_count: number;
  top1_correct: number;
  top3_correct: number;
  top1_accuracy: number | null;
  top3_recall: number | null;
  insufficient_image_count: number;
  source_error_count: number;
};

export type RecognitionBenchmark = {
  id: string;
  name: string;
  source_code: string;
  status: string;
  sampling_config: Record<string, unknown>;
  target_top1: string;
  target_top3: string;
  metrics: RecognitionBenchmarkMetrics;
  sample_count: number;
  labeled_count: number;
  created_at: string;
};

export type MarketIdentityConflict = {
  source_code: string;
  external_id: string;
  listing_title: string | null;
  source_url: string | null;
  occurred_at: string;
  sale_price: string;
  shipping_price: string;
  currency: string;
  bindings: Array<{
    market_comp_id: string;
    card_definition_id: string;
    identity: Record<string, unknown>;
    comparable_tier: string;
    match_confidence: string | null;
    source_reliability: string | null;
  }>;
};

export type RecognitionBenchmarkSample = {
  id: string;
  batch_id: string;
  source_item_id: string;
  image_urls: string[];
  prediction: null | {
    candidates: Array<{
      rank: number;
      title: string;
      identity?: Record<string, unknown>;
      confidence?: string;
      match_confidence?: string;
      matching_fields?: string[];
      conflicts?: string[];
      source_urls?: string[];
      image_url?: string | null;
    }>;
    field_evidence: Record<string, string[]>;
    caveats: string[];
  };
  prediction_model: string | null;
  prediction_pipeline_version: string | null;
  source_audit?: null | {
    status: "review" | "no_explicit_conflict";
    conflicts: Array<{
      field: string;
      prediction: unknown;
      source_hint: unknown;
      reason: string;
    }>;
    disclaimer: string;
  };
  prediction_error: string | null;
  label_status: string;
  label_result: string | null;
  corrected_identity: Record<string, unknown> | null;
  label_notes: string | null;
  label_pipeline_version: string | null;
  assisted_label_result: string | null;
  assisted_identity: Record<string, unknown> | null;
  assisted_confidence: string | null;
  assisted_evidence: string[];
  assisted_labeler: string | null;
  assisted_review_status: "pending" | "accepted" | "overridden" | null;
  assisted_at: string | null;
  source_url: string | null;
  final_price: string | null;
  currency: string | null;
  revealed_reference: null | {
    title?: string;
    property_json?: string;
    solr_title?: string;
  };
};

export type RecognitionPredictionJob = {
  id: string;
  batch_id: string;
  status: "queued" | "running" | "succeeded" | "partial" | "failed";
  requested_count: number;
  predicted_count: number;
  failed_count: number;
  error_message: string | null;
};

export type RecognitionFeedbackReport = {
  label_counts: Record<string, number>;
  corrected_sample_count: number;
  knowledge_queue_count: number;
  field_errors: Array<{
    field: string;
    errors: number;
    corrections: number;
    error_rate: number | null;
    accuracy: number | null;
  }>;
};

export type RecognitionVersionMetrics = {
  minimum_acceptance_samples: number;
  target_top1: number;
  target_top3: number;
  ground_truth_count: number;
  scorable_ground_truth_count: number;
  incomplete_ground_truth_count: number;
  unresolved_labeled_count: number;
  versions: Array<{
    pipeline_version: string;
    knowledge_version: string | null;
    knowledge_version_number: number | null;
    model: string | null;
    history_sample_count: number;
    sample_count: number;
    prediction_error_count: number;
    candidate_count_distribution: Record<string, number>;
    multi_candidate_rate: number | null;
    three_candidate_rate: number | null;
    top1_correct: number;
    top3_correct: number;
    top1_accuracy: number | null;
    top3_recall: number | null;
    acceptance_evaluable: boolean;
    acceptance_status: "pending" | "passed" | "failed";
    field_metrics: Array<{
      field: string;
      correct: number;
      total: number;
      accuracy: number;
    }>;
  }>;
};

export type RecognitionSamplingAudit = {
  sample_count: number;
  unique_item_count: number;
  unique_title_count: number;
  unique_seller_count: number;
  min_final_price: string;
  max_final_price: string;
  at_or_below_20_count: number;
  non_single_card_count: number;
  max_samples_per_seller: number;
  sampling_config: Record<string, unknown>;
  conforms: boolean;
};

export type PortfolioItem = {
  id: string;
  player_id: string;
  player_name: string;
  brand_name: string;
  season_start: number;
  season_end: number | null;
  product_name: string;
  set_name: string | null;
  card_number: string | null;
  insert_name: string | null;
  parallel_name: string | null;
  is_rookie: boolean;
  is_autograph: boolean;
  autograph_type: "on_card" | "sticker" | "cut" | "unknown" | "none";
  has_inscription: boolean;
  inscription_text: string | null;
  is_memorabilia: boolean;
  memorabilia_types: string[];
  memorabilia_usage:
    | "game_used"
    | "player_worn"
    | "event_worn"
    | "not_from_specific_game_or_event"
    | "manufactured"
    | "unknown"
    | "none";
  memorabilia_player: string | null;
  is_patch: boolean;
  serial_number: number | null;
  serial_total: number | null;
  grade_company: string | null;
  grade_value: string | null;
  image_front_url: string | null;
  image_back_url: string | null;
  strategy_tags: string[];
  quantity_held: string;
  remaining_cost: string;
  realized_profit: string;
  valuation_currency: string | null;
  valuation_low: string | null;
  fair_value: string | null;
  valuation_high: string | null;
  shadow_valuation_currency: string | null;
  shadow_valuation_low: string | null;
  shadow_fair_value: string | null;
  shadow_valuation_high: string | null;
  shadow_valuation_confidence: string | null;
  shadow_valuation_status: string | null;
  shadow_valuation_model: string | null;
  market_comp_count: number;
  unrealized_profit: string | null;
  valuation_status: string | null;
  valuation_result_summary: Record<string, unknown>;
  valuation_job_id: string | null;
  valuation_error: string | null;
  deleted_at: string | null;
  deleted_reason: string | null;
};

export type MarketComp = {
  id: string;
  occurred_at: string;
  sale_price: string;
  shipping_price: string;
  currency: string;
  fx_rate_to_base: string | null;
  grade_company: string | null;
  grade_value: string | null;
  listing_title: string | null;
  source_url: string | null;
  match_confidence: string | null;
  source_reliability: string | null;
  classification: {
    source_code?: string;
    origin_source_code?: string;
    ingestion?: string;
  } | null;
};

export type CardHobbyImportCapture = {
  external_order_id: string | null;
  cardhobby_item_id: string;
  item_url: string;
  title: string;
  image_urls: string[];
  purchased_at: string | null;
  sale_price: string | null;
  shipping_price: string;
  currency: string;
  raw_text: string | null;
};

export type CardHobbyImportDraft = {
  id: string;
  duplicate: boolean;
  status: string;
  capture: CardHobbyImportCapture;
  proposed_item: null | {
    card: {
      player_name: string;
      brand_name: string;
      season_start: number;
      season_end: number | null;
      product_name: string;
      card_number: string | null;
      insert_name: string | null;
      parallel_name: string | null;
    };
    serial_total: number | null;
  };
  warnings: string[];
};

export type CardHobbyImportPreview = {
  batch_id: string;
  drafts: CardHobbyImportDraft[];
};

export type AutomatedValuationDetail = {
  job_id: string | null;
  status: string | null;
  error_message: string | null;
  result_summary: Record<string, unknown>;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  valuation: null | {
    id: string;
    valued_at: string;
    currency: string;
    low_value: string;
    fair_value: string;
    high_value: string;
    confidence: string | null;
    evidence: {
      strategy?: string;
      formula?: string;
      search_queries?: string[];
      notes?: string[];
      card_price?: { low: string; fair: string; high: string };
      landed_price?: { low: string; fair: string; high: string };
      player_adjustments?: Array<{
        target_player: string;
        reference_player: string;
        segment: string;
        coefficient: string;
        sample_count: number;
        ratio_low: string;
        ratio_high: string;
        pairs: Array<{
          target_title: string;
          target_url: string;
          target_price_base: string;
          reference_title: string;
          reference_url: string;
          reference_price_base: string;
        }>;
      }>;
      comparables?: Array<{
        id: string;
        title: string | null;
        source_url: string | null;
        occurred_at: string;
        original_price?: string;
        sale_price?: string;
        shipping_price?: string;
        landed_price?: string;
        currency: string;
        fx_rate_to_base: string;
        normalized_price?: string;
        normalized_card_price?: string;
        normalized_landed_price?: string;
        match_confidence?: string;
        age_days?: number;
        tier?: "T0" | "T1" | "T2" | "T3" | "rejected";
        similarity?: string;
        source_reliability?: string;
        raw_weight?: string;
        final_weight?: string;
        used_for_center?: boolean;
        hard_conflicts?: string[];
        field_matches?: Record<string, boolean | null>;
        adjustment_factor?: string;
        adjustment_basis?: string;
        evidence_type?: string;
        source?: string;
        evidence_level?: "confirmed_transaction" | "ended_auction" | null;
        adjustment?: string;
        condition_type?: string;
        grade_company?: string | null;
        grade_value?: string | null;
        player_name?: string;
        player_coefficient?: string;
      }>;
    };
    model_version: string | null;
  };
  shadow_valuation: null | {
    id: string;
    created_at: string;
    status: "estimated" | "manual_review" | "insufficient";
    currency: string;
    low_value: string | null;
    fair_value: string | null;
    high_value: string | null;
    confidence: string;
    evidence_count: number;
    review_reasons: string[];
    evidence: {
      used_ids?: string[];
      rejected?: Array<{ id: string; reasons: string[] }>;
      published_value_unchanged?: boolean;
    };
    model_version: string;
  };
  comparables: MarketComp[];
};

export type IdentityCorrection = {
  id: string;
  field_name:
    | "card_number"
    | "insert_name"
    | "parallel_name"
    | "serial_total"
    | "is_autograph"
    | "is_patch";
  current_value: string | number | boolean | null;
  suggested_value: string | number | boolean;
  confidence: string;
  evidence: Array<{
    title: string;
    source_url: string;
    market_comp_id: string;
    source_reliability: string;
    match_confidence: string;
  }>;
  detector_version: string;
  status: string;
  detected_at: string;
};

export type ConfirmedCardIdentity = {
  player_name: string; season_start: number; season_end: number | null;
  brand_name: string; product_name: string; set_name: string | null;
  card_number: string | null; insert_name: string | null; parallel_name: string | null;
  serial_number: number | null; serial_total: number | null;
  is_rookie: boolean; is_autograph: boolean; autograph_type: string;
  is_memorabilia: boolean; memorabilia_types: string[];
  grade_company: string | null; grade_value: number | null;
};

export type IdentityReviewDetail = {
  item_id: string;
  images: { front: string | null; back: string | null };
  current_identity: ConfirmedCardIdentity;
  suggested_identity: ConfirmedCardIdentity;
  latest_version: null | { id: string; version: number; review_status: "pending" | "confirmed" | "rejected" };
  field_provenance: Record<string, unknown>;
  conflicts: string[];
  review_reasons: string[];
  market_evidence: Array<{
    id: string; provider_code: string; external_sale_id: string; raw_title: string;
    sale_price: string; shipping_price: string; currency: string; occurred_at: string;
    source_url: string; evidence_level: string; decision: "accepted" | "rejected";
    reason_codes: string[];
  }>;
  research: null | { id: string; status: string; trigger_reasons: string[]; source_version: string };
  candidates: Array<{
    id: string; rank: number; identity: ConfirmedCardIdentity; confidence: string;
    field_provenance: Record<string, unknown>; visual_reasons: string[];
    checklist_evidence: Array<{ source_url: string; source_version: string; parallel_name: string }>;
    market_evidence: Array<{ id: string; sale_price: string; currency: string; price_base: string; occurred_at: string; reason_codes: string[] }>;
    validation_reasons: string[]; is_valid: boolean;
  }>;
  latest_shadow: null | {
    id: string; identity_version_id: string; status: string; currency: string;
    low_value: string | null; fair_value: string | null; high_value: string | null;
    confidence: string; evidence_count: number; review_reasons: string[]; created_at: string;
  };
};

export type CardRecognition = {
  player_name: string | null;
  season_start: number | null;
  season_end: number | null;
  brand_name: string | null;
  product_name: string | null;
  insert_name: string | null;
  parallel_name: string | null;
  card_number: string | null;
  serial_number: number | null;
  serial_total: number | null;
  is_rookie: boolean;
  is_autograph: boolean;
  is_patch: boolean;
  confidence: string;
  recognition_model?: string | null;
  recognition_status?: "resolved" | "research_required";
  recognition_quality_issues?: string[];
  recognition_estimated_cost_cny?: string;
  recognition_route?: Array<{
    tier: string; model: string; reason: string; status?: string;
    latency_ms?: number; prompt_tokens?: number | null; completion_tokens?: number | null;
    estimated_cost_cny?: string; quality_issues?: string[];
  }>;
  caveats: string[];
  search_queries: string[];
  field_evidence: Record<string, string[]>;
  web_candidates: Array<{
    title: string;
    source_urls: string[];
    image_url?: string | null;
    matching_fields: string[];
    conflicts: string[];
    match_confidence: string;
  }>;
  reference_image_front_url?: string | null;
  reference_image_back_url?: string | null;
  reference_image_source_url?: string | null;
  reference_image_provider?: string | null;
  reference_image_match_confidence?: string | null;
};

export type BatchCardRecognition = {
  filename: string;
  result: CardRecognition | null;
  error: string | null;
};

export type SheetCardRecognitionItem = {
  result: CardRecognition;
  card_image_bbox: [number, number, number, number] | null;
  listing_title: string | null;
  visible_description: string | null;
  text_derived_fields: Record<string, string>;
  order_number: string | null;
  product_number: string | null;
  purchased_at: string | null;
  purchase_price: string | null;
  shipping_price: string | null;
  currency: string | null;
  quantity: number;
  seller_name: string | null;
  source_region: string | null;
  caveats: string[];
};

export type SheetCardRecognition = {
  detected_count: number;
  cards: SheetCardRecognitionItem[];
  caveats: string[];
};

export type PlayerIntelligence = {
  player_id: string;
  player_name: string;
  metrics: Array<{
    season: string;
    scope: string;
    metrics: Record<string, unknown>;
    captured_at: string;
  }>;
  events: Array<{
    id: string;
    event_type: string;
    occurred_at: string;
    headline: string;
    summary: string | null;
    impact_score: string | null;
  }>;
  potential: null | {
    score: string;
    label: string;
    components: Record<string, string>;
  };
};

export type ItemDetail = {
  item: PortfolioItem;
  transactions: Array<{
    id: string;
    transaction_type: "buy" | "sell" | "fee" | "adjustment";
    transacted_at: string;
    quantity: string;
    unit_price: string;
    fee_amount: string;
    currency: string;
    fx_rate_to_base: string;
    venue: string | null;
    notes: string | null;
  }>;
  valuations: Array<{
    id: string;
    valued_at: string;
    method: string;
    currency: string;
    low_value: string;
    fair_value: string;
    high_value: string;
    confidence: string | null;
  }>;
};

export type AnalysisResult = {
  analysis_id: string;
  total_score: string;
  price_assessment: string;
  rating: "strong_buy" | "buy" | "hold" | "reduce" | "avoid";
  currency: string;
  fair_value_low: string;
  fair_value_high: string;
  target_sell_low: string;
  target_sell_high: string;
  holding_period_min_months: number;
  holding_period_max_months: number;
  confidence: string;
  catalysts: string[];
  risks: string[];
};

export type AINarrativeResult = {
  analysis_id: string;
  provider: string;
  model: string;
  narrative: {
    thesis: string;
    price_reasoning: string;
    catalysts: string[];
    risks: string[];
    disclaimer: string;
  };
};

export type KnowledgeWorkspace = {
  id: string;
  name: string;
  revision: number;
  published_version_id: string | null;
  published_version: number | null;
  member_count: number;
  runtime_version: number | null;
  runtime_version_id: string | null;
};

export type KnowledgeAlias = {
  id: string;
  alias: string;
  language: string;
  alias_type: string;
  review_status: string;
};

export type KnowledgeEntity = {
  id: string;
  entity_type: string;
  canonical_name: string;
  confidence: string;
  review_status: string;
  attributes: Record<string, unknown>;
  aliases: KnowledgeAlias[];
};

export type KnowledgeVersion = {
  id: string;
  version_number: number;
  status: string;
  change_summary: Record<string, number>;
  checksum: string;
  release_notes: string | null;
  published_at: string;
};

export type KnowledgeRelation = {
  id: string;
  subject_entity_id: string;
  subject_name: string;
  relation_type: string;
  object_entity_id: string;
  object_name: string;
  reason: string | null;
  review_status: string;
};

export type KnowledgeObservation = {
  id: string;
  observed_text: string;
  occurrence_count: number;
  proposed_entity_id: string | null;
  proposed_alias: string | null;
  confidence: string | null;
  status: string;
  replay_result: Record<string, unknown>;
  last_seen_at: string;
};

export type KnowledgeImpact = {
  object_type: "collection_item" | "market_comp";
  object_id: string;
  reason: Record<string, unknown>;
  revaluation_status: string;
  created_at: string;
};

export type KnowledgeCoverage = {
  id: string;
  manufacturer_name: string | null;
  product_name: string | null;
  season_start: number | null;
  season_end: number | null;
  insert_name: string | null;
  parallel_name: string | null;
  coverage_status: "covered" | "partial" | "needs_review" | "pending_source" | "not_applicable";
  source_title: string | null;
  source_url: string | null;
  notes: string | null;
};

export type KnowledgeBatch = {
  id: string;
  batch_key: string;
  status: string;
  dry_run: boolean;
  summary: Record<string, number>;
  conflict_report: Array<Record<string, unknown>>;
  replay_result: Record<string, boolean>;
};

export type KnowledgeBatchCatalogItem = {
  batch_key: string;
  manifest_version: number;
  source_count: number;
  entity_count: number;
  path: string;
};

// Prefer same-origin requests. In local development next.config.ts proxies /v1
// to the API, avoiding stale ports, CORS failures, and browser/host loopback
// differences. Deployments that use a separate API origin can still opt in.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

function multipartApiUrl(): string {
  if (process.env.NEXT_PUBLIC_UPLOAD_API_URL) {
    return process.env.NEXT_PUBLIC_UPLOAD_API_URL;
  }
  if (process.env.NODE_ENV === "development" && typeof window !== "undefined") {
    // Keep the exact browser hostname so host-scoped Session cookies work for
    // both localhost and 127.0.0.1, while bypassing Next's large-body proxy.
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return API_URL;
}

export function assetUrl(path: string | null): string | null {
  return path ? (path.startsWith("http") ? path : `${API_URL}${path}`) : null;
}

export function externalSourceUrl(sourceUrl: string): string {
  try {
    const url = new URL(sourceUrl);
    if (url.hostname.toLowerCase() === "www.cardhobby.com.cn") {
      url.hostname = "cardhobby.com.cn";
    }
    const item = url.pathname.match(/^\/market\/item\/(\d+)\/?$/i);
    if (url.hostname.toLowerCase() === "cardhobby.com.cn" && item) {
      url.pathname = `/Market/Item/${item[1]}`;
    }
    return url.toString();
  } catch {
    return sourceUrl;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (response.status === 401 && !path.startsWith("/v1/auth/")) {
    window.dispatchEvent(new Event("card-platform:unauthorized"));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function knowledgeHeaders(actorId?: string, idempotencyKey?: string) {
  return {
    ...(actorId ? { "X-Knowledge-Actor": actorId } : {}),
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  };
}

export function getKnowledgeWorkspace(): Promise<KnowledgeWorkspace> {
  return request("/v1/knowledge/workspace");
}

export function bootstrapKnowledge(actorId: string): Promise<{ role: string; user_id: string }> {
  return request("/v1/knowledge/bootstrap", {
    method: "POST",
    headers: knowledgeHeaders(actorId),
  });
}

export function getKnowledgeEntities(
  entityType = "",
  query = "",
): Promise<KnowledgeEntity[]> {
  const params = new URLSearchParams();
  if (entityType) params.set("entity_type", entityType);
  if (query) params.set("q", query);
  return request(`/v1/knowledge/entities?${params}`);
}

export function createKnowledgeEntity(
  actorId: string,
  payload: { entity_type: string; canonical_name: string },
): Promise<KnowledgeEntity> {
  return request("/v1/knowledge/entities", {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify(payload),
  });
}

export function getRecognitionBenchmarks(): Promise<RecognitionBenchmark[]> {
  return request("/v1/recognition-benchmarks");
}

export function createRecognitionBenchmark(payload: {
  name: string;
  sample_size: number;
  page_count: number;
  seed: number;
  max_per_seller: number;
}): Promise<{ id: string }> {
  return request("/v1/recognition-benchmarks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getRecognitionBenchmarkSamples(
  batchId: string,
): Promise<RecognitionBenchmarkSample[]> {
  return request(`/v1/recognition-benchmarks/${batchId}/samples`);
}

export function getRecognitionSamplingAudit(
  batchId: string,
): Promise<RecognitionSamplingAudit> {
  return request(`/v1/recognition-benchmarks/${batchId}/sampling-audit`);
}

export function predictRecognitionBenchmark(
  batchId: string,
  limit = 10,
  options: { retryErrors?: boolean; replayAll?: boolean } = {},
): Promise<RecognitionPredictionJob> {
  return request(`/v1/recognition-benchmarks/${batchId}/predict`, {
    method: "POST",
    body: JSON.stringify({
      limit,
      retry_errors: options.retryErrors ?? false,
      replay_all: options.replayAll ?? false,
    }),
  });
}

export function getRecognitionPredictionJob(
  batchId: string,
  jobId: string,
): Promise<RecognitionPredictionJob> {
  return request(`/v1/recognition-benchmarks/${batchId}/prediction-jobs/${jobId}`);
}

export function getRecognitionFeedbackReport(
  batchId: string,
): Promise<RecognitionFeedbackReport> {
  return request(`/v1/recognition-benchmarks/${batchId}/feedback-report`);
}

export function getRecognitionVersionMetrics(
  batchId: string,
): Promise<RecognitionVersionMetrics> {
  return request(`/v1/recognition-benchmarks/${batchId}/version-metrics`);
}

export function createRecognitionKnowledgeProposals(
  batchId: string,
): Promise<{ proposal_count: number }> {
  return request(`/v1/recognition-benchmarks/${batchId}/knowledge-proposals`, {
    method: "POST",
  });
}

export function labelRecognitionBenchmarkSample(
  batchId: string,
  sampleId: string,
  result: string,
  correctedIdentity?: Record<string, unknown>,
  notes?: string,
): Promise<{ id: string; metrics: RecognitionBenchmarkMetrics }> {
  return request(`/v1/recognition-benchmarks/${batchId}/samples/${sampleId}/label`, {
    method: "POST",
    body: JSON.stringify({
      result,
      corrected_identity: correctedIdentity,
      notes: notes || undefined,
    }),
  });
}

export function acceptRecognitionAssistedLabel(
  batchId: string,
  sampleId: string,
): Promise<{ id: string; metrics: RecognitionBenchmarkMetrics }> {
  return request(
    `/v1/recognition-benchmarks/${batchId}/samples/${sampleId}/assisted-label/accept`,
    { method: "POST" },
  );
}

export function getMarketIdentityConflicts(): Promise<MarketIdentityConflict[]> {
  return request("/v1/market-data/identity-conflicts");
}

export function resolveMarketIdentityConflict(
  sourceCode: string,
  externalId: string,
  payload: { keep_market_comp_id?: string; reject_all?: boolean },
): Promise<{
  source_code: string;
  external_id: string;
  kept_market_comp_id: string | null;
  rejected_count: number;
}> {
  return request(
    `/v1/market-data/identity-conflicts/${encodeURIComponent(sourceCode)}/${encodeURIComponent(externalId)}/resolve`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function addKnowledgeAlias(
  actorId: string,
  entityId: string,
  payload: { alias: string; language: string; alias_type: string },
): Promise<KnowledgeAlias> {
  return request(`/v1/knowledge/entities/${entityId}/aliases`, {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify(payload),
  });
}

export function reviewKnowledge(
  actorId: string,
  objectType: "entity" | "alias" | "relation",
  objectId: string,
  decision: "approved" | "rejected",
): Promise<Record<string, unknown>> {
  return request(`/v1/knowledge/${objectType}/${objectId}/review`, {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify({ decision }),
  });
}

export function seedKnowledge(actorId: string): Promise<{ entities: number; aliases: number }> {
  return request("/v1/knowledge/seed", {
    method: "POST",
    headers: knowledgeHeaders(actorId),
  });
}

export function getKnowledgeVersions(): Promise<KnowledgeVersion[]> {
  return request("/v1/knowledge/versions");
}

export function publishKnowledge(
  actorId: string,
  expectedRevision: number,
  releaseNotes: string,
): Promise<KnowledgeVersion> {
  return request("/v1/knowledge/publish", {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify({ expected_revision: expectedRevision, release_notes: releaseNotes }),
  });
}

export function rollbackKnowledge(
  actorId: string,
  expectedRevision: number,
  targetVersionId: string,
): Promise<KnowledgeVersion> {
  return request("/v1/knowledge/rollback", {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify({
      expected_revision: expectedRevision,
      target_version_id: targetVersionId,
    }),
  });
}

export function getKnowledgeRelations(): Promise<KnowledgeRelation[]> {
  return request("/v1/knowledge/relations");
}

export function createKnowledgeRelation(
  actorId: string,
  payload: {
    subject_entity_id: string;
    relation_type: string;
    object_entity_id: string;
    reason: string;
  },
): Promise<KnowledgeRelation> {
  return request("/v1/knowledge/relations", {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify(payload),
  });
}

export function getKnowledgeObservations(): Promise<KnowledgeObservation[]> {
  return request("/v1/knowledge/observations");
}

export function reviewKnowledgeObservation(
  actorId: string,
  observationId: string,
  payload: {
    proposed_entity_id: string;
    proposed_alias: string;
    replay_result: Record<string, unknown>;
    confidence: number;
    decision: "validated" | "rejected";
  },
): Promise<KnowledgeObservation> {
  return request(`/v1/knowledge/observations/${observationId}/review`, {
    method: "POST",
    headers: knowledgeHeaders(actorId),
    body: JSON.stringify(payload),
  });
}

export function promoteKnowledgeObservation(
  actorId: string,
  observationId: string,
): Promise<KnowledgeAlias> {
  return request(`/v1/knowledge/observations/${observationId}/promote`, {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
  });
}

export function getKnowledgeImpact(versionId: string): Promise<KnowledgeImpact[]> {
  return request(`/v1/knowledge/versions/${versionId}/impact`);
}

export function revalueKnowledgeImpact(
  actorId: string,
  versionId: string,
): Promise<{ queued: number }> {
  return request(`/v1/knowledge/versions/${versionId}/revalue`, {
    method: "POST",
    headers: knowledgeHeaders(actorId),
  });
}

export function getKnowledgeCoverage(): Promise<{
  counts: Record<string, number>;
  rows: KnowledgeCoverage[];
}> {
  return request("/v1/knowledge/coverage");
}

export function getKnowledgeBatches(): Promise<KnowledgeBatch[]> {
  return request("/v1/knowledge/batches");
}

export function getKnowledgeBatchCatalog(): Promise<KnowledgeBatchCatalogItem[]> {
  return request("/v1/knowledge/batches/catalog");
}

export function applyKnowledgeCatalogBatch(
  actorId: string,
  batchKey: string,
  dryRun: boolean,
): Promise<KnowledgeBatch> {
  return request(`/v1/knowledge/batches/${encodeURIComponent(batchKey)}`, {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify({ dry_run: dryRun }),
  });
}

export function applyKnowledgeMvpBatch(
  actorId: string,
  dryRun: boolean,
): Promise<KnowledgeBatch> {
  return request("/v1/knowledge/batches/mvp-v3", {
    method: "POST",
    headers: knowledgeHeaders(actorId),
    body: JSON.stringify({ dry_run: dryRun }),
  });
}

export function registerUser(payload: {
  email: string;
  password: string;
  display_name: string;
  base_currency: string;
}): Promise<AuthUser> {
  return request("/v1/auth/register", { method: "POST", body: JSON.stringify(payload) });
}

export function loginUser(payload: { email: string; password: string }): Promise<AuthUser> {
  return request("/v1/auth/login", { method: "POST", body: JSON.stringify(payload) });
}

export function getCurrentUser(): Promise<AuthUser> {
  return request("/v1/auth/me");
}

export function logoutUser(): Promise<void> {
  return request("/v1/auth/logout", { method: "POST" });
}

export function getAuthSessions(): Promise<AuthSession[]> {
  return request("/v1/auth/sessions");
}

export function revokeAuthSession(sessionId: string): Promise<void> {
  return request(`/v1/auth/sessions/${sessionId}`, { method: "DELETE" });
}

export function changePassword(payload: {
  current_password: string;
  new_password: string;
}): Promise<void> {
  return request("/v1/auth/change-password", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPortfolio(
  ownerId: string,
): Promise<[PortfolioSummary, PortfolioItem[]]> {
  return Promise.all([
    request<PortfolioSummary>(`/v1/portfolio/summary?owner_id=${ownerId}`),
    request<PortfolioItem[]>(`/v1/portfolio/items?owner_id=${ownerId}`),
  ]);
}

export function getIdentityCorrectionCounts(
  ownerId: string,
): Promise<Record<string, number>> {
  return request(`/v1/portfolio/identity-corrections?owner_id=${ownerId}`);
}

export function getIdentityCorrections(
  itemId: string,
  ownerId: string,
): Promise<IdentityCorrection[]> {
  return request(
    `/v1/portfolio/items/${itemId}/identity-corrections?owner_id=${ownerId}`,
  );
}

export function applyIdentityCorrections(
  itemId: string,
  ownerId: string,
  suggestionIds: string[] = [],
): Promise<{ applied_count: number; valuation_job_id: string }> {
  return request(`/v1/portfolio/items/${itemId}/identity-corrections/apply`, {
    method: "POST",
    body: JSON.stringify({
      owner_id: ownerId,
      suggestion_ids: suggestionIds,
    }),
  });
}

export function getIdentityReview(itemId: string): Promise<IdentityReviewDetail> {
  return request(`/v1/portfolio/identity-review/items/${itemId}`);
}

export function researchIdentity(itemId: string): Promise<{ status: string; candidate_count: number }> {
  return request(`/v1/portfolio/identity-review/items/${itemId}/research`, {
    method: "POST", body: JSON.stringify({ visual_observations: {} }),
  });
}

export function publishShadowValuation(itemId: string): Promise<{ valuation_id: string; fair_value: string; currency: string; already_published: boolean }> {
  return request(`/v1/portfolio/identity-review/items/${itemId}/publish-shadow`, { method: "POST" });
}

export function confirmIdentityReview(
  itemId: string,
  identity: ConfirmedCardIdentity,
  reason: string,
): Promise<{ identity_version_id: string; accepted_count: number; rejected_count: number }> {
  return request(`/v1/portfolio/identity-review/items/${itemId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ identity, reason, field_provenance: { reviewer: "portfolio_workbench" } }),
  });
}

export function getTrash(ownerId: string): Promise<PortfolioItem[]> {
  return request(`/v1/portfolio/trash?owner_id=${ownerId}`);
}

export function deleteItem(
  itemId: string,
  ownerId: string,
  reason?: string,
): Promise<void> {
  return request(`/v1/portfolio/items/${itemId}`, {
    method: "DELETE",
    body: JSON.stringify({ owner_id: ownerId, reason: reason || null }),
  });
}

export function restoreItem(itemId: string, ownerId: string): Promise<void> {
  return request(`/v1/portfolio/items/${itemId}/restore`, {
    method: "POST",
    body: JSON.stringify({ owner_id: ownerId }),
  });
}

export function permanentlyDeleteItem(
  itemId: string,
  ownerId: string,
): Promise<void> {
  return request(`/v1/portfolio/items/${itemId}/permanent`, {
    method: "DELETE",
    body: JSON.stringify({ owner_id: ownerId }),
  });
}

export function createItem(payload: unknown): Promise<{ id: string }> {
  return request("/v1/portfolio/items", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function analyzeItem(
  itemId: string,
  payload: unknown,
): Promise<AnalysisResult> {
  return request(`/v1/portfolio/items/${itemId}/analysis`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function automaticallyAnalyzeItem(
  itemId: string,
  payload: { owner_id: string; asking_price: number; currency: string },
): Promise<AnalysisResult> {
  return request(`/v1/portfolio/items/${itemId}/analysis/auto`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function generateNarrative(
  analysisId: string,
): Promise<AINarrativeResult> {
  return request(`/v1/analyses/${analysisId}/narrative`, { method: "POST" });
}

export function getPlayerIntelligence(
  playerId: string,
): Promise<PlayerIntelligence> {
  return request(`/v1/players/${playerId}/intelligence`);
}

export function syncPlayer(
  playerId: string,
  nbaPersonId: number,
): Promise<{
  status: string;
  source_results: Record<string, { status: string; count: number }>;
}> {
  return request(`/v1/players/${playerId}/sync`, {
    method: "POST",
    body: JSON.stringify({
      nba_person_id: nbaPersonId,
      sources: ["nba_stats", "news", "injury"],
    }),
  });
}

export function assessPotential(
  playerId: string,
  payload: unknown,
): Promise<{
  score: string;
  label: string;
  components: Record<string, string>;
}> {
  return request(`/v1/players/${playerId}/potential`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getItemDetail(
  itemId: string,
  ownerId: string,
): Promise<ItemDetail> {
  return request(`/v1/portfolio/items/${itemId}/detail?owner_id=${ownerId}`);
}

export function getCardCatalog(
  itemId: string,
  ownerId: string,
  forceRefresh = false,
): Promise<CardRecognition> {
  return request(
    `/v1/portfolio/items/${itemId}/catalog-enrichment?owner_id=${ownerId}&force_refresh=${forceRefresh}`,
  );
}

export function applyCatalogImages(
  itemId: string,
  ownerId: string,
  side: "front" | "back" | "both",
): Promise<Array<{ item_id: string; side: "front" | "back"; image_url: string }>> {
  return request(
    `/v1/portfolio/items/${itemId}/catalog-images/apply?owner_id=${ownerId}&side=${side}`,
    { method: "POST" },
  );
}

export function addTransaction(
  itemId: string,
  ownerId: string,
  payload: unknown,
): Promise<{ id: string }> {
  return request(`/v1/portfolio/items/${itemId}/transactions?owner_id=${ownerId}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addValuation(
  itemId: string,
  ownerId: string,
  payload: unknown,
): Promise<{ id: string }> {
  return request(`/v1/portfolio/items/${itemId}/valuations?owner_id=${ownerId}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getMarketComps(
  itemId: string,
  ownerId: string,
): Promise<MarketComp[]> {
  return request(
    `/v1/portfolio/items/${itemId}/market-comps?owner_id=${ownerId}`,
  );
}

export function getAutomatedValuation(
  itemId: string,
  ownerId: string,
): Promise<AutomatedValuationDetail> {
  return request(
    `/v1/portfolio/items/${itemId}/automated-valuation?owner_id=${ownerId}`,
  );
}

export function retryAutomatedValuation(
  itemId: string,
  ownerId: string,
): Promise<{ id: string }> {
  return request(
    `/v1/portfolio/items/${itemId}/automated-valuation/retry?owner_id=${ownerId}`,
    { method: "POST" },
  );
}

export function addMarketComp(
  itemId: string,
  payload: unknown,
): Promise<{ id: string }> {
  return request(`/v1/portfolio/items/${itemId}/market-comps`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function estimateMarket(
  itemId: string,
  payload: unknown,
): Promise<{
  low_value: string;
  fair_value: string;
  high_value: string;
  confidence: string;
  evidence_count: number;
}> {
  return request(`/v1/portfolio/items/${itemId}/market-estimate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

async function multipartRequest<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("image", file);
  const response = await fetch(`${multipartApiUrl()}${path}`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (response.status === 401) window.dispatchEvent(new Event("card-platform:unauthorized"));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function recognizeCard(file: File): Promise<CardRecognition> {
  return multipartRequest("/v1/cards/recognize", file);
}

export async function recognizeCardPair(
  front: File,
  back: File,
): Promise<CardRecognition> {
  const form = new FormData();
  form.append("front", front);
  form.append("back", back);
  const response = await fetch(`${multipartApiUrl()}/v1/cards/recognize-pair`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (response.status === 401) window.dispatchEvent(new Event("card-platform:unauthorized"));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<CardRecognition>;
}

export async function recognizeCardBatch(
  files: File[],
): Promise<BatchCardRecognition[]> {
  const form = new FormData();
  files.forEach((file) => form.append("images", file));
  const response = await fetch(`${multipartApiUrl()}/v1/cards/recognize-batch`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (response.status === 401) window.dispatchEvent(new Event("card-platform:unauthorized"));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<BatchCardRecognition[]>;
}

export async function enqueueScannerRecognition(
  front: File,
  back: File,
  frontRaw?: File,
  backRaw?: File,
): Promise<ScannerRecognitionJob> {
  const form = new FormData();
  form.append("front", front);
  form.append("back", back);
  if (frontRaw) form.append("front_raw", frontRaw);
  if (backRaw) form.append("back_raw", backRaw);
  const response = await fetch(`${multipartApiUrl()}/v1/scanner/jobs`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (response.status === 401) window.dispatchEvent(new Event("card-platform:unauthorized"));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `加入识别队列失败 (${response.status})`);
  }
  return response.json() as Promise<ScannerRecognitionJob>;
}

export function getScannerRecognitionJobs(): Promise<ScannerRecognitionJob[]> {
  return request("/v1/scanner/jobs");
}

export function getScannerCameraControls(): Promise<ScannerCameraControls> {
  return request("/v1/scanner/camera-controls");
}

export function updateScannerCameraControls(values: {
  exposure_mode?: "auto" | "manual";
  exposure_time?: number;
}): Promise<ScannerCameraControls> {
  return request("/v1/scanner/camera-controls", {
    method: "PUT",
    body: JSON.stringify(values),
  });
}

export function retryScannerRecognition(jobId: string): Promise<ScannerRecognitionJob> {
  return request(`/v1/scanner/jobs/${jobId}/retry`, { method: "POST" });
}

export function deleteScannerRecognition(jobId: string): Promise<void> {
  return request(`/v1/scanner/jobs/${jobId}`, { method: "DELETE" });
}

export async function getScannerJobImage(url: string, side: "front" | "back"): Promise<File> {
  const response = await fetch(`${API_URL}${url}`, { credentials: "include" });
  if (!response.ok) throw new Error(`扫描图片读取失败 (${response.status})`);
  const blob = await response.blob();
  return new File([blob], `scanner-${side}.jpg`, { type: blob.type || "image/jpeg" });
}

export function recognizeCardAuto(file: File): Promise<SheetCardRecognition> {
  return multipartRequest("/v1/cards/recognize-auto", file);
}

export function uploadItemImage(
  itemId: string,
  ownerId: string,
  side: "front" | "back",
  file: File,
): Promise<{ image_url: string }> {
  return multipartRequest(
    `/v1/portfolio/items/${itemId}/images?owner_id=${ownerId}&side=${side}`,
    file,
  );
}

export function rotateItemImage(
  itemId: string,
  side: "front" | "back",
  degrees: 90 | 180 | 270,
): Promise<{ image_url: string }> {
  return request(
    `/v1/portfolio/items/${itemId}/images/${side}/rotate?degrees=${degrees}`,
    { method: "POST" },
  );
}

const CARDHOBBY_HELPER_URL =
  process.env.NEXT_PUBLIC_CARDHOBBY_HELPER_URL ?? "http://127.0.0.1:8765";

async function cardHobbyHelperRequest<T>(
  path: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const response = await fetch(`${CARDHOBBY_HELPER_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error ?? `卡淘桌面助手请求失败 (${response.status})`);
  }
  return result as T;
}

export function launchCardHobbyDesktop(): Promise<{
  order_url: string;
  profile_dir: string;
  reused_session: boolean;
}> {
  return cardHobbyHelperRequest("/launch");
}

export function scanCardHobbyDesktop(
  ownerId: string,
): Promise<CardHobbyImportPreview> {
  return cardHobbyHelperRequest("/scan", { owner_id: ownerId });
}

export function confirmCardHobbyDesktop(
  batchId: string,
  ownerId: string,
  items: Array<Record<string, unknown>>,
): Promise<{ imported_item_ids: string[]; skipped_draft_ids: string[] }> {
  return cardHobbyHelperRequest("/confirm", {
    batch_id: batchId,
    owner_id: ownerId,
    items,
  });
}
