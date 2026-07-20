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
  market_comp_count: number;
  unrealized_profit: string | null;
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

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function assetUrl(path: string | null): string | null {
  return path ? (path.startsWith("http") ? path : `${API_URL}${path}`) : null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function createUser(payload: {
  email: string;
  display_name: string;
  base_currency: string;
}): Promise<{ id: string }> {
  return request("/v1/users", {
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

export function addTransaction(
  itemId: string,
  payload: unknown,
): Promise<{ id: string }> {
  return request(`/v1/portfolio/items/${itemId}/transactions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addValuation(
  itemId: string,
  payload: unknown,
): Promise<{ id: string }> {
  return request(`/v1/portfolio/items/${itemId}/valuations`, {
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
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function recognizeCard(file: File): Promise<CardRecognition> {
  return multipartRequest("/v1/cards/recognize", file);
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
