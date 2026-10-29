// Shared API contracts. Keep device transports and UI out of this module.
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

export type PortfolioPage = {
  summary?: PortfolioSummary | null;
  snapshot_token?: string | null;
  snapshot_at?: string | null;
  items: PortfolioItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
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
  collection_item_id: string | null;
  imported_at: string | null;
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

export type FlatbedCropQuality = {
  presence_score: number;
  isolation_score: number;
  boundary_score: number;
  usability_score: number;
  usable: boolean;
  needs_review: boolean;
  violations: Array<{ code: string; severity: "warning" | "error"; message: string }>;
};
export type FlatbedRegion = {
  id: string;
  ordinal: number;
  bounds: { x: number; y: number; width: number; height: number; angle?: number; points?: Array<{ x: number; y: number }> };
  confidence: number;
  failure_reason: string | null;
  crop_quality?: FlatbedCropQuality;
  signals?: { crop_quality?: FlatbedCropQuality; [key: string]: unknown };
};
export type FlatbedPair = { front_id: string; back_id: string; confidence: number; confidence_level: "high" | "medium" | "low"; conflicts: string[]; signals: Record<string, number>; requires_review: boolean; identity_status?: "consistent" | "conflict" | "unknown"; identity_reason_codes?: string[]; identity_ambiguous?: boolean };
export type FlatbedSheet = { id: string; side: "front" | "back"; revision: number; content_type?: string; created_at?: string; detector_version?: string; layout_hypothesis?: Record<string, unknown>; requires_review?: boolean };
export type FlatbedSession = { id: string; status: string; dpi: 300 | 600; pairing_requires_review: boolean; pairing_transform: string | null; submitted_at: string | null; pairs?: Array<Record<string, unknown>>; regions?: Array<FlatbedRegion & { side: "front" | "back" }>; sheets?: FlatbedSheet[] };
export type FlatbedCaptureProgress = { capture_id: string; session_id: string; side: "front" | "back"; phase: "scanning" | "processing" | "completed" | "failed"; detail: string | null; updated_at: string };

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

export type NewsAlert = {
  id: string;
  player_id: string;
  event_key: string;
  direction: "positive" | "negative";
  headline: string;
  facts: string;
  attribution: string;
  confidence: "low" | "medium" | "high";
  source_url: string;
  published_at: string;
  created_at: string;
  expires_at: string;
};

export type PortfolioItem = {
  holding_status?: "held" | "sold";
  news_alerts?: NewsAlert[];
  is_redemption?: boolean;
  redemption_status?: "unknown" | "unredeemed" | "redeemed" | "expired";
  id: string;
  display_id: number;
  identity_missing_fields?: string[];
  identity_review_required?: boolean;
  player_id: string;
  player_name: string | null;
  brand_name: string | null;
  season_start: number | null;
  season_end: number | null;
  product_name: string | null;
  set_name: string | null;
  card_number: string | null;
  insert_name: string | null;
  parallel_name: string | null;
  is_rookie: boolean | null;
  is_autograph: boolean | null;
  autograph_type: "on_card" | "sticker" | "cut" | "unknown" | "none";
  has_inscription: boolean | null;
  inscription_text: string | null;
  is_memorabilia: boolean | null;
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
  is_patch: boolean | null;
  is_numbered: boolean | null;
  serial_number: number | null;
  serial_total: number | null;
  grade_company: string | null;
  grade_value: string | null;
  image_front_url: string | null;
  image_back_url: string | null;
  strategy_tags: string[];
  quantity_held: string;
  remaining_cost: string;
  purchase_price: string | null;
  purchase_total_cost?: string | null;
  purchase_currency: string | null;
  realized_profit: string;
  valuation_currency: string | null;
  valuation_low: string | null;
  fair_value: string | null;
  valuation_high: string | null;
  codex_analysis_version?: number | null;
  valuation_synthesis?: {
    rule_version: string;
    status: string;
    reasons: string[];
    original_id: string | null;
    original_fair_value: string | number | null;
    codex_id: string | null;
    codex_analysis_version: number | null;
    evidence_urls: string[];
    weights: Record<string, string | number>;
    confidence?: string | number;
    recovered_evidence?: Array<{kind: string; url: string | null}>;
    accepted_evidence?: Array<{source: string; url: string; price_base: string | number; sold_at: string; verification_scope?: string}>;
    rejected_evidence?: Array<{url: string | null; reasons: string[]}>;
    identity_checks?: Array<{agreed: string[]; supplemented: string[]; conflicts: string[]; matched: boolean}>;
    identity_corrections?: Record<string, {before: unknown; after: unknown; reason: string}>;
    joint_identity?: Record<string, unknown>;
  };
  codex_valuation_currency?: string | null;
  codex_valuation_low?: string | null;
  codex_fair_value?: string | null;
  codex_valuation_high?: string | null;
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
  recognition_confidence: string | null;
  recognition_status: "resolved" | "research_required" | null;
  recognition_quality_issues: string[];
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
  similarity?: string | null;
  comparable_tier?: "T0" | "T1" | "T2" | "T3" | "rejected" | null;
  classification: {
    source_code?: string;
    origin_source_code?: string;
    ingestion?: string;
    hard_conflicts?: string[];
    field_matches?: Record<string, boolean | null>;
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
      risk_flags?: string[];
      selection_policy?: string;
      pricing_basis?: string;
      source_analyzed_at?: string | null;
      exact_version_verified?: boolean;
      numbered_premium_applied?: boolean;
      grade_premium_applied?: boolean;
      automatic_publication?: boolean;
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
      used_comparables?: Array<{
        market_comp_id: string;
        title?: string | null;
        source_url?: string | null;
        tier?: "T0" | "T1" | "T2" | "T3" | "rejected" | null;
        classification?: {
          hard_conflicts?: string[];
          field_matches?: Record<string, boolean | null>;
          cardhobby_provider_tier?: string;
        };
        visual_verdict?: string | null;
        visual_confidence?: string | null;
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
      used_comparables?: Array<{ market_comp_id: string; tier?: string | null }>;
      rejected?: Array<{ id: string; reasons: string[] }>;
      published_value_unchanged?: boolean;
      risk_flags?: string[];
      notes?: string[];
      pricing_basis?: string;
      exact_version_verified?: boolean;
      references?: Array<{
        title?: string;
        source_url?: string;
        evidence_type?: string;
        condition_type?: string;
      }>;
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
  is_rookie: boolean | null; is_autograph: boolean | null; autograph_type: string;
  is_memorabilia: boolean | null; memorabilia_types: string[];
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
    source_url: string; image_url: string | null; evidence_level: string; decision: "accepted" | "rejected" | "needs_review";
    reason_codes: string[];
  }>;
  research: null | { id: string; status: string; trigger_reasons: string[]; source_version: string };
  candidates: Array<{
    id: string; rank: number; identity: ConfirmedCardIdentity; confidence: string;
    field_provenance: Record<string, unknown>; visual_reasons: string[];
    checklist_evidence: Array<{ source_url: string; source_version: string; parallel_name: string | null }>;
    market_evidence: Array<{
      id: string; provider_code: string; external_sale_id: string;
      raw_title?: string; source_url?: string; image_url?: string | null;
      sale_price: string; shipping_price?: string; currency: string;
      price_base?: string; occurred_at: string; reason_codes: string[];
      evidence_level: string;
    }>;
    validation_reasons: string[]; is_valid: boolean;
  }>;
  latest_shadow: null | {
    id: string; identity_version_id: string; status: string; currency: string;
    low_value: string | null; fair_value: string | null; high_value: string | null;
    confidence: string; evidence_count: number; review_reasons: string[]; created_at: string;
  };
};

export type CardFaceObservation = {
  role: "front" | "back" | "ad" | "unknown";
  player_names: string[];
  brand_name: string | null;
  product_name: string | null;
  insert_name: string | null;
  card_number: string | null;
  multi_subject: boolean;
  evidence: string[];
};

export type CardPairValidation = {
  status: "consistent" | "conflict" | "unknown";
  reason_codes: string[];
  evidence: string[];
  front?: CardFaceObservation;
  back?: CardFaceObservation;
  image_content_hashes: Record<string, string>;
  version: number;
};

export type CardRecognition = {
  field_assessments?: Record<string, {
    status: "unknown" | "proposed" | "verified" | "conflicted";
    value?: unknown;
    source?: string;
    evidence?: string[];
    issues?: string[];
  }>;
  pair_validation?: CardPairValidation | null;
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
  is_numbered?: boolean | null;
  is_rookie: boolean | null;
  is_autograph: boolean | null;
  is_patch: boolean | null;
  autograph_type?: "on_card" | "sticker" | "cut" | "unknown" | "none" | null;
  has_inscription?: boolean | null;
  inscription_text?: string | null;
  is_memorabilia?: boolean | null;
  memorabilia_types?: string[];
  memorabilia_usage?: string | null;
  memorabilia_player?: string | null;
  grade_company?: string | null;
  grade_value?: string | number | null;
  confidence: string;
  recognition_model?: string | null;
  recognition_status?: "resolved" | "research_required";
  recognition_quality_issues?: string[];
  recognition_estimated_cost_cny?: string | null;
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
  purchase_total_cost?: string | null;
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

export type ExpertAnalysisSnapshot = {
  id: string; job_id: string; collection_item_id: string; analysis_version: number;
  source: string; model_name: string; identity_version_id: string | null;
  identity_summary: string; identity_confidence: string; currency: string | null;
  fair_value_low: string | null; fair_value: string | null; fair_value_high: string | null;
  valuation_confidence: string | null;
  evidence_summary: Array<Record<string, unknown>>; value_drivers: string[];
  risks_and_conflicts: string[]; investment_conclusion: string | null;
  recommended_actions: string[]; raw_result: Record<string, unknown>;
  status: "succeeded" | "failed"; error_message: string | null; created_at: string;
};

export type ExpertAnalysisHistory = {
  latest: ExpertAnalysisSnapshot | null;
  history: ExpertAnalysisSnapshot[];
  current_job: null | {
    id: string; collection_item_id: string; status: "queued" | "running" | "succeeded" | "failed";
    attempt_count: number; error_message: string | null; created_at: string;
    started_at: string | null; finished_at: string | null;
  };
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
  source?: Record<string, unknown>;
  confidence?: string;
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
  context: {
    kind?: string;
    canonical_name?: string;
    entity_type?: string;
    applicability?: Record<string, unknown>;
    source_samples?: Array<{ title: string; url: string }>;
    sample_count?: number;
    requires_human_review?: boolean;
    [key: string]: unknown;
  };
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

/** Generic capture DTO keeps absent sides nullable without weakening the scanner DTO. */
export type CaptureImage = {
  url:string; revision:string; source_url?:string|null; source_hash?:string|null;
  crop_recipe?:Record<string,unknown>|null;
  thumb_url?:string|null; preview_url?:string|null; original_url?:string|null;
};
export type CaptureJob = Omit<ScannerRecognitionJob,"front_image_url"|"back_image_url"> & {
  front_image_url:string|null; back_image_url:string|null;
  available_sides:("front"|"back")[]; images:Partial<Record<"front"|"back",CaptureImage|null>>;
  source:string; require_confirmation:boolean; revision:number; target_item_id?:string|null;
};
