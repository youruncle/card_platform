export type * from "../../../packages/card-core/src/contracts";
import type {
  PortfolioSummary,
  PortfolioPage,
  AuthUser,
  AuthSession,
  ScannerRecognitionJob,
  ScannerCameraProperty,
  ScannerCameraControls,
  FlatbedCropQuality,
  FlatbedRegion,
  FlatbedPair,
  FlatbedSheet,
  FlatbedSession,
  FlatbedCaptureProgress,
  RecognitionBenchmarkMetrics,
  RecognitionBenchmark,
  MarketIdentityConflict,
  RecognitionBenchmarkSample,
  RecognitionPredictionJob,
  RecognitionFeedbackReport,
  RecognitionVersionMetrics,
  RecognitionSamplingAudit,
  NewsAlert,
  PortfolioItem,
  MarketComp,
  CardHobbyImportCapture,
  CardHobbyImportDraft,
  CardHobbyImportPreview,
  AutomatedValuationDetail,
  IdentityCorrection,
  ConfirmedCardIdentity,
  IdentityReviewDetail,
  CardRecognition,
  BatchCardRecognition,
  SheetCardRecognitionItem,
  SheetCardRecognition,
  PlayerIntelligence,
  ItemDetail,
  AnalysisResult,
  ExpertAnalysisSnapshot,
  ExpertAnalysisHistory,
  AINarrativeResult,
  KnowledgeWorkspace,
  KnowledgeAlias,
  KnowledgeEntity,
  KnowledgeVersion,
  KnowledgeRelation,
  KnowledgeObservation,
  KnowledgeImpact,
  KnowledgeCoverage,
  KnowledgeBatch,
  KnowledgeBatchCatalogItem
} from "../../../packages/card-core/src/contracts";

import { portfolioSortQuery, type PortfolioSort } from "../../../packages/card-core/src/sorting";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

function multipartApiUrl(): string {
  if (process.env.NEXT_PUBLIC_UPLOAD_API_URL) {
    return process.env.NEXT_PUBLIC_UPLOAD_API_URL;
  }
  if (
    typeof window !== "undefined"
    && (process.env.NODE_ENV === "development" || window.location.port === "3000")
  ) {
    // Keep the exact browser hostname so host-scoped Session cookies work for
    // both localhost and 127.0.0.1, while bypassing Next's long-request proxy.
    // The local launchd stack serves a production build on port 3000, so the
    // direct API route must not be limited to NODE_ENV=development.
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

export class ApiError extends Error {
  constructor(message: string, public code?: string) { super(message); }
}

export type CardHobbyAccountStatus = {saved: boolean; state: "unbound" | "connected" | "login_required" | "verification_required"};
const cardHobbyAccountPath = "/v1/purchase-cost/cardhobby";
export const getCardHobbyAccountStatus = (signal?: AbortSignal) => request<CardHobbyAccountStatus>(`${cardHobbyAccountPath}/status`, {signal});
export const loginCardHobbyAccount = (username: string, password: string, signal?: AbortSignal) => request<CardHobbyAccountStatus>(`${cardHobbyAccountPath}/login`, {method:"POST", body:JSON.stringify({username,password}), signal});
export const verifyCardHobbyAccount = (code: string, signal?: AbortSignal) => request<CardHobbyAccountStatus>(`${cardHobbyAccountPath}/verify`, {method:"POST", body:JSON.stringify({code}), signal});
export type CardHobbyVerification = {captcha_url: string; sms_sent: boolean; retry_after_seconds: number};
export const getCardHobbyVerification = (signal?: AbortSignal) => request<CardHobbyVerification>(`${cardHobbyAccountPath}/verification`, {signal});
export const restartCardHobbyVerification = (signal?: AbortSignal) => request<CardHobbyAccountStatus>(`${cardHobbyAccountPath}/restart-verification`, {method:"POST", signal});
export const sendCardHobbySms = (signal?: AbortSignal) => request<CardHobbyVerification>(`${cardHobbyAccountPath}/send-sms`, {method:"POST", signal});
export const forgetCardHobbyAccount = (signal?: AbortSignal) => request<void>(`${cardHobbyAccountPath}/account`, {method:"DELETE",signal});
export const searchSavedCardHobbyOrders = (keywords: string[], signal?: AbortSignal) => request<{orders:unknown[];complete:boolean}>(`${cardHobbyAccountPath}/search`, {method:"POST",body:JSON.stringify({keywords}),signal});

async function request<T>(path: string, init?: RequestInit, baseUrl = API_URL): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (response.status === 401 && !path.startsWith("/v1/auth/")) {
    window.dispatchEvent(new Event("card-platform:unauthorized"));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 500 && !body.detail) {
      throw new Error("API 服务不可用，请确认后端 8000 端口已启动");
    }
    const detail = body.detail;
    throw new ApiError(typeof detail === "string" ? detail : detail?.message ?? `请求失败 (${response.status})`, detail?.code);
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

export function updateKnowledgeAlias(
  actorId: string,
  aliasId: string,
  payload: { alias?: string; language?: string; alias_type?: string },
): Promise<KnowledgeAlias> {
  return request(`/v1/knowledge/aliases/${aliasId}`, {
    method: "PATCH",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
    body: JSON.stringify(payload),
  });
}

export function deleteKnowledgeAlias(actorId: string, aliasId: string): Promise<KnowledgeAlias> {
  return request(`/v1/knowledge/aliases/${aliasId}`, {
    method: "DELETE",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
  });
}

export function initializeMarketplaceAliases(actorId: string): Promise<{
  created_entities: number; created_aliases: number; existing_aliases: number;
}> {
  return request("/v1/knowledge/aliases/initialize", {
    method: "POST",
    headers: knowledgeHeaders(actorId, crypto.randomUUID()),
  });
}

export function discoverMarketplaceAlias(
  actorId: string,
  payload: {
    entity_id: string;
    observed_alias: string;
    applicability: Record<string, unknown>;
    samples: Array<{ title: string; url: string }>;
  },
): Promise<KnowledgeObservation> {
  return request("/v1/knowledge/aliases/discover", {
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

import { SearchFilters, searchQuery } from './portfolio-search';

export function getPortfolioSummary(ownerId: string): Promise<PortfolioSummary> {
  return request(`/v1/portfolio/summary?owner_id=${ownerId}`);
}

export function getPortfolioPage(
  ownerId: string, page: number, filters: SearchFilters, pageSize: number,
  sort: PortfolioSort, snapshotToken?: string | null, includeSummary = false,
): Promise<PortfolioPage> {
  return request(`/v1/portfolio/items/page?owner_id=${ownerId}&page=${page}&page_size=${pageSize}${searchQuery(filters)}${portfolioSortQuery(sort, snapshotToken)}${includeSummary ? '&include_summary=true' : ''}`);
}

export function getPortfolio(
  ownerId: string,
  page = 1,
  filters: SearchFilters = {},
  pageSize = 30,
  sort?: PortfolioSort,
  snapshotToken?: string | null,
  snapshotSummary?: PortfolioSummary,
): Promise<[PortfolioSummary, PortfolioPage]> {
  if (sort) {
    const reuseSummary = snapshotToken && snapshotSummary;
    return request<PortfolioPage>(
      `/v1/portfolio/items/page?owner_id=${ownerId}&page=${page}&page_size=${pageSize}${searchQuery(filters)}${portfolioSortQuery(sort, snapshotToken)}${reuseSummary ? "" : "&include_summary=true"}`,
    ).then(async result => [
      result.summary ?? (reuseSummary || await request<PortfolioSummary>(`/v1/portfolio/summary?owner_id=${ownerId}`)),
      result,
    ]);
  }
  return Promise.all([
    snapshotToken && snapshotSummary ? Promise.resolve(snapshotSummary)
      : request<PortfolioSummary>(`/v1/portfolio/summary?owner_id=${ownerId}`),
    request<PortfolioPage>(
      `/v1/portfolio/items/page?owner_id=${ownerId}&page=${page}&page_size=${pageSize}${searchQuery(filters)}${sort ? portfolioSortQuery(sort, snapshotToken) : ""}`,
    ),
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
): Promise<{ identity_version_id: string; accepted_count: number; rejected_count: number; needs_review_count: number }> {
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

export function quickUpdateItem(
  itemId: string,
  payload: {
    holding_status?: "held" | "sold";
    purchase_price?: number;
    purchase_currency?: string;
    is_rookie?: boolean;
    is_numbered?: boolean;
    is_autograph?: boolean;
    is_memorabilia?: boolean;
    serial_number?: number | null;
    serial_total?: number | null;
  },
): Promise<void> {
  return request(`/v1/portfolio/items/${itemId}`, {
    method: "PATCH",
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

export function getExpertAnalysis(itemId: string): Promise<ExpertAnalysisHistory> {
  return request(`/v1/portfolio/items/${itemId}/expert-analysis`);
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

export function getFlatbedDevice(): Promise<{ status: string; name: string; serial: string; capabilities: Record<string, unknown> }> {
  return request("/v1/flatbed/device");
}

export function createFlatbedSession(dpi: 300 | 600): Promise<FlatbedSession> {
  return request("/v1/flatbed/sessions", { method: "POST", body: JSON.stringify({ dpi }) });
}

export function getFlatbedSessions(): Promise<FlatbedSession[]> { return request("/v1/flatbed/sessions"); }
export function getFlatbedSession(sessionId: string): Promise<FlatbedSession> { return request(`/v1/flatbed/sessions/${sessionId}`); }

export async function uploadFlatbedSheet(sessionId: string, side: "front" | "back", image: File): Promise<{ id: string; regions: FlatbedRegion[]; failures: string[] }> {
  const form = new FormData(); form.append("image", image);
  const response = await fetch(`${multipartApiUrl()}/v1/flatbed/sessions/${sessionId}/sheets/${side}`, { method: "POST", credentials: "include", body: form });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail ?? `扫描整版上传失败 (${response.status})`); }
  return response.json();
}

export function captureFlatbedSheet(sessionId: string, side: "front" | "back", preview = false, captureId?: string): Promise<{ id: string; regions: FlatbedRegion[]; failures: string[] }> {
  // A full-bed scan regularly takes longer than Next's development rewrite
  // proxy is willing to keep the upstream response open. The API may finish
  // and persist the sheet after that proxy has already returned a misleading
  // 500, which encourages duplicate scans. Use the same direct local API
  // origin as large uploads so the browser waits for the real scan result.
  return request(
    `/v1/flatbed/sessions/${sessionId}/capture/${side}?preview=${preview}${captureId ? `&capture_id=${encodeURIComponent(captureId)}` : ""}`,
    { method: "POST" },
    multipartApiUrl(),
  );
}

export function getFlatbedCaptureProgress(captureId: string): Promise<FlatbedCaptureProgress> {
  return request(`/v1/flatbed/captures/${captureId}/status`, {}, multipartApiUrl());
}

export function proposeFlatbedPairs(sessionId: string): Promise<{ pairs: FlatbedPair[]; missing_front: string[]; missing_back: string[]; transform: string; requires_review: boolean }> {
  return request(`/v1/flatbed/sessions/${sessionId}/pair`, { method: "POST" });
}

export function confirmFlatbedPairs(sessionId: string, pairs: Array<{ front_region_id: string; back_region_id: string }>): Promise<{ status: string; count: number; skipped: { front: number; back: number } }> {
  return request(`/v1/flatbed/sessions/${sessionId}/pairs`, { method: "PUT", body: JSON.stringify({ pairs }) });
}

export function confirmFlatbedLayout(sheetId: string, regions: Array<{ points: Array<{ x: number; y: number }> }>): Promise<{ id: string; regions: FlatbedRegion[]; failures: string[] }> {
  return request(`/v1/flatbed/sheets/${sheetId}/layout`, { method: "PUT", body: JSON.stringify({ regions }) });
}

export function submitFlatbedSession(sessionId: string): Promise<{ status: string; job_ids: string[] }> {
  return request(`/v1/flatbed/sessions/${sessionId}/submit`, { method: "POST" });
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


export type CostCandidate = {
  unit_id: string; evidence_hash: string; order_id: string; ordered_at: string;
  seller: string; status: string; item_id: string; unit_index: number; title: string;
  image_urls: string[]; source_url: string; card_price: string; shipping: string;
  shipping_method?: "prepaid" | "sf_collect"; shipping_basis?: "order" | "owner_default";
  total_cost: string; currency: string; confidence: string; reasons: string[];
  missing: string[]; automatic: boolean;
  item_url?: string; match_method?: "exact" | "inferred" | "image_title_correction" | "bundle_equal_split"; inference_warnings?: string[];
  visual_inference?: {verdict: string; confidence: string; reasons: string[]};
};
export type CostPreview = { candidates: CostCandidate[]; rejected: {item_id: string; reasons: string[]}[]; reason: string };
export type CostJob = { source_mode?: "synced" | "browser_search"; item_ids?: string[]; id: string; status: string; target: number; scan_limit: number; scanned: number; succeeded: number; error?: string; results: {item_id: string; status: string; reason?: string}[] };
export type CostAudit = { card_price: string; shipping: string; confirmation: string; source_changed: boolean; manual_override: boolean; evidence: CostCandidate; current_price: string; current_shipping: string; current_currency: string };
export const getCostCandidates = (id: string): Promise<CostPreview> => request(`/v1/purchase-cost/items/${id}/candidates`);
export const confirmCost = (id: string, candidate: CostCandidate) => request(`/v1/purchase-cost/items/${id}/confirm`, {method: "POST", body: JSON.stringify({unit_id: candidate.unit_id, evidence_hash: candidate.evidence_hash, refresh:true})});
export const getCostAudit = (id: string): Promise<CostAudit | null> => request(`/v1/purchase-cost/items/${id}/audit`);
export const syncCostOrders = (orders: unknown[]): Promise<{orders: {status: string; errors: string[]}[]}> => request("/v1/purchase-cost/sync", {method: "POST", body: JSON.stringify({orders})});
export const createCostJob = (target: number, requestKey: string, sourceMode: "synced" | "browser_search" = "synced"): Promise<CostJob> => request("/v1/purchase-cost/jobs", {method: "POST", body: JSON.stringify({target, scan_limit: Math.min(2000, Math.max(100, target * 20)), request_key: requestKey, source_mode: sourceMode})});
export const getCostJob = (id: string): Promise<CostJob> => request(`/v1/purchase-cost/jobs/${id}`);
export const listCostJobs = (): Promise<CostJob[]> => request("/v1/purchase-cost/jobs");
export const actCostJob = (id: string, action: "cancel" | "retry" | "search-failed"): Promise<CostJob> => request(`/v1/purchase-cost/jobs/${id}/${action}`, {method: "POST"});

export type CostSearchPlan = {item_id: string; label: string; keywords: string[]; fallback_keywords?: string[]; status: "ready" | "skipped"; reason: string};
export type CostSearchResult = CostPreview & {status: string; recorded?: CostCandidate};
export const getCostSearchPlan = (id: string, signal?: AbortSignal): Promise<CostSearchPlan> => request(`/v1/purchase-cost/items/${id}/search-plan`, {signal});
export const resolveCostSearch = (id: string, capture: {orders: unknown[]; complete: boolean}, signal?: AbortSignal): Promise<CostSearchResult> => request(`/v1/purchase-cost/items/${id}/search-result`, {method:"POST", body:JSON.stringify({...capture, refresh:true}), signal});
export const finishCostSearchStep = (jobId: string, itemId: string, capture: {orders: unknown[]; complete: boolean}): Promise<CostJob> => request(`/v1/purchase-cost/jobs/${jobId}/search-step`, {method:"POST", body:JSON.stringify({...capture, item_id:itemId})});

export type OfficialChecklistProgress = {
  publishers?: Record<string, {total: number; eligible_total: number; counts: Record<string, number>; known_success_percent: number; date_unknown: number; quarantined_rows: number; full_window_percent: number | null}>;
  phase_progress?: { downloaded_percent: number; parsed_percent: number; published_percent: number; known_pipeline_percent: number };
  engineering_percent: number;
  known_products_percent: number;
  window?: { start: string; end: string };
  summary: { total: number; eligible_total?: number; counts: Record<string, number>; discovery_complete: boolean; success_percent: number | null; date_unknown: number };
  source_blockers: string[];
  products: { key: string; title: string; status: string; row_count?: number; issue_count?: number; release_date?: string; knowledge_version?: number; error?: string; source_url?: string }[];
};
export async function getOfficialChecklistProgress(): Promise<OfficialChecklistProgress> {
  return request<OfficialChecklistProgress>("/v1/knowledge/official-checklists/progress");
}
