"use client";

import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Camera,
  ChevronRight,
  CircleDollarSign,
  Download,
  LayoutDashboard,
  Library,
  LogOut,
  Menu,
  Plus,
  RotateCcw,
  RotateCw,
  Search,
  ScanSearch,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { GlobalImageViewer, OpenableImage } from "@/components/image-viewer";

import {
  addTransaction,
  addValuation,
  addMarketComp,
  analyzeItem,
  automaticallyAnalyzeItem,
  changePassword,
  createItem,
  getCurrentUser,
  getAuthSessions,
  loginUser,
  logoutUser,
  registerUser,
  revokeAuthSession,
  getPortfolio,
  getTrash,
  deleteItem,
  restoreItem,
  permanentlyDeleteItem,
  generateNarrative,
  syncPlayer,
  assessPotential,
  getPlayerIntelligence,
  getItemDetail,
  getCardCatalog,
  applyCatalogImages,
  getMarketComps,
  getAutomatedValuation,
  getIdentityCorrectionCounts,
  getIdentityCorrections,
  applyIdentityCorrections,
  getIdentityReview,
  confirmIdentityReview,
  researchIdentity,
  publishShadowValuation,
  retryAutomatedValuation,
  estimateMarket,
  enqueueScannerRecognition,
  getScannerJobImage,
  getScannerCameraControls,
  getScannerRecognitionJobs,
  recognizeCardAuto,
  recognizeCardBatch,
  recognizeCardPair,
  retryScannerRecognition,
  updateScannerCameraControls,
  deleteScannerRecognition,
  rotateItemImage,
  uploadItemImage,
  launchCardHobbyDesktop,
  scanCardHobbyDesktop,
  confirmCardHobbyDesktop,
  assetUrl,
  externalSourceUrl,
  type AnalysisResult,
  type AINarrativeResult,
  type PortfolioItem,
  type PortfolioSummary,
  type PlayerIntelligence,
  type ItemDetail,
  type MarketComp,
  type AutomatedValuationDetail,
  type IdentityCorrection,
  type IdentityReviewDetail,
  type ConfirmedCardIdentity,
  type CardRecognition,
  type BatchCardRecognition,
  type SheetCardRecognitionItem,
  type CardHobbyImportDraft,
  type AuthUser,
  type AuthSession,
  type ScannerRecognitionJob,
  type ScannerCameraControls,
} from "@/lib/api";

type RecognitionMode = "single" | "pair" | "scanner" | "batch";
type AcquisitionDraft = {
  purchased_at?: string | null;
  purchase_price?: string | null;
  shipping_price?: string | null;
  currency?: string | null;
  order_number?: string | null;
  product_number?: string | null;
  listing_title?: string | null;
  visible_description?: string | null;
  text_derived_fields?: Record<string, string>;
  seller_name?: string | null;
  card_image_bbox?: [number, number, number, number] | null;
};
type BatchRecognitionDraft = BatchCardRecognition & {
  file: File | null;
  acquisition?: AcquisitionDraft;
};
type EditableRecognition = CardRecognition & AcquisitionDraft;

const recognitionFieldLabels: Record<string, string> = {
  player_name: "球员",
  season_start: "赛季起始年",
  season_end: "赛季结束年",
  brand_name: "品牌",
  product_name: "产品线",
  insert_name: "Insert / 卡种",
  parallel_name: "平行版本",
  card_number: "卡号",
  serial_number: "序列编号",
  serial_total: "总限编",
  is_rookie: "RC",
  is_autograph: "签字",
  is_patch: "Patch",
};
const identityCorrectionLabels: Record<string, string> = {
  card_number: "卡号",
  insert_name: "Insert / 卡种",
  parallel_name: "平行版本",
  serial_total: "限编",
  is_autograph: "签字属性",
  is_patch: "物料属性",
};

function correctionValue(value: string | number | boolean | null) {
  if (value === null || value === "") return "未填写";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

function isEvidenceUrl(value: string) {
  return /^https?:\/\//i.test(value.trim());
}

function recognitionSourceUrls(result: CardRecognition) {
  return Array.from(
    new Set([
      ...Object.values(result.field_evidence).flat().filter(isEvidenceUrl),
      ...result.web_candidates.flatMap((candidate) => candidate.source_urls),
    ]),
  );
}

function evidenceSourceHost(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "外部来源";
  }
}

async function cropCardImage(
  source: File,
  bbox: [number, number, number, number] | null,
  index: number,
): Promise<File | null> {
  if (!bbox) return null;
  const bitmap = await createImageBitmap(source);
  try {
    const [left, top, right, bottom] = bbox;
    const x = Math.max(0, Math.floor((left / 1000) * bitmap.width));
    const y = Math.max(0, Math.floor((top / 1000) * bitmap.height));
    const width = Math.min(bitmap.width - x, Math.ceil(((right - left) / 1000) * bitmap.width));
    const height = Math.min(
      bitmap.height - y,
      Math.ceil(((bottom - top) / 1000) * bitmap.height),
    );
    if (width < 20 || height < 20) return null;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(bitmap, x, y, width, height, 0, 0, width, height);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.92),
    );
    return blob
      ? new File([blob], `recognized-card-${index + 1}.jpg`, { type: "image/jpeg" })
      : null;
  } finally {
    bitmap.close();
  }
}

function canUseOriginalAsCardImage(
  detectedCount: number,
  card: SheetCardRecognitionItem,
): boolean {
  if (detectedCount !== 1 || card.card_image_bbox) return false;
  // A single result does not mean the upload is a single-card photo. Order
  // screenshots often contain one recognized listing plus surrounding rows,
  // prices and seller information; saving the original would attach the full
  // screenshot to the card. Only preserve an un-cropped original when no
  // listing/acquisition context indicates that the source is a screenshot.
  return ![
    card.listing_title,
    card.visible_description,
    card.order_number,
    card.product_number,
    card.purchase_price,
    card.shipping_price,
    card.seller_name,
  ].some((value) => value !== null && value !== "");
}

const emptySummary: PortfolioSummary = {
  owner_id: "",
  base_currency: "CNY",
  item_count: 0,
  valued_item_count: 0,
  unvalued_item_count: 0,
  total_cost: "0",
  total_market_value: "0",
  unrealized_profit: "0",
  realized_profit: "0",
};

function money(value: string | number, currency = "CNY") {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function season(item: PortfolioItem) {
  if (!item.season_end || item.season_end === item.season_start)
    return String(item.season_start);
  return `${item.season_start}-${String(item.season_end).slice(-2)}`;
}

function isValuationRunning(status: string | null) {
  return ["queued", "identifying", "searching", "calculating"].includes(status ?? "");
}

export default function Dashboard() {
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [summary, setSummary] = useState(emptySummary);
  const [items, setItems] = useState<PortfolioItem[]>([]);
  const [selectedItemIds, setSelectedItemIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [trashItems, setTrashItems] = useState<PortfolioItem[]>([]);
  const [showTrash, setShowTrash] = useState(false);
  const [emptyingTrash, setEmptyingTrash] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [showSetup, setShowSetup] = useState(false);
  const [showAccount, setShowAccount] = useState(false);
  const [authSessions, setAuthSessions] = useState<AuthSession[]>([]);
  const [showAddCard, setShowAddCard] = useState(false);
  const [showCardHobbyImport, setShowCardHobbyImport] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [analysisItem, setAnalysisItem] = useState<PortfolioItem | null>(null);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(
    null,
  );
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [aiNarrative, setAiNarrative] = useState<AINarrativeResult | null>(
    null,
  );
  const [playerIntel, setPlayerIntel] = useState<PlayerIntelligence | null>(
    null,
  );
  const [itemDetail, setItemDetail] = useState<ItemDetail | null>(null);
  const [identityIssueCounts, setIdentityIssueCounts] = useState<
    Record<string, number>
  >({});
  const [identityCorrections, setIdentityCorrections] = useState<
    IdentityCorrection[]
  >([]);
  const [identityReview, setIdentityReview] = useState<IdentityReviewDetail | null>(null);
  const [applyingIdentityCorrections, setApplyingIdentityCorrections] =
    useState(false);
  const [itemCatalog, setItemCatalog] = useState<CardRecognition | null>(null);
  const [itemCatalogLoading, setItemCatalogLoading] = useState(false);
  const [detailAnalysis, setDetailAnalysis] = useState<AnalysisResult | null>(null);
  const [detailAnalysisLoading, setDetailAnalysisLoading] = useState(false);
  const [marketComps, setMarketComps] = useState<MarketComp[]>([]);
  const [marketItem, setMarketItem] = useState<PortfolioItem | null>(null);
  const [valuationDetail, setValuationDetail] =
    useState<AutomatedValuationDetail | null>(null);
  const [valuationDetailItem, setValuationDetailItem] =
    useState<PortfolioItem | null>(null);
  const [potentialItem, setPotentialItem] = useState<PortfolioItem | null>(
    null,
  );
  const [imageItem, setImageItem] = useState<PortfolioItem | null>(null);
  const [showRecognition, setShowRecognition] = useState(false);
  const [recognitionResult, setRecognitionResult] =
    useState<CardRecognition | null>(null);
  const [recognitionFile, setRecognitionFile] = useState<File | null>(null);
  const [recognitionBackFile, setRecognitionBackFile] = useState<File | null>(null);
  const [recognitionMode, setRecognitionMode] =
    useState<RecognitionMode>("single");
  const [batchDrafts, setBatchDrafts] = useState<BatchRecognitionDraft[]>([]);
  const [selectedBatchIndex, setSelectedBatchIndex] = useState<number | null>(null);
  const [recognitionLoading, setRecognitionLoading] = useState(false);
  const [recognitionProgress, setRecognitionProgress] = useState(0);
  const [recognitionPhase, setRecognitionPhase] = useState("");
  const [scannerJobs, setScannerJobs] = useState<ScannerRecognitionJob[]>([]);
  const [scannerResetToken, setScannerResetToken] = useState(0);
  const [selectedScannerJobId, setSelectedScannerJobId] = useState<string | null>(null);
  const [cardDraft, setCardDraft] = useState<EditableRecognition | null>(null);

  const refresh = useCallback(async (id: string) => {
    setLoading(true);
    setError("");
    try {
      const [[nextSummary, nextItems], nextIssueCounts] = await Promise.all([
        getPortfolio(id),
        getIdentityCorrectionCounts(id),
      ]);
      setSummary(nextSummary);
      setItems(nextItems);
      setIdentityIssueCounts(nextIssueCounts);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "暂时无法读取卡库");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void getCurrentUser()
      .then((user) => {
        setCurrentUser(user);
        setOwnerId(user.id);
        setShowSetup(false);
        return refresh(user.id);
      })
      .catch(() => {
        setCurrentUser(null);
        setOwnerId(null);
        setLoading(false);
        setShowSetup(true);
      });
  }, [refresh]);

  useEffect(() => {
    const expireSession = () => {
      setCurrentUser(null);
      setOwnerId(null);
      setShowSetup(true);
      setError("登录已过期，请重新登录");
    };
    window.addEventListener("card-platform:unauthorized", expireSession);
    return () => window.removeEventListener("card-platform:unauthorized", expireSession);
  }, []);

  useEffect(() => {
    if (!showRecognition || !ownerId) return;
    let active = true;
    const refreshJobs = () =>
      getScannerRecognitionJobs()
        .then((jobs) => {
          if (active) setScannerJobs(jobs);
        })
        .catch(() => undefined);
    void refreshJobs();
    const timer = window.setInterval(refreshJobs, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [showRecognition, ownerId]);

  useEffect(() => {
    if (!ownerId) return;
    const active = items.some((item) =>
      ["queued", "identifying", "searching", "calculating"].includes(
        item.valuation_status ?? "",
      ),
    );
    if (!active) return;
    const timer = window.setInterval(() => {
      void Promise.all([
        getPortfolio(ownerId),
        getIdentityCorrectionCounts(ownerId),
      ]).then(([[nextSummary, nextItems], nextIssueCounts]) => {
        setSummary(nextSummary);
        setItems(nextItems);
        setIdentityIssueCounts(nextIssueCounts);
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [items, ownerId]);

  const filteredItems = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    if (!keyword) return items;
    return items.filter((item) =>
      [item.player_name, item.brand_name, item.product_name, item.insert_name]
        .filter(Boolean)
        .some((value) => value?.toLocaleLowerCase().includes(keyword)),
    );
  }, [items, search]);

  useEffect(() => {
    const activeIds = new Set(items.map((item) => item.id));
    setSelectedItemIds((current) => {
      const next = new Set([...current].filter((id) => activeIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [items]);

  const selectedVisibleCount = filteredItems.filter((item) =>
    selectedItemIds.has(item.id),
  ).length;
  const allVisibleSelected =
    filteredItems.length > 0 && selectedVisibleCount === filteredItems.length;

  const unrealized = Number(summary.unrealized_profit);
  const returnRate = Number(summary.total_cost)
    ? (unrealized / Number(summary.total_cost)) * 100
    : 0;

  async function handleSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    try {
      const mode = String(form.get("authMode"));
      const result = mode === "register"
        ? await registerUser({
            email: String(form.get("email")),
            password: String(form.get("password")),
            display_name: String(form.get("displayName")),
            base_currency: String(form.get("currency")),
          })
        : await loginUser({
            email: String(form.get("email")),
            password: String(form.get("password")),
          });
      window.localStorage.removeItem("card-alpha-owner-id");
      setCurrentUser(result);
      setOwnerId(result.id);
      setShowSetup(false);
      await refresh(result.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建账户失败");
    }
  }

  async function handleLogout() {
    try {
      await logoutUser();
    } finally {
      setCurrentUser(null);
      setOwnerId(null);
      setItems([]);
      setSummary(emptySummary);
      setShowSetup(true);
    }
  }

  async function openAccount() {
    try {
      setAuthSessions(await getAuthSessions());
      setShowAccount(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账户信息读取失败");
    }
  }

  async function handleChangePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = String(form.get("newPassword"));
    if (next !== String(form.get("confirmPassword"))) {
      setError("两次输入的新密码不一致");
      return;
    }
    try {
      await changePassword({
        current_password: String(form.get("currentPassword")),
        new_password: next,
      });
      event.currentTarget.reset();
      setError("");
      setAuthSessions(await getAuthSessions());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "密码修改失败");
    }
  }

  async function handleRevokeSession(sessionId: string) {
    await revokeAuthSession(sessionId);
    setAuthSessions(await getAuthSessions());
  }

  async function handleAddCard(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId) throw new Error("账户信息尚未加载，请刷新页面后重试");
    const form = new FormData(event.currentTarget);
    const seasonStart = Number(form.get("seasonStart"));
    const serialTotal = String(form.get("serialTotal") ?? "");
    setError("");
    try {
      const created = await createItem({
        owner_id: ownerId,
        card: {
          player_name: form.get("playerName"),
          brand_name: form.get("brandName"),
          season_start: seasonStart,
          season_end: form.get("seasonEnd")
            ? Number(form.get("seasonEnd"))
            : seasonStart + 1,
          product_name: form.get("productName"),
          insert_name: form.get("insertName") || null,
          parallel_name: form.get("parallelName") || null,
          card_number: form.get("cardNumber") || null,
          is_rookie: form.get("isRookie") === "on",
          is_autograph: form.get("isAutograph") === "on",
          is_patch: form.get("isPatch") === "on",
          print_run: serialTotal ? Number(serialTotal) : null,
        },
        serial_number: form.get("serialNumber")
          ? Number(form.get("serialNumber"))
          : null,
        serial_total: serialTotal ? Number(serialTotal) : null,
        strategy_tags: form.getAll("strategyTags"),
        purchase: {
          transacted_at: String(form.get("purchasedAt") || new Date().toISOString()),
          price: form.get("price"),
          currency: form.get("currency"),
          fx_rate_to_base: 1,
          fee_amount: form.get("shippingPrice") || 0,
          venue: form.get("sellerName") || null,
          notes: form.get("purchaseNotes") || null,
        },
      });
      if (cardDraft && recognitionFile) {
        await uploadItemImage(created.id, ownerId, "front", recognitionFile);
      }
      if (cardDraft && recognitionBackFile) {
        await uploadItemImage(created.id, ownerId, "back", recognitionBackFile);
      }
      const consumedScannerJobId = selectedScannerJobId;
      let remainingScannerJobs: ScannerRecognitionJob[] | null = null;
      let scannerCleanupError = "";
      if (consumedScannerJobId) {
        try {
          await deleteScannerRecognition(consumedScannerJobId);
          remainingScannerJobs = await getScannerRecognitionJobs().catch(() =>
            scannerJobs.filter((job) => job.id !== consumedScannerJobId),
          );
        } catch (cleanupError) {
          remainingScannerJobs = scannerJobs.filter((job) => job.id !== consumedScannerJobId);
          scannerCleanupError = cleanupError instanceof Error
            ? `卡片已录入，但队列清理失败：${cleanupError.message}`
            : "卡片已录入，但队列清理失败";
        }
        setScannerJobs(remainingScannerJobs);
        setSelectedScannerJobId(null);
      }
      setShowAddCard(false);
      setCardDraft(null);
      if (consumedScannerJobId) {
        const nextScannerJob = remainingScannerJobs?.find(
          (job) => job.status === "succeeded" && job.result,
        );
        setBatchDrafts([]);
        setSelectedBatchIndex(null);
        setRecognitionResult(null);
        setRecognitionFile(null);
        setRecognitionBackFile(null);
        setRecognitionMode("scanner");
        setShowRecognition((remainingScannerJobs?.length ?? 0) > 0);
        if (nextScannerJob) {
          await openScannerJob(nextScannerJob);
        }
        if (scannerCleanupError) setError(scannerCleanupError);
      } else if (selectedBatchIndex !== null && batchDrafts.length > 1) {
        const remaining = batchDrafts.filter((_, index) => index !== selectedBatchIndex);
        const nextIndex = remaining.findIndex((draft) => draft.result !== null);
        setBatchDrafts(remaining);
        setSelectedBatchIndex(nextIndex >= 0 ? nextIndex : null);
        setRecognitionResult(nextIndex >= 0 ? remaining[nextIndex].result : null);
        setRecognitionFile(nextIndex >= 0 ? remaining[nextIndex].file : null);
        setRecognitionBackFile(null);
        setShowRecognition(remaining.length > 0);
      } else {
        setBatchDrafts([]);
        setSelectedBatchIndex(null);
        setRecognitionResult(null);
        setRecognitionFile(null);
        setRecognitionBackFile(null);
      }
      await refresh(ownerId);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "录入卡片失败";
      setError(message);
      throw new Error(message);
    }
  }

  async function handleAnalysis(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId || !analysisItem) return;
    const form = new FormData(event.currentTarget);
    const value = (name: string) => Number(form.get(name));
    setError("");
    try {
      const result = await analyzeItem(analysisItem.id, {
        owner_id: ownerId,
        asking_price: value("askingPrice"),
        currency: summary.base_currency,
        fair_value_low: value("fairValueLow"),
        fair_value_high: value("fairValueHigh"),
        evidence_count: value("evidenceCount"),
        factors: {
          historical_standing: value("historicalStanding"),
          current_performance: value("currentPerformance"),
          age_potential: value("agePotential"),
          card_scarcity: value("cardScarcity"),
          brand_tier: value("brandTier"),
          market_heat: value("marketHeat"),
        },
      });
      setAnalysisResult(result);
      setAiNarrative(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "分析失败");
    }
  }

  async function handleNarrative() {
    if (!analysisResult) return;
    setError("");
    try {
      setAiNarrative(await generateNarrative(analysisResult.analysis_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI 解读生成失败");
    }
  }

  async function handleAutomaticAnalysis(item: PortfolioItem) {
    if (!ownerId) return;
    setAnalysisItem(item);
    setAnalysisResult(null);
    setAiNarrative(null);
    setAnalysisLoading(true);
    setError("");
    try {
      setAnalysisResult(
        await automaticallyAnalyzeItem(item.id, {
          owner_id: ownerId,
          asking_price: Number(item.fair_value ?? item.remaining_cost),
          currency: summary.base_currency,
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "自动分析失败");
    } finally {
      setAnalysisLoading(false);
    }
  }

  async function openPlayerIntelligence(playerId: string) {
    setError("");
    try {
      setPlayerIntel(await getPlayerIntelligence(playerId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "球员情报读取失败");
    }
  }

  async function handlePlayerSync(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!playerIntel) return;
    const form = new FormData(event.currentTarget);
    try {
      await syncPlayer(playerIntel.player_id, Number(form.get("nbaPersonId")));
      setPlayerIntel(await getPlayerIntelligence(playerIntel.player_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "球员数据同步失败");
    }
  }

  async function handlePotential(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!potentialItem) return;
    const form = new FormData(event.currentTarget);
    const value = (name: string) => Number(form.get(name));
    try {
      await assessPotential(potentialItem.player_id, {
        age: value("age"),
        performance_score: value("performanceScore"),
        performance_trend: value("performanceTrend"),
        availability_score: value("availabilityScore"),
        role_score: value("roleScore"),
        team_environment_score: value("teamEnvironmentScore"),
      });
      const playerId = potentialItem.player_id;
      setPotentialItem(null);
      setPlayerIntel(await getPlayerIntelligence(playerId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "潜力评估失败");
    }
  }

  async function handleRecognition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    setRecognitionLoading(true);
    setRecognitionProgress(8);
    setRecognitionPhase("正在上传并校验图片");
    if (recognitionMode !== "scanner") setSelectedScannerJobId(null);
    const progressTimer = window.setInterval(() => {
      setRecognitionProgress((current) => {
        const next = Math.min(current + (current < 45 ? 7 : 3), 90);
        if (next >= 62) setRecognitionPhase("正在联网核验目录与候选");
        else if (next >= 24) setRecognitionPhase("Qwen3-VL 正在提取卡面信息");
        return next;
      });
    }, 900);
    try {
      if (recognitionMode === "single") {
        const file = form.get("recognitionImage");
        if (!(file instanceof File) || !file.size) return;
        setRecognitionFile(file);
        setRecognitionBackFile(null);
        const auto = await recognizeCardAuto(file);
        const drafts = await Promise.all(
          auto.cards.map(async (card: SheetCardRecognitionItem, index) => {
            const cropped = await cropCardImage(file, card.card_image_bbox, index);
            return {
              filename: auto.detected_count === 1 ? file.name : `卡牌 ${index + 1}`,
              result: card.result,
              error: null,
              file: cropped ?? (canUseOriginalAsCardImage(auto.detected_count, card) ? file : null),
              acquisition: card,
            };
          }),
        );
        setBatchDrafts(drafts);
        if (drafts.length === 0) {
          setSelectedBatchIndex(null);
          setRecognitionResult(null);
          throw new Error("图片中未识别到球星卡，请更换清晰图片后重试");
        }
        setSelectedBatchIndex(0);
        setRecognitionResult(drafts[0].result);
      } else if (recognitionMode === "pair") {
        const front = form.get("frontImage");
        const back = form.get("backImage");
        if (
          !(front instanceof File) ||
          !front.size ||
          !(back instanceof File) ||
          !back.size
        )
          return;
        setRecognitionFile(front);
        setRecognitionBackFile(back);
        setRecognitionResult(await recognizeCardPair(front, back));
      } else if (recognitionMode === "scanner") {
        const front = form.get("frontImage");
        const back = form.get("backImage");
        const frontRaw = form.get("frontRawImage");
        const backRaw = form.get("backRawImage");
        if (
          !(front instanceof File) ||
          !front.size ||
          !(back instanceof File) ||
          !back.size
        )
          return;
        setRecognitionPhase("正在自动裁切并加入后台队列");
        const job = await enqueueScannerRecognition(
          front,
          back,
          frontRaw instanceof File && frontRaw.size ? frontRaw : undefined,
          backRaw instanceof File && backRaw.size ? backRaw : undefined,
        );
        setScannerJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
        setScannerResetToken((current) => current + 1);
        setRecognitionProgress(100);
        setRecognitionPhase("已加入后台队列，可以继续拍下一张");
      } else {
        const input = event.currentTarget.elements.namedItem("batchImages");
        if (!(input instanceof HTMLInputElement) || !input.files?.length) return;
        const files = Array.from(input.files);
        const results = await recognizeCardBatch(files);
        setBatchDrafts(results.map((result, index) => ({ ...result, file: files[index] })));
        setRecognitionResult(null);
        setSelectedBatchIndex(null);
      }
      if (recognitionMode !== "scanner") {
        setRecognitionProgress(100);
        setRecognitionPhase("识别完成，正在整理结果");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "卡片图片识别失败");
      setRecognitionPhase("识别失败");
    } finally {
      window.clearInterval(progressTimer);
      window.setTimeout(() => setRecognitionLoading(false), 350);
    }
  }

  async function openScannerJob(job: ScannerRecognitionJob) {
    if (!job.result) return;
    try {
      const [front, back] = await Promise.all([
        getScannerJobImage(job.front_image_url, "front"),
        getScannerJobImage(job.back_image_url, "back"),
      ]);
      setRecognitionFile(front);
      setRecognitionBackFile(back);
      setRecognitionResult(job.result);
      setSelectedScannerJobId(job.id);
      setSelectedBatchIndex(null);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "扫描结果读取失败");
    }
  }

  async function retryScannerJob(jobId: string) {
    try {
      const job = await retryScannerRecognition(jobId);
      setScannerJobs((current) => current.map((item) => item.id === job.id ? job : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务重试失败");
    }
  }

  async function deleteScannerJob(jobId: string) {
    const job = scannerJobs.find((item) => item.id === jobId);
    const label = job?.result?.player_name || "这条扫描任务";
    if (!window.confirm(`确定删除 ${label}？相关扫描图片也会一并删除。`)) return;
    try {
      await deleteScannerRecognition(jobId);
      setScannerJobs((current) => current.filter((item) => item.id !== jobId));
      if (selectedScannerJobId === jobId) {
        setSelectedScannerJobId(null);
        setRecognitionResult(null);
        setRecognitionFile(null);
        setRecognitionBackFile(null);
        setCardDraft(null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "扫描任务删除失败");
    }
  }

  function selectBatchDraft(index: number) {
    const draft = batchDrafts[index];
    if (!draft?.result) return;
    setSelectedBatchIndex(index);
    setRecognitionFile(draft.file);
    setRecognitionBackFile(null);
    setSelectedScannerJobId(null);
    setRecognitionResult(draft.result);
  }

  function handleUseRecognition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!recognitionResult) return;
    const form = new FormData(event.currentTarget);
    const optionalNumber = (name: string) => {
      const value = String(form.get(name) ?? "").trim();
      return value ? Number(value) : null;
    };
    setCardDraft({
      ...recognitionResult,
      ...(selectedBatchIndex !== null
        ? batchDrafts[selectedBatchIndex]?.acquisition
        : {}),
      player_name: String(form.get("playerName") || "").trim() || null,
      season_start: optionalNumber("seasonStart"),
      season_end: optionalNumber("seasonEnd"),
      brand_name: String(form.get("brandName") || "").trim() || null,
      product_name: String(form.get("productName") || "").trim() || null,
      insert_name: String(form.get("insertName") || "").trim() || null,
      parallel_name: String(form.get("parallelName") || "").trim() || null,
      card_number: String(form.get("cardNumber") || "").trim() || null,
      serial_number: optionalNumber("serialNumber"),
      serial_total: optionalNumber("serialTotal"),
      is_rookie: form.get("isRookie") === "on",
      is_autograph: form.get("isAutograph") === "on",
      is_patch: form.get("isPatch") === "on",
    });
    setShowRecognition(false);
    setShowAddCard(true);
  }

  async function handleImageUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId || !imageItem) return;
    const form = new FormData(event.currentTarget);
    const front = form.get("frontImage");
    const back = form.get("backImage");
    try {
      if (front instanceof File && front.size) {
        await uploadItemImage(imageItem.id, ownerId, "front", front);
      }
      if (back instanceof File && back.size) {
        await uploadItemImage(imageItem.id, ownerId, "back", back);
      }
      await refresh(ownerId);
      setImageItem(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "卡片图片上传失败");
    }
  }

  async function openItemDetail(itemId: string) {
    if (!ownerId) return;
    setError("");
    setItemCatalog(null);
    setDetailAnalysis(null);
    try {
      const [detail, corrections, review] = await Promise.all([
        getItemDetail(itemId, ownerId),
        getIdentityCorrections(itemId, ownerId),
        getIdentityReview(itemId),
      ]);
      setItemDetail(detail);
      setIdentityCorrections(corrections);
      setIdentityReview(review);
      setItemCatalogLoading(true);
      void getCardCatalog(itemId, ownerId)
        .then(setItemCatalog)
        .catch((reason) =>
          setError(reason instanceof Error ? reason.message : "图鉴补全失败"),
        )
        .finally(() => setItemCatalogLoading(false));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "持仓详情读取失败");
    }
  }

  async function confirmReviewedIdentity(identity: ConfirmedCardIdentity, reason: string) {
    if (!itemDetail) return;
    setApplyingIdentityCorrections(true);
    setError("");
    try {
      await confirmIdentityReview(itemDetail.item.id, identity, reason);
      setIdentityReview(await getIdentityReview(itemDetail.item.id));
      await refresh(ownerId!);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "身份确认失败");
      throw reason;
    } finally {
      setApplyingIdentityCorrections(false);
    }
  }

  async function runIdentityResearch() {
    if (!itemDetail) return;
    setApplyingIdentityCorrections(true);
    try {
      await researchIdentity(itemDetail.item.id);
      setIdentityReview(await getIdentityReview(itemDetail.item.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "身份研究失败");
    } finally {
      setApplyingIdentityCorrections(false);
    }
  }

  async function publishReviewedValuation() {
    if (!itemDetail || !ownerId) return;
    setApplyingIdentityCorrections(true);
    setError("");
    try {
      await publishShadowValuation(itemDetail.item.id);
      await refresh(ownerId);
      setItemDetail(null);
      setItemCatalog(null);
      setDetailAnalysis(null);
      setIdentityCorrections([]);
      setIdentityReview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新正式估值失败");
    } finally {
      setApplyingIdentityCorrections(false);
    }
  }

  async function repairIdentity() {
    if (!ownerId || !itemDetail || identityCorrections.length === 0) return;
    setApplyingIdentityCorrections(true);
    setError("");
    try {
      await applyIdentityCorrections(
        itemDetail.item.id,
        ownerId,
        identityCorrections.map((item) => item.id),
      );
      const [detail, corrections] = await Promise.all([
        getItemDetail(itemDetail.item.id, ownerId),
        getIdentityCorrections(itemDetail.item.id, ownerId),
      ]);
      setItemDetail(detail);
      setIdentityCorrections(corrections);
      await refresh(ownerId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "卡片信息修复失败");
    } finally {
      setApplyingIdentityCorrections(false);
    }
  }

  async function refreshItemCatalog() {
    if (!ownerId || !itemDetail) return;
    setItemCatalogLoading(true);
    setError("");
    try {
      setItemCatalog(await getCardCatalog(itemDetail.item.id, ownerId, true));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "图鉴补全失败");
    } finally {
      setItemCatalogLoading(false);
    }
  }

  async function replaceWithCatalogImages(side: "front" | "back" | "both") {
    if (!ownerId || !itemDetail) return;
    setError("");
    try {
      await applyCatalogImages(itemDetail.item.id, ownerId, side);
      const next = await getItemDetail(itemDetail.item.id, ownerId);
      setItemDetail(next);
      await refresh(ownerId);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "高清图片替换失败";
      setError(message);
      throw reason;
    }
  }

  async function rotateDetailImage(side: "front" | "back", degrees: 90 | 270) {
    if (!ownerId || !itemDetail) return;
    setError("");
    try {
      await rotateItemImage(itemDetail.item.id, side, degrees);
      const next = await getItemDetail(itemDetail.item.id, ownerId);
      setItemDetail(next);
      await refresh(ownerId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "图片旋转失败");
      throw reason;
    }
  }

  async function analyzeItemDetail() {
    if (!ownerId || !itemDetail) return;
    setDetailAnalysisLoading(true);
    setError("");
    try {
      setDetailAnalysis(
        await automaticallyAnalyzeItem(itemDetail.item.id, {
          owner_id: ownerId,
          asking_price: Number(
            itemDetail.item.fair_value ?? itemDetail.item.remaining_cost,
          ),
          currency: summary.base_currency,
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "投资建议生成失败");
    } finally {
      setDetailAnalysisLoading(false);
    }
  }

  async function handleMarketComp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId || !marketItem) return;
    const form = new FormData(event.currentTarget);
    try {
      await addMarketComp(marketItem.id, {
        owner_id: ownerId,
        occurred_at: `${String(form.get("occurredAt"))}T12:00:00Z`,
        sale_price: Number(form.get("salePrice")),
        shipping_price: Number(form.get("shippingPrice")),
        currency: String(form.get("compCurrency")),
        fx_rate_to_base: Number(form.get("compFxRate")),
        listing_title: form.get("listingTitle") || null,
        source_url: form.get("sourceUrl") || null,
        source_code: String(form.get("sourceCode") || "market_import"),
        external_id: form.get("externalId") || null,
        origin_source_code: form.get("originSourceCode") || null,
        source_reliability: Number(form.get("sourceReliability")) / 100,
        match_confidence: Number(form.get("matchConfidence")) / 100,
      });
      setMarketComps(await getMarketComps(marketItem.id, ownerId));
      event.currentTarget.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "成交证据保存失败");
    }
  }

  async function handleMarketEstimate() {
    if (!ownerId || !marketItem) return;
    try {
      await estimateMarket(marketItem.id, {
        owner_id: ownerId,
        currency: summary.base_currency,
      });
      await refresh(ownerId);
      setMarketItem(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "自动估值失败");
    }
  }

  async function openMarket(item: PortfolioItem) {
    if (!ownerId) return;
    try {
      setMarketComps(await getMarketComps(item.id, ownerId));
      setMarketItem(item);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "成交证据读取失败");
    }
  }

  async function openValuationDetail(item: PortfolioItem) {
    if (!ownerId) return;
    try {
      setValuationDetailItem(item);
      setValuationDetail(await getAutomatedValuation(item.id, ownerId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "估值详情读取失败");
    }
  }

  async function handleValuationRetry() {
    if (!ownerId || !valuationDetailItem) return;
    const itemId = valuationDetailItem.id;
    try {
      setError("");
      const job = await retryAutomatedValuation(itemId, ownerId);
      setItems((current) =>
        current.map((item) =>
          item.id === itemId
            ? {
                ...item,
                valuation_status: "queued",
                valuation_job_id: job.id,
                valuation_error: null,
              }
            : item,
        ),
      );
      setValuationDetailItem(null);
      setValuationDetail(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重新估值失败");
      setValuationDetail(await getAutomatedValuation(itemId, ownerId).catch(() => null));
    }
  }

  async function handleTransaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId || !itemDetail) return;
    const form = new FormData(event.currentTarget);
    try {
      await addTransaction(itemDetail.item.id, ownerId, {
        transaction_type: form.get("transactionType"),
        transacted_at: new Date().toISOString(),
        quantity: Number(form.get("quantity")),
        unit_price: Number(form.get("unitPrice")),
        fee_amount: Number(form.get("feeAmount")),
        currency: summary.base_currency,
        fx_rate_to_base: 1,
        venue: form.get("venue") || null,
      });
      const next = await getItemDetail(itemDetail.item.id, ownerId);
      setItemDetail(next);
      await refresh(ownerId);
      event.currentTarget.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "交易记录失败");
    }
  }

  async function handleValuation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId || !itemDetail) return;
    const form = new FormData(event.currentTarget);
    try {
      await addValuation(itemDetail.item.id, ownerId, {
        method: "manual",
        currency: summary.base_currency,
        low_value: Number(form.get("lowValue")),
        fair_value: Number(form.get("fairValue")),
        high_value: Number(form.get("highValue")),
        confidence: Number(form.get("confidence")) / 100,
      });
      const next = await getItemDetail(itemDetail.item.id, ownerId);
      setItemDetail(next);
      await refresh(ownerId);
      event.currentTarget.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "估值记录失败");
    }
  }

  async function openTrash() {
    if (!ownerId) return;
    try {
      setTrashItems(await getTrash(ownerId));
      setShowTrash(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "回收站读取失败");
    }
  }

  async function handleDeleteItem(item: PortfolioItem) {
    if (!ownerId) return;
    if (!window.confirm(`确定删除 ${item.player_name}？卡片将进入回收站，可稍后恢复。`)) {
      return;
    }
    try {
      await deleteItem(item.id, ownerId);
      setItemDetail(null);
      await refresh(ownerId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除卡片失败");
    }
  }

  function toggleItemSelection(itemId: string) {
    setSelectedItemIds((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  function toggleVisibleSelection() {
    setSelectedItemIds((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        filteredItems.forEach((item) => next.delete(item.id));
      } else {
        filteredItems.forEach((item) => next.add(item.id));
      }
      return next;
    });
  }

  async function handleBulkDelete() {
    if (!ownerId || selectedItemIds.size === 0 || bulkDeleting) return;
    const itemIds = [...selectedItemIds];
    if (
      !window.confirm(
        `确定删除选中的 ${itemIds.length} 张卡片？它们将进入回收站，可稍后恢复。`,
      )
    ) {
      return;
    }
    setBulkDeleting(true);
    setError("");
    const results = await Promise.allSettled(
      itemIds.map((itemId) => deleteItem(itemId, ownerId, "批量删除")),
    );
    const failedIds = itemIds.filter((_, index) => results[index].status === "rejected");
    setSelectedItemIds(new Set(failedIds));
    setItemDetail(null);
    await refresh(ownerId);
    if (failedIds.length > 0) {
      setError(`${itemIds.length - failedIds.length} 张已移入回收站，${failedIds.length} 张删除失败，请重试。`);
    }
    setBulkDeleting(false);
  }

  async function handleRestoreItem(item: PortfolioItem) {
    if (!ownerId) return;
    try {
      await restoreItem(item.id, ownerId);
      setTrashItems(await getTrash(ownerId));
      await refresh(ownerId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "恢复卡片失败");
    }
  }

  async function handlePermanentDelete(item: PortfolioItem) {
    if (!ownerId) return;
    const first = window.confirm(`永久删除 ${item.player_name}？交易、估值和分析数据将一并清除。`);
    if (!first || !window.confirm("此操作不可恢复。请再次确认永久删除。")) return;
    try {
      await permanentlyDeleteItem(item.id, ownerId);
      setTrashItems(await getTrash(ownerId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "永久删除失败");
    }
  }

  async function handleEmptyTrash() {
    if (!ownerId || trashItems.length === 0 || emptyingTrash) return;
    const count = trashItems.length;
    const first = window.confirm(
      `确定永久删除回收站中的全部 ${count} 张卡片？相关交易、估值和分析数据将一并清除。`,
    );
    if (!first || !window.confirm("此操作不可恢复。请再次确认清空回收站。")) return;

    setEmptyingTrash(true);
    setError("");
    const results = await Promise.allSettled(
      trashItems.map((item) => permanentlyDeleteItem(item.id, ownerId)),
    );
    const failedCount = results.filter((result) => result.status === "rejected").length;
    const remainingItems = await getTrash(ownerId).catch(() =>
      trashItems.filter((_, index) => results[index].status === "rejected"),
    );
    setTrashItems(remainingItems);
    if (failedCount > 0) {
      setError(`${count - failedCount} 张已永久删除，${failedCount} 张删除失败，请重试。`);
    }
    setEmptyingTrash(false);
  }

  return (
    <main className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
        <div className="brand-mark">
          <span className="brand-ball">CA</span>
          <span>Card Alpha</span>
          <button
            className="icon-button sidebar-close"
            onClick={() => setSidebarOpen(false)}
          >
            <X size={18} />
          </button>
        </div>
        <nav aria-label="主导航">
          <a className="nav-item active" href="#overview">
            <LayoutDashboard size={18} /> 资产总览
          </a>
          <a className="nav-item" href="#collection">
            <BarChart3 size={18} /> 我的卡库 <span>{summary.item_count}</span>
          </a>
          <a className="nav-item" href="#analysis">
            <Activity size={18} /> 球员分析
          </a>
          <a className="nav-item" href="#ai">
            <Sparkles size={18} /> AI 估值
          </a>
          {currentUser?.role === "admin" && (
            <>
              <a className="nav-item" href="/knowledge">
                <Library size={18} /> 知识库管理
              </a>
              <a className="nav-item" href="/benchmarks">
                <ScanSearch size={18} /> 识别盲测
              </a>
              <a className="nav-item" href="/market-data">
                <ShieldAlert size={18} /> 成交复核
              </a>
            </>
          )}
          <button className="nav-item" type="button" onClick={() => void openTrash()}>
            <Trash2 size={18} /> 回收站
          </button>
        </nav>
        <div className="sidebar-insight">
          <div className="eyebrow">
            <ShieldCheck size={14} /> 投资纪律
          </div>
          <strong>价格不是价值</strong>
          <p>记录买入逻辑、催化剂与退出条件，持续复盘每一次决策。</p>
        </div>
        <div className="user-chip">
          <div className="avatar">{currentUser?.display_name.slice(0, 1) || "投"}</div>
          <div>
            <strong>{currentUser?.display_name || "我的组合"}</strong>
            <small>{currentUser?.email || `${summary.base_currency} 基准`}</small>
          </div>
          {currentUser && (
            <>
              <button className="icon-button" type="button" aria-label="账户设置" onClick={() => void openAccount()}>
                <ChevronRight size={16} />
              </button>
              <button className="icon-button" type="button" aria-label="退出登录" onClick={() => void handleLogout()}>
                <LogOut size={16} />
              </button>
            </>
          )}
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu size={20} />
          </button>
          <div className="search-box">
            <Search size={17} />
            <input
              aria-label="搜索球员或卡片"
              placeholder="搜索球员、品牌或卡种"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <kbd>⌘ K</kbd>
          </div>
          <div className="top-actions">
            <button
              className="ghost-button"
              onClick={() => setShowCardHobbyImport(true)}
              disabled={!ownerId}
            >
              <Download size={15} /> 卡淘导入
            </button>
            <button
              className="ghost-button"
              onClick={() => {
                setError("");
                setShowRecognition(true);
                setRecognitionMode("single");
                setRecognitionResult(null);
                setRecognitionFile(null);
                setRecognitionBackFile(null);
                setBatchDrafts([]);
                setSelectedBatchIndex(null);
              }}
            >
              <Sparkles size={15} /> 图片识别
            </button>
            <button
              className="primary-button"
              onClick={() => {
                setCardDraft(null);
                setRecognitionFile(null);
                setRecognitionBackFile(null);
                setShowAddCard(true);
              }}
              disabled={!ownerId}
            >
              <Plus size={17} /> 录入卡片
            </button>
          </div>
        </header>

        <div className="content" id="overview">
          <div className="page-heading">
            <div>
              <p className="eyebrow">PORTFOLIO / 组合视图</p>
              <h1>让每张卡，都有投资逻辑。</h1>
              <p>追踪成本、价值和催化剂，把收藏升级为可复盘的资产组合。</p>
            </div>
            <div className={`system-status ${error ? "warning" : ""}`}>
              <span /> {error ? "数据服务未连接" : "资产数据已同步"}
            </div>
          </div>

          {error && (
            <div className="error-banner">
              {error}。请确认 API 和数据库已启动。
            </div>
          )}

          <section className="metric-grid" aria-label="组合指标">
            <article className="metric-card hero-metric">
              <p>当前总市值</p>
              <strong>
                {loading
                  ? "—"
                  : money(summary.total_market_value, summary.base_currency)}
              </strong>
              <div className="metric-foot">
                <span className={unrealized >= 0 ? "positive" : "negative"}>
                  {unrealized >= 0 ? (
                    <ArrowUpRight size={15} />
                  ) : (
                    <ArrowDownRight size={15} />
                  )}
                  {returnRate.toFixed(1)}%
                </span>
                <small>
                  {summary.unvalued_item_count > 0
                    ? `${summary.unvalued_item_count} 张按成本暂估`
                    : "相对持仓成本"}
                </small>
              </div>
            </article>
            <article className="metric-card">
              <p>持仓成本</p>
              <strong>
                {money(summary.total_cost, summary.base_currency)}
              </strong>
              <small>{summary.item_count} 张资产卡</small>
            </article>
            <article className="metric-card">
              <p>未实现盈亏</p>
              <strong
                className={unrealized >= 0 ? "positive-text" : "negative-text"}
              >
                {money(summary.unrealized_profit, summary.base_currency)}
              </strong>
              <small>{summary.valued_item_count} 张已完成估值</small>
            </article>
            <article className="metric-card">
              <p>待补充估值</p>
              <strong>{summary.unvalued_item_count}</strong>
              <small>完善估值后组合更准确</small>
            </article>
          </section>

          <section className="collection-panel" id="collection">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">COLLECTION</p>
                <h2>核心持仓</h2>
              </div>
              <div className="collection-actions">
                {selectedItemIds.size > 0 && (
                  <button
                    className="danger-button bulk-delete-button"
                    type="button"
                    disabled={bulkDeleting}
                    onClick={() => void handleBulkDelete()}
                  >
                    <Trash2 size={14} />
                    {bulkDeleting ? "删除中…" : `删除选中 (${selectedItemIds.size})`}
                  </button>
                )}
                <button className="text-button" onClick={() => void openTrash()}>
                  回收站 <Trash2 size={16} />
                </button>
              </div>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="selection-column">
                      <input
                        type="checkbox"
                        aria-label="全选当前列表"
                        checked={allVisibleSelected}
                        disabled={filteredItems.length === 0 || bulkDeleting}
                        onChange={toggleVisibleSelection}
                      />
                    </th>
                    <th>卡片资产</th>
                    <th>属性</th>
                    <th>持仓成本</th>
                    <th>当前估值</th>
                    <th>未实现盈亏</th>
                    <th>分析</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((item) => {
                    const profit = Number(item.unrealized_profit ?? 0);
                    return (
                      <tr key={item.id}>
                        <td className="selection-column">
                          <input
                            type="checkbox"
                            aria-label={`选择 ${item.player_name}`}
                            checked={selectedItemIds.has(item.id)}
                            disabled={bulkDeleting}
                            onChange={() => toggleItemSelection(item.id)}
                          />
                        </td>
                        <td>
                          <div className="card-identity">
                            {item.image_front_url ? (
                              <div className="card-thumb has-image">
                                <OpenableImage
                                  src={
                                    assetUrl(item.image_front_url) ?? undefined
                                  }
                                  alt={`${item.player_name} 卡片正面`}
                                />
                              </div>
                            ) : (
                              <button
                                type="button"
                                className="card-thumb"
                                onClick={() => void openItemDetail(item.id)}
                                aria-label={`查看 ${item.player_name} 卡片详情`}
                              >
                                {item.player_name
                                  .split(" ")
                                  .map((part) => part[0])
                                  .join("")
                                  .slice(0, 2)}
                              </button>
                            )}
                            <div>
                              <button
                                className="player-link"
                                onClick={() => void openItemDetail(item.id)}
                              >
                                {item.player_name}
                              </button>
                              <small>
                                {season(item)} · {item.product_name}
                                {item.insert_name
                                  ? ` · ${item.insert_name}`
                                  : ""}
                              </small>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="tag-row">
                            {item.is_rookie && (
                              <span className="tag lime">RC</span>
                            )}
                            {item.is_autograph && (
                              <span className="tag">AUTO</span>
                            )}
                            {item.is_patch && (
                              <span className="tag">PATCH</span>
                            )}
                            {item.serial_total && (
                              <span className="tag muted">
                                /{item.serial_total}
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          {money(item.remaining_cost, summary.base_currency)}
                        </td>
                        <td>
                          {item.fair_value && item.valuation_status === "succeeded" ? (
                            <button
                              className="valuation-price"
                              onClick={() => void openValuationDetail(item)}
                            >
                              {money(
                                item.fair_value,
                                item.valuation_currency ?? summary.base_currency,
                              )}
                            </button>
                          ) : isValuationRunning(item.valuation_status) ? (
                            <button
                              className="valuation-status running"
                              onClick={() => void openValuationDetail(item)}
                            >
                              <span className="valuation-spinner" /> 更新中
                            </button>
                          ) : item.valuation_status === "insufficient_evidence" ? (
                            <button
                              className="valuation-status insufficient"
                              onClick={() => void openValuationDetail(item)}
                            >
                              证据不足
                            </button>
                          ) : item.valuation_status === "failed" ? (
                            <button
                              className="valuation-status failed"
                              onClick={() => void openValuationDetail(item)}
                            >
                              估值失败
                            </button>
                          ) : item.fair_value ? (
                            <button
                              className="valuation-price"
                              onClick={() => void openValuationDetail(item)}
                            >
                              {money(
                                item.fair_value,
                                item.valuation_currency ?? summary.base_currency,
                              )}
                            </button>
                          ) : (
                            <span className="unvalued">待估值</span>
                          )}
                        </td>
                        <td
                          className={
                            profit >= 0 ? "positive-text" : "negative-text"
                          }
                        >
                          {item.unrealized_profit
                            ? money(
                                item.unrealized_profit,
                                summary.base_currency,
                              )
                            : "—"}
                        </td>
                        <td>
                          <div className="action-row">
                            <button
                              className="analysis-button"
                              onClick={() => void handleAutomaticAnalysis(item)}
                            >
                              <Sparkles size={13} /> 分析
                            </button>
                            <button
                              className="manage-button"
                              onClick={() => setPotentialItem(item)}
                            >
                              潜力
                            </button>
                            <button
                              className="manage-button"
                              onClick={() => void openMarket(item)}
                            >
                              市场
                            </button>
                            <button
                              className="manage-button"
                              onClick={() => setImageItem(item)}
                            >
                              图片
                            </button>
                            <button
                              className="manage-button detail-alert-button"
                              onClick={() => void openItemDetail(item.id)}
                            >
                              详情
                              {(identityIssueCounts[item.id] ?? 0) > 0 && (
                                <span
                                  className="identity-issue-dot"
                                  aria-label={`${identityIssueCounts[item.id]} 条卡片信息修复建议`}
                                />
                              )}
                            </button>
                            <button
                              className="manage-button danger"
                              onClick={() => void handleDeleteItem(item)}
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {!loading && filteredItems.length === 0 && (
                    <tr>
                      <td colSpan={7}>
                        <div className="empty-state">
                          <CircleDollarSign size={28} />
                          <strong>
                            {search
                              ? "没有匹配的卡片"
                              : "从第一张卡开始建立组合"}
                          </strong>
                          <p>
                            {search
                              ? "换一个球员或品牌关键词试试。"
                              : "记录成本和卡片属性，系统会为你建立投资账本。"}
                          </p>
                          {!search && (
                            <button
                              className="secondary-button"
                              onClick={() => {
                                setCardDraft(null);
                                setRecognitionFile(null);
                                setRecognitionBackFile(null);
                                setShowAddCard(true);
                              }}
                            >
                              录入第一张卡
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="strategy-strip" id="analysis">
            <div>
              <p className="eyebrow">YOUR EDGE</p>
              <h2>你的收藏策略</h2>
            </div>
            <div className="strategy-card">
              <span>01</span>
              <strong>高端品牌</strong>
              <small>寻找低价球员资产</small>
            </div>
            <div className="strategy-card">
              <span>02</span>
              <strong>RC 潜力股</strong>
              <small>关注成长与上场机会</small>
            </div>
            <div className="strategy-card">
              <span>03</span>
              <strong>历史球星</strong>
              <small>配置长期共识资产</small>
            </div>
          </section>
        </div>
      </section>

      {showSetup && <SetupDialog onSubmit={handleSetup} error={error} />}
      {showAccount && currentUser && (
        <AccountDialog
          user={currentUser}
          sessions={authSessions}
          error={error}
          onClose={() => setShowAccount(false)}
          onChangePassword={handleChangePassword}
          onRevokeSession={(id) => void handleRevokeSession(id)}
        />
      )}
      {showAddCard && (
        <CardDialog
          onClose={() => setShowAddCard(false)}
          onSubmit={handleAddCard}
          currency={summary.base_currency}
          initialValues={cardDraft}
        />
      )}
      {analysisItem && (
        <AnalysisDialog
          item={analysisItem}
          currency={summary.base_currency}
          result={analysisResult}
          loading={analysisLoading}
          narrative={aiNarrative}
          onNarrative={() => void handleNarrative()}
          onClose={() => setAnalysisItem(null)}
          onSubmit={handleAnalysis}
        />
      )}
      {playerIntel && (
        <PlayerIntelDialog
          intelligence={playerIntel}
          onSync={handlePlayerSync}
          onClose={() => setPlayerIntel(null)}
        />
      )}
      {itemDetail && (
        <ItemDetailDialog
          key={itemDetail.item.id}
          detail={itemDetail}
          currency={summary.base_currency}
          catalog={itemCatalog}
          catalogLoading={itemCatalogLoading}
          analysis={detailAnalysis}
          analysisLoading={detailAnalysisLoading}
          identityCorrections={identityCorrections}
          identityReview={identityReview}
          applyingIdentityCorrections={applyingIdentityCorrections}
          onClose={() => {
            setItemDetail(null);
            setItemCatalog(null);
            setDetailAnalysis(null);
            setIdentityCorrections([]);
            setIdentityReview(null);
          }}
          onCatalogRefresh={() => void refreshItemCatalog()}
          onCatalogImagesApply={replaceWithCatalogImages}
          onImageRotate={rotateDetailImage}
          onIdentityRepair={() => void repairIdentity()}
          onIdentityConfirm={confirmReviewedIdentity}
          onIdentityResearch={() => void runIdentityResearch()}
          onPublishValuation={() => void publishReviewedValuation()}
          onAnalyze={() => void analyzeItemDetail()}
          onPlayer={() => void openPlayerIntelligence(itemDetail.item.player_id)}
          onValuationDetail={() => void openValuationDetail(itemDetail.item)}
          onImage={() => setImageItem(itemDetail.item)}
          onTransaction={handleTransaction}
          onValuation={handleValuation}
          onDelete={() => void handleDeleteItem(itemDetail.item)}
        />
      )}
      {showTrash && (
        <TrashDialog
          items={trashItems}
          emptying={emptyingTrash}
          onClose={() => setShowTrash(false)}
          onRestore={(item) => void handleRestoreItem(item)}
          onPermanentDelete={(item) => void handlePermanentDelete(item)}
          onEmpty={() => void handleEmptyTrash()}
        />
      )}
      {marketItem && (
        <MarketDialog
          item={marketItem}
          comps={marketComps}
          currency={summary.base_currency}
          onClose={() => setMarketItem(null)}
          onComp={handleMarketComp}
          onEstimate={() => void handleMarketEstimate()}
        />
      )}
      {valuationDetailItem && valuationDetail && (
        <AutomatedValuationDialog
          item={valuationDetailItem}
          detail={valuationDetail}
          onClose={() => {
            setValuationDetailItem(null);
            setValuationDetail(null);
          }}
          onRetry={handleValuationRetry}
          onIdentityReview={async () => {
            const itemId = valuationDetailItem.id;
            setValuationDetailItem(null);
            setValuationDetail(null);
            await openItemDetail(itemId);
          }}
        />
      )}
      {potentialItem && (
        <PotentialDialog
          item={potentialItem}
          onClose={() => setPotentialItem(null)}
          onSubmit={handlePotential}
        />
      )}
      {imageItem && (
        <ImageDialog
          item={imageItem}
          onClose={() => setImageItem(null)}
          onSubmit={handleImageUpload}
        />
      )}
      {showRecognition && (
        <RecognitionDialog
          result={recognitionResult}
          mode={recognitionMode}
          batchDrafts={batchDrafts}
          selectedBatchIndex={selectedBatchIndex}
          loading={recognitionLoading}
          progress={recognitionProgress}
          phase={recognitionPhase}
          error={error}
          scannerJobs={scannerJobs}
          scannerResetToken={scannerResetToken}
          selectedScannerJobId={selectedScannerJobId}
          onClose={() => setShowRecognition(false)}
          onSubmit={handleRecognition}
          onUse={handleUseRecognition}
          onModeChange={(mode) => {
            setError("");
            setRecognitionMode(mode);
            setRecognitionResult(null);
            setRecognitionFile(null);
            setRecognitionBackFile(null);
            setSelectedBatchIndex(null);
            setSelectedScannerJobId(null);
          }}
          onSelectBatch={selectBatchDraft}
          onOpenScannerJob={(job) => void openScannerJob(job)}
          onRetryScannerJob={(jobId) => void retryScannerJob(jobId)}
          onDeleteScannerJob={(jobId) => void deleteScannerJob(jobId)}
        />
      )}
      {showCardHobbyImport && ownerId && (
        <CardHobbyImportDialog
          ownerId={ownerId}
          onClose={() => setShowCardHobbyImport(false)}
          onImported={async () => {
            await refresh(ownerId);
          }}
        />
      )}
      <GlobalImageViewer />
    </main>
  );
}

function CardHobbyImportDialog({
  ownerId,
  onClose,
  onImported,
}: {
  ownerId: string;
  onClose: () => void;
  onImported: () => Promise<void>;
}) {
  const [batchId, setBatchId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<CardHobbyImportDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(
    "先打开独立卡淘窗口并完成登录，登录态只保存在本机专用浏览器目录。",
  );
  const [dialogError, setDialogError] = useState("");

  async function launch() {
    setBusy(true);
    setDialogError("");
    try {
      await launchCardHobbyDesktop();
      setMessage("独立卡淘窗口已打开。登录后进入“我买到的”，再点击扫描。");
    } catch (reason) {
      setDialogError(reason instanceof Error ? reason.message : "无法启动卡淘窗口");
    } finally {
      setBusy(false);
    }
  }

  async function scan() {
    setBusy(true);
    setDialogError("");
    try {
      const preview = await scanCardHobbyDesktop(ownerId);
      setBatchId(preview.batch_id);
      setDrafts(preview.drafts);
      setMessage(`已生成 ${preview.drafts.length} 条导入预览。`);
    } catch (reason) {
      setDialogError(reason instanceof Error ? reason.message : "扫描订单失败");
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!batchId) return;
    const form = new FormData(event.currentTarget);
    const selected = drafts.filter(
      (draft) => !draft.duplicate && form.get(`selected-${draft.id}`) === "on",
    );
    try {
      const items = selected.map((draft) => {
        const value = (field: string) =>
          String(form.get(`${draft.id}-${field}`) ?? "").trim() || null;
        const seasonStart = Number(value("season_start"));
        if (
          !value("player_name") ||
          !value("brand_name") ||
          !seasonStart ||
          !value("product_name")
        ) {
          throw new Error(`“${draft.capture.title}”缺少必填身份字段`);
        }
        const serialTotal = value("serial_total")
          ? Number(value("serial_total"))
          : null;
        return {
          draft_id: draft.id,
          item: {
            owner_id: ownerId,
            card: {
              player_name: value("player_name"),
              brand_name: value("brand_name"),
              season_start: seasonStart,
              season_end: seasonStart + 1,
              product_name: value("product_name"),
              card_number: value("card_number"),
              insert_name: value("insert_name"),
              parallel_name: value("parallel_name"),
              print_run: serialTotal,
            },
            serial_total: serialTotal,
            notes: `卡淘商品：${draft.capture.item_url}`,
            purchase: {
              transacted_at: draft.capture.purchased_at ?? new Date().toISOString(),
              price: draft.capture.sale_price ?? "0",
              fee_amount: draft.capture.shipping_price,
              currency: draft.capture.currency,
              venue: "CardHobby",
              external_order_id: draft.capture.external_order_id,
              notes: `${draft.capture.title}\n${draft.capture.item_url}`,
            },
          },
        };
      });
      if (!items.length) throw new Error("没有选择可导入的卡片");
      setBusy(true);
      setDialogError("");
      const result = await confirmCardHobbyDesktop(batchId, ownerId, items);
      await onImported();
      setMessage(`成功导入 ${result.imported_item_ids.length} 张卡片。`);
      setDrafts([]);
    } catch (reason) {
      setDialogError(reason instanceof Error ? reason.message : "确认导入失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal cardhobby-import-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">CARDHOBBY DESKTOP IMPORT</p>
            <h2>从卡淘导入已买入卡片</h2>
            <p className="detail-subtitle">{message}</p>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="cardhobby-import-actions">
          <button type="button" className="ghost-button" onClick={() => void launch()} disabled={busy}>
            打开卡淘登录窗口
          </button>
          <button type="button" className="primary-button" onClick={() => void scan()} disabled={busy}>
            {busy ? "处理中…" : "扫描当前订单页"}
          </button>
        </div>
        {dialogError && <div className="error-banner">{dialogError}</div>}
        <form onSubmit={confirm}>
          <div className="cardhobby-import-list">
            {drafts.map((draft) => {
              const card = draft.proposed_item?.card;
              return (
                <article
                  className={`cardhobby-import-draft ${draft.duplicate ? "duplicate" : ""}`}
                  key={draft.id}
                >
                  <label className="cardhobby-import-title">
                    <input
                      type="checkbox"
                      name={`selected-${draft.id}`}
                      defaultChecked={Boolean(card) && !draft.duplicate}
                      disabled={draft.duplicate}
                    />
                    <strong>{draft.capture.title}</strong>
                  </label>
                  <small>
                    商品价 ¥{draft.capture.sale_price ?? "待确认"} · 运费 ¥
                    {draft.capture.shipping_price}
                  </small>
                  {draft.warnings.map((warning) => (
                    <p className="cardhobby-import-warning" key={warning}>
                      {warning}
                    </p>
                  ))}
                  <div className="cardhobby-import-fields">
                    <label>
                      球员
                      <input
                        name={`${draft.id}-player_name`}
                        defaultValue={card?.player_name ?? ""}
                      />
                    </label>
                    <label>
                      品牌
                      <input
                        name={`${draft.id}-brand_name`}
                        defaultValue={card?.brand_name ?? "Panini"}
                      />
                    </label>
                    <label>
                      赛季
                      <input
                        type="number"
                        name={`${draft.id}-season_start`}
                        defaultValue={card?.season_start ?? ""}
                      />
                    </label>
                    <label>
                      产品
                      <input
                        name={`${draft.id}-product_name`}
                        defaultValue={card?.product_name ?? ""}
                      />
                    </label>
                    <label>
                      卡号
                      <input
                        name={`${draft.id}-card_number`}
                        defaultValue={card?.card_number ?? ""}
                      />
                    </label>
                    <label>
                      Insert
                      <input
                        name={`${draft.id}-insert_name`}
                        defaultValue={card?.insert_name ?? ""}
                      />
                    </label>
                    <label>
                      平行
                      <input
                        name={`${draft.id}-parallel_name`}
                        defaultValue={card?.parallel_name ?? ""}
                      />
                    </label>
                    <label>
                      限编
                      <input
                        type="number"
                        name={`${draft.id}-serial_total`}
                        defaultValue={draft.proposed_item?.serial_total ?? ""}
                      />
                    </label>
                  </div>
                </article>
              );
            })}
          </div>
          {drafts.length > 0 && (
            <div className="form-actions">
              <button type="button" className="ghost-button" onClick={onClose}>
                取消
              </button>
              <button className="primary-button" disabled={busy}>
                确认导入已选卡片
              </button>
            </div>
          )}
        </form>
      </div>
    </div>
  );
}

function SetupDialog({
  onSubmit,
  error,
}: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  error: string;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  return (
    <div className="modal-backdrop">
      <div className="modal setup-modal">
        <div className="modal-icon">
          <Sparkles />
        </div>
        <p className="eyebrow">WELCOME TO CARD ALPHA</p>
        <h2>{mode === "login" ? "登录你的卡片账户" : "建立你的投资档案"}</h2>
        <p>{mode === "login" ? "登录后继续管理卡库、估值与投资记录。" : "首次注册会建立安全账户；已有旧卡库请使用原邮箱设置密码。"}</p>
        <div className="auth-mode-switch">
          <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>登录</button>
          <button type="button" className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>注册 / 认领旧账户</button>
        </div>
        <form onSubmit={onSubmit}>
          <input type="hidden" name="authMode" value={mode} />
          {mode === "register" && (
            <label>
              显示名称
              <input name="displayName" placeholder="例如：Song 的卡库" required />
            </label>
          )}
          <label>
            邮箱
            <input
              name="email"
              type="email"
              placeholder="you@example.com"
              required
            />
          </label>
          <label>
            密码
            <input name="password" type="password" minLength={mode === "register" ? 10 : 1} autoComplete={mode === "login" ? "current-password" : "new-password"} required />
          </label>
          {mode === "register" && (
            <label>
              基准币种
              <select name="currency" defaultValue="CNY">
                <option value="CNY">CNY · 人民币</option>
                <option value="USD">USD · 美元</option>
              </select>
            </label>
          )}
          {error && <p className="form-error">{error}</p>}
          <button className="primary-button full" type="submit">
            {mode === "login" ? "登录" : "创建账户并进入"} <ChevronRight size={17} />
          </button>
        </form>
      </div>
    </div>
  );
}

function AccountDialog({
  user,
  sessions,
  error,
  onClose,
  onChangePassword,
  onRevokeSession,
}: {
  user: AuthUser;
  sessions: AuthSession[];
  error: string;
  onClose: () => void;
  onChangePassword: (event: FormEvent<HTMLFormElement>) => void;
  onRevokeSession: (id: string) => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal account-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">ACCOUNT SECURITY</p>
            <h2>{user.display_name}</h2>
            <p>{user.email} · {user.role === "admin" ? "管理员" : "成员"}</p>
          </div>
          <button className="icon-button" onClick={onClose}><X size={20} /></button>
        </div>
        <h3>修改密码</h3>
        <form onSubmit={onChangePassword} className="account-password-form">
          <input name="currentPassword" type="password" placeholder="当前密码" autoComplete="current-password" required />
          <input name="newPassword" type="password" placeholder="新密码（至少 10 位）" minLength={10} autoComplete="new-password" required />
          <input name="confirmPassword" type="password" placeholder="确认新密码" minLength={10} autoComplete="new-password" required />
          <button className="primary-button" type="submit">更新密码</button>
        </form>
        {error && <p className="form-error">{error}</p>}
        <h3>已登录设备</h3>
        <div className="account-sessions">
          {sessions.map((session) => (
            <article key={session.id}>
              <div>
                <strong>{session.current ? "当前设备" : session.ip_address || "未知设备"}</strong>
                <small>{session.user_agent || "未知浏览器"}</small>
                <small>最近活动：{new Date(session.last_seen_at).toLocaleString("zh-CN")}</small>
              </div>
              {!session.current && <button className="ghost-button" onClick={() => onRevokeSession(session.id)}>退出该设备</button>}
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

function CardDialog({
  onClose,
  onSubmit,
  currency,
  initialValues,
}: {
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  currency: string;
  initialValues: EditableRecognition | null;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  async function submitCard(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.checkValidity()) {
      setSubmitError("请补全所有必填信息后再保存");
      const invalidField = form.querySelector<HTMLElement>(":invalid");
      invalidField?.focus();
      invalidField?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    setSubmitError("");
    setSubmitting(true);
    try {
      await onSubmit(event);
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "保存失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal card-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">NEW POSITION</p>
            <h2>录入一张卡</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            disabled={submitting}
          >
            <X size={20} />
          </button>
        </div>
        <form noValidate onSubmit={submitCard}>
          <div className="form-grid">
            <label className="wide">
              球员
              <input
                name="playerName"
                placeholder="Jalen Duren"
                defaultValue={initialValues?.player_name ?? ""}
                required
              />
            </label>
            <label>
              赛季起始年
              <input
                name="seasonStart"
                type="number"
                placeholder="2022"
                min="1940"
                max="2200"
                defaultValue={initialValues?.season_start ?? ""}
                required
              />
            </label>
            <label>
              赛季结束年
              <input
                name="seasonEnd"
                type="number"
                min="1940"
                max="2200"
                defaultValue={initialValues?.season_end ?? ""}
              />
            </label>
            <label>
              品牌
              <input
                name="brandName"
                placeholder="National Treasures"
                defaultValue={initialValues?.brand_name ?? ""}
                required
              />
            </label>
            <label className="wide">
              产品线
              <input
                name="productName"
                placeholder="National Treasures Basketball"
                defaultValue={initialValues?.product_name ?? ""}
                required
              />
            </label>
            <label className="wide">
              Insert / 卡种
              <input
                name="insertName"
                placeholder="Rookie Patch Autographs"
                defaultValue={initialValues?.insert_name ?? ""}
              />
            </label>
            <label>
              Parallel / 平行版本
              <input
                name="parallelName"
                placeholder="Gold /99"
                defaultValue={initialValues?.parallel_name ?? ""}
              />
            </label>
            <label>
              卡号
              <input
                name="cardNumber"
                placeholder="123"
                defaultValue={initialValues?.card_number ?? ""}
              />
            </label>
            <label>
              编号
              <input
                name="serialNumber"
                type="number"
                placeholder="23"
                min="1"
                defaultValue={initialValues?.serial_number ?? ""}
              />
            </label>
            <label>
              总限编
              <input
                name="serialTotal"
                type="number"
                placeholder="99"
                min="1"
                defaultValue={initialValues?.serial_total ?? ""}
              />
            </label>
            <fieldset className="wide">
              <legend>卡片属性</legend>
              <label className="check">
                <input
                  name="isRookie"
                  type="checkbox"
                  defaultChecked={initialValues?.is_rookie ?? false}
                />{" "}
                RC
              </label>
              <label className="check">
                <input
                  name="isAutograph"
                  type="checkbox"
                  defaultChecked={initialValues?.is_autograph ?? false}
                />{" "}
                Auto
              </label>
              <label className="check">
                <input
                  name="isPatch"
                  type="checkbox"
                  defaultChecked={initialValues?.is_patch ?? false}
                />{" "}
                Patch
              </label>
            </fieldset>
            <label>
              买入价格
              <input
                name="price"
                type="number"
                step="0.01"
                min="0"
                placeholder="260"
                defaultValue={initialValues?.purchase_price ?? ""}
                required
              />
            </label>
            <label>
              币种
              <select name="currency" defaultValue={initialValues?.currency ?? currency}>
                <option value="CNY">CNY</option>
                <option value="USD">USD</option>
              </select>
            </label>
            <label>
              购买日期
              <input
                name="purchasedAt"
                type="datetime-local"
                defaultValue={initialValues?.purchased_at?.slice(0, 16) ?? ""}
              />
            </label>
            <label>
              运费 / 采购费用
              <input
                name="shippingPrice"
                type="number"
                step="0.01"
                min="0"
                defaultValue={initialValues?.shipping_price ?? ""}
              />
            </label>
            <label>
              卖家
              <input name="sellerName" defaultValue={initialValues?.seller_name ?? ""} />
            </label>
            <label className="wide">
              采购凭证
              <input
                name="purchaseNotes"
                defaultValue={[
                  initialValues?.order_number && `订单号：${initialValues.order_number}`,
                  initialValues?.product_number && `商品编号：${initialValues.product_number}`,
                  initialValues?.listing_title && `商品标题：${initialValues.listing_title}`,
                  initialValues?.visible_description &&
                    `截图描述：${initialValues.visible_description}`,
                ].filter(Boolean).join("；")}
              />
            </label>
            <fieldset className="wide">
              <legend>策略标签</legend>
              <label className="check">
                <input
                  name="strategyTags"
                  value="高端品牌低价球员"
                  type="checkbox"
                />{" "}
                高端低价
              </label>
              <label className="check">
                <input name="strategyTags" value="RC潜力股" type="checkbox" />{" "}
                RC 潜力
              </label>
              <label className="check">
                <input
                  name="strategyTags"
                  value="历史球星资产"
                  type="checkbox"
                />{" "}
                历史球星
              </label>
            </fieldset>
          </div>
          {submitError && (
            <p className="form-error card-submit-error" role="alert">
              {submitError}
            </p>
          )}
          <div className="modal-actions">
            <button
              type="button"
              className="ghost-button"
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </button>
            <button type="submit" className="primary-button" disabled={submitting}>
              {submitting ? "保存中…" : "保存到卡库"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const ratingLabels: Record<string, string> = {
  strong_buy: "强力买入",
  buy: "买入",
  hold: "持有",
  reduce: "减仓",
  avoid: "回避",
};

function AnalysisDialog({
  item,
  currency,
  result,
  loading,
  narrative,
  onNarrative,
  onClose,
  onSubmit,
}: {
  item: PortfolioItem;
  currency: string;
  result: AnalysisResult | null;
  loading: boolean;
  narrative: AINarrativeResult | null;
  onNarrative: () => void;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  if (loading)
    return (
      <div className="modal-backdrop">
        <div className="modal analysis-modal analysis-loading">
          <Sparkles size={28} />
          <h2>正在生成投资建议</h2>
          <p>正在综合球员表现、潜力、卡种稀缺度、品牌等级与市场成交证据。</p>
        </div>
      </div>
    );
  if (result)
    return (
      <div className="modal-backdrop">
        <div className="modal analysis-modal">
          <div className="modal-heading">
            <div>
              <p className="eyebrow">INVESTMENT VERDICT</p>
              <h2>{item.player_name}</h2>
            </div>
            <button className="icon-button" onClick={onClose}>
              <X size={20} />
            </button>
          </div>
          <div className="score-hero">
            <div>
              <span>综合评分</span>
              <strong>{result.total_score}</strong>
              <small>/ 100</small>
            </div>
            <div className={`rating-badge ${result.rating}`}>
              {ratingLabels[result.rating]}
            </div>
          </div>
          <div className="result-grid">
            <div>
              <span>价格判断</span>
              <strong>{result.price_assessment}</strong>
            </div>
            <div>
              <span>建议持有</span>
              <strong>
                {result.holding_period_min_months}–
                {result.holding_period_max_months} 个月
              </strong>
            </div>
            <div>
              <span>合理区间</span>
              <strong>
                {money(result.fair_value_low, currency)} –{" "}
                {money(result.fair_value_high, currency)}
              </strong>
            </div>
            <div>
              <span>目标卖出</span>
              <strong>
                {money(result.target_sell_low, currency)} –{" "}
                {money(result.target_sell_high, currency)}
              </strong>
            </div>
          </div>
          <div className="evidence-columns">
            <div>
              <h3>潜在催化剂</h3>
              {result.catalysts.map((value, index) => (
                <p key={`${value}-${index}`} className="evidence positive-dot">
                  {value}
                </p>
              ))}
            </div>
            <div>
              <h3>主要风险</h3>
              {result.risks.map((value, index) => (
                <p key={`${value}-${index}`} className="evidence risk-dot">
                  {value}
                </p>
              ))}
            </div>
          </div>
          {narrative ? (
            <div className="ai-narrative">
              <div className="ai-heading">
                <Sparkles size={15} />
                <strong>AI 深度解读</strong>
                <span>
                  {narrative.provider === "openai"
                    ? narrative.model
                    : "规则降级"}
                </span>
              </div>
              <p>{narrative.narrative.thesis}</p>
              <p>{narrative.narrative.price_reasoning}</p>
              <small>{narrative.narrative.disclaimer}</small>
            </div>
          ) : (
            <button className="ai-button" onClick={onNarrative}>
              <Sparkles size={15} /> 生成 AI 深度解读
            </button>
          )}
          <p className="confidence">
            分析置信度 {(Number(result.confidence) * 100).toFixed(0)}% ·
            数值由可解释规则模型生成
          </p>
          <button className="primary-button full" onClick={onClose}>
            完成
          </button>
        </div>
      </div>
    );
  const initialPrice = Number(item.fair_value ?? item.remaining_cost) || 0;
  return (
    <div className="modal-backdrop">
      <div className="modal analysis-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">CARD ANALYSIS</p>
            <h2>分析 {item.player_name}</h2>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <p>为六项因子打分，并提供近期可比成交推导出的合理区间。</p>
        <form onSubmit={onSubmit}>
          <div className="form-grid">
            <label>
              当前询价
              <input
                name="askingPrice"
                type="number"
                min="0"
                step="0.01"
                defaultValue={initialPrice}
                required
              />
            </label>
            <label>
              可比成交数
              <input
                name="evidenceCount"
                type="number"
                min="0"
                defaultValue="0"
                required
              />
            </label>
            <label>
              合理价下限
              <input
                name="fairValueLow"
                type="number"
                min="0"
                step="0.01"
                defaultValue={initialPrice * 0.9}
                required
              />
            </label>
            <label>
              合理价上限
              <input
                name="fairValueHigh"
                type="number"
                min="0"
                step="0.01"
                defaultValue={initialPrice * 1.1}
                required
              />
            </label>
          </div>
          <div className="factor-grid">
            <ScoreInput name="historicalStanding" label="历史地位" value={60} />
            <ScoreInput name="currentPerformance" label="当前表现" value={65} />
            <ScoreInput name="agePotential" label="年龄潜力" value={70} />
            <ScoreInput name="cardScarcity" label="卡种稀缺" value={70} />
            <ScoreInput name="brandTier" label="品牌等级" value={75} />
            <ScoreInput name="marketHeat" label="市场热度" value={60} />
          </div>
          <div className="modal-actions">
            <button type="button" className="ghost-button" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="primary-button">
              <Sparkles size={15} /> 生成投资建议
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ScoreInput({
  name,
  label,
  value,
}: {
  name: string;
  label: string;
  value: number;
}) {
  return (
    <label className="score-input">
      <span>{label}</span>
      <input
        name={name}
        type="number"
        min="0"
        max="100"
        defaultValue={value}
        required
      />
    </label>
  );
}

const eventLabels: Record<string, string> = {
  news: "新闻",
  injury: "伤病",
  trade: "交易",
  team_context: "球队环境",
  award: "奖项",
  milestone: "里程碑",
};

function PlayerIntelDialog({
  intelligence,
  onSync,
  onClose,
}: {
  intelligence: PlayerIntelligence;
  onSync: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}) {
  const latestMetric = intelligence.metrics[0];
  return (
    <div className="modal-backdrop">
      <div className="modal intel-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">PLAYER INTELLIGENCE</p>
            <h2>{intelligence.player_name}</h2>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <form className="sync-form" onSubmit={onSync}>
          <input
            name="nbaPersonId"
            type="number"
            min="1"
            placeholder="NBA Player ID"
            required
          />
          <button className="secondary-button" type="submit">
            同步 NBA / 新闻 / 伤病
          </button>
        </form>
        {intelligence.potential ? (
          <div className="potential-card">
            <div>
              <span>未来潜力</span>
              <strong>{intelligence.potential.score}</strong>
              <small>/ 100</small>
            </div>
            <b>{intelligence.potential.label}</b>
          </div>
        ) : (
          <div className="intel-empty">尚未建立潜力评估</div>
        )}
        <div className="intel-section">
          <h3>最新 NBA 数据</h3>
          {latestMetric ? (
            <div className="metric-chips">
              <span>{latestMetric.season}</span>
              {Object.entries(latestMetric.metrics)
                .slice(0, 6)
                .map(([key, value]) => (
                  <div key={key}>
                    <small>{key}</small>
                    <strong>{String(value ?? "—")}</strong>
                  </div>
                ))}
            </div>
          ) : (
            <p className="muted-copy">暂无比赛数据，可通过指标导入接口添加。</p>
          )}
        </div>
        <div className="intel-section">
          <h3>新闻、伤病与球队动态</h3>
          <div className="event-list">
            {intelligence.events.map((event) => (
              <article key={event.id}>
                <span className={`event-type ${event.event_type}`}>
                  {eventLabels[event.event_type] ?? event.event_type}
                </span>
                <div>
                  <strong>{event.headline}</strong>
                  <small>
                    {new Date(event.occurred_at).toLocaleDateString("zh-CN")}
                    {event.summary ? ` · ${event.summary}` : ""}
                  </small>
                </div>
                {event.impact_score && <b>{event.impact_score}</b>}
              </article>
            ))}
            {intelligence.events.length === 0 && (
              <p className="muted-copy">暂无情报事件。</p>
            )}
          </div>
        </div>
        <button className="primary-button full" onClick={onClose}>
          完成
        </button>
      </div>
    </div>
  );
}

const transactionLabels: Record<string, string> = {
  buy: "买入",
  sell: "卖出",
  fee: "费用",
  adjustment: "调整",
};

function TrashDialog({
  items,
  emptying,
  onClose,
  onRestore,
  onPermanentDelete,
  onEmpty,
}: {
  items: PortfolioItem[];
  emptying: boolean;
  onClose: () => void;
  onRestore: (item: PortfolioItem) => void;
  onPermanentDelete: (item: PortfolioItem) => void;
  onEmpty: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal trash-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">TRASH / 回收站</p>
            <h2>已删除卡片</h2>
            <p className="detail-subtitle">恢复后重新进入持仓；永久删除不可撤销。</p>
          </div>
          <div className="trash-heading-actions">
            {items.length > 0 && (
              <button
                type="button"
                className="danger-button"
                disabled={emptying}
                onClick={onEmpty}
              >
                <Trash2 size={14} />
                {emptying ? "清空中…" : `清空回收站 (${items.length})`}
              </button>
            )}
            <button
              type="button"
              className="icon-button"
              disabled={emptying}
              onClick={onClose}
            >
              <X size={20} />
            </button>
          </div>
        </div>
        <div className="trash-list">
          {items.map((item) => (
            <article key={item.id}>
              <div>
                <strong>{item.player_name}</strong>
                <span>{season(item)} · {item.product_name}</span>
                <small>
                  {item.deleted_at
                    ? new Date(item.deleted_at).toLocaleString("zh-CN")
                    : ""}
                  {item.deleted_reason ? ` · ${item.deleted_reason}` : ""}
                </small>
              </div>
              <div className="action-row">
                <button className="secondary-button" disabled={emptying} onClick={() => onRestore(item)}>
                  恢复
                </button>
                <button className="danger-button" disabled={emptying} onClick={() => onPermanentDelete(item)}>
                  永久删除
                </button>
              </div>
            </article>
          ))}
          {items.length === 0 && <p className="muted-copy">回收站为空。</p>}
        </div>
      </div>
    </div>
  );
}

function ItemDetailDialog({
  detail,
  currency,
  catalog,
  catalogLoading,
  analysis,
  analysisLoading,
  identityCorrections,
  identityReview,
  applyingIdentityCorrections,
  onClose,
  onCatalogRefresh,
  onCatalogImagesApply,
  onImageRotate,
  onIdentityRepair,
  onIdentityConfirm,
  onIdentityResearch,
  onPublishValuation,
  onAnalyze,
  onPlayer,
  onValuationDetail,
  onImage,
  onTransaction,
  onValuation,
  onDelete,
}: {
  detail: ItemDetail;
  currency: string;
  catalog: CardRecognition | null;
  catalogLoading: boolean;
  analysis: AnalysisResult | null;
  analysisLoading: boolean;
  identityCorrections: IdentityCorrection[];
  identityReview: IdentityReviewDetail | null;
  applyingIdentityCorrections: boolean;
  onClose: () => void;
  onCatalogRefresh: () => void;
  onCatalogImagesApply: (side: "front" | "back" | "both") => Promise<void>;
  onImageRotate: (side: "front" | "back", degrees: 90 | 270) => Promise<void>;
  onIdentityRepair: () => void;
  onIdentityConfirm: (identity: ConfirmedCardIdentity, reason: string) => Promise<void>;
  onIdentityResearch: () => void;
  onPublishValuation: () => void;
  onAnalyze: () => void;
  onPlayer: () => void;
  onValuationDetail: () => void;
  onImage: () => void;
  onTransaction: (event: FormEvent<HTMLFormElement>) => void;
  onValuation: (event: FormEvent<HTMLFormElement>) => void;
  onDelete: () => void;
}) {
  const [applyingCatalogImages, setApplyingCatalogImages] = useState<
    "front" | "back" | "both" | null
  >(null);
  const [catalogImageMessage, setCatalogImageMessage] = useState("");
  const [rotatingImage, setRotatingImage] = useState<"front" | "back" | null>(null);
  const [reviewDraft, setReviewDraft] = useState<ConfirmedCardIdentity | null>(
    identityReview?.suggested_identity ?? null,
  );
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const confirmationFormRef = useRef<HTMLDivElement | null>(null);
  const [reviewReason, setReviewReason] = useState("人工核对正反面图片及成交证据");
  const item = detail.item;
  const catalogFields = [
    ["球员", catalog?.player_name ?? item.player_name],
    ["赛季", catalog?.season_start
      ? `${catalog.season_start}${catalog.season_end ? `-${String(catalog.season_end).slice(-2)}` : ""}`
      : season(item)],
    ["品牌", catalog?.brand_name ?? item.brand_name],
    ["产品线", catalog?.product_name ?? item.product_name],
    ["卡种", catalog?.insert_name ?? item.insert_name],
    ["平行版本", catalog?.parallel_name ?? item.parallel_name],
    ["卡号", catalog?.card_number ?? item.card_number],
    ["限编", catalog?.serial_total ? `/${catalog.serial_total}` : item.serial_total ? `/${item.serial_total}` : null],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  const imageFront = assetUrl(item.image_front_url);
  const imageBack = assetUrl(item.image_back_url);
  const profit = Number(item.unrealized_profit ?? 0);
  const referenceFront = catalog?.reference_image_front_url ?? null;
  const referenceBack = catalog?.reference_image_back_url ?? null;

  async function applyReferenceImages(side: "front" | "back" | "both") {
    setApplyingCatalogImages(side);
    setCatalogImageMessage("");
    try {
      await onCatalogImagesApply(side);
      setCatalogImageMessage(
        side === "both" ? "正反面高清图已替换" : `${side === "front" ? "正面" : "背面"}高清图已替换`,
      );
    } catch {
      setCatalogImageMessage("");
    } finally {
      setApplyingCatalogImages(null);
    }
  }

  async function rotateImage(side: "front" | "back", degrees: 90 | 270) {
    setRotatingImage(side);
    try {
      await onImageRotate(side, degrees);
    } finally {
      setRotatingImage(null);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal detail-modal card-dossier-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">CARD DOSSIER / 卡片档案</p>
            <h2>{item.player_name}</h2>
            <p className="detail-subtitle">
              {season(item)} · {item.product_name}
              {item.insert_name ? ` · ${item.insert_name}` : ""}
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="dossier-hero">
          <section className="dossier-gallery">
            <div className="dossier-card-images">
              <figure>
                <div className="dossier-main-image">
                  {imageFront ? (
                    <OpenableImage src={imageFront} alt={`${item.player_name} 卡片正面`} />
                  ) : (
                    <div>
                      <strong>暂无正面图片</strong>
                      <span>上传正面图后会显示在这里</span>
                    </div>
                  )}
                </div>
                <figcaption>
                  <span>正面</span>
                  {imageFront && (
                    <span className="image-rotation-actions">
                      <button type="button" aria-label="正面向左旋转" disabled={rotatingImage === "front"} onClick={() => void rotateImage("front", 270)}><RotateCcw size={13} /></button>
                      <button type="button" aria-label="正面向右旋转" disabled={rotatingImage === "front"} onClick={() => void rotateImage("front", 90)}><RotateCw size={13} /></button>
                    </span>
                  )}
                </figcaption>
              </figure>
              <figure>
                <div className="dossier-main-image">
                  {imageBack ? (
                    <OpenableImage src={imageBack} alt={`${item.player_name} 卡片背面`} />
                  ) : (
                    <div>
                      <strong>暂无背面图片</strong>
                      <span>上传背面图后会显示在这里</span>
                    </div>
                  )}
                </div>
                <figcaption>
                  <span>背面</span>
                  {imageBack && (
                    <span className="image-rotation-actions">
                      <button type="button" aria-label="背面向左旋转" disabled={rotatingImage === "back"} onClick={() => void rotateImage("back", 270)}><RotateCcw size={13} /></button>
                      <button type="button" aria-label="背面向右旋转" disabled={rotatingImage === "back"} onClick={() => void rotateImage("back", 90)}><RotateCw size={13} /></button>
                    </span>
                  )}
                </figcaption>
              </figure>
            </div>
            <div className="dossier-gallery-foot">
              <button type="button" className="ghost-button" onClick={onImage}>
                {imageFront || imageBack ? "更新正反面图片" : "上传正反面图片"}
              </button>
            </div>
          </section>

          <section className="dossier-overview">
            <div className="tag-row">
              {item.is_rookie && <span className="tag lime">RC</span>}
              {item.is_autograph && <span className="tag">AUTO</span>}
              {item.is_patch && <span className="tag">PATCH</span>}
              {item.serial_total && <span className="tag muted">/{item.serial_total}</span>}
              {item.grade_company && (
                <span className="tag">{item.grade_company} {item.grade_value}</span>
              )}
            </div>
            <div className="dossier-value-grid">
              <div>
                <span>当前估值</span>
                <strong>{item.fair_value ? money(item.fair_value, item.valuation_currency ?? currency) : "待估值"}</strong>
              </div>
              <div>
                <span>持仓成本</span>
                <strong>{money(item.remaining_cost, currency)}</strong>
              </div>
              <div>
                <span>未实现盈亏</span>
                <strong className={profit >= 0 ? "positive-text" : "negative-text"}>
                  {item.unrealized_profit ? money(item.unrealized_profit, currency) : "—"}
                </strong>
              </div>
            </div>
            <div className="dossier-actions">
              <button type="button" className="primary-button" onClick={onAnalyze}>
                <Sparkles size={14} /> 生成投资建议
              </button>
              <button type="button" className="secondary-button" onClick={onValuationDetail}>
                查看估值依据
              </button>
              <button type="button" className="ghost-button" onClick={onPlayer}>
                球员情报
              </button>
            </div>
            <div className="dossier-fields">
              {catalogFields.map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
          </section>
        </div>

        {identityReview && reviewDraft && (
          <section className="dossier-section identity-review-workbench">
            <div className="dossier-section-heading">
              <div>
                <p className="eyebrow">IDENTITY REVIEW / 身份复核</p>
                <h3>逐字段确认 · 版本 {identityReview.latest_version?.version ?? 0}</h3>
              </div>
              <span className={`tag ${identityReview.latest_version?.review_status === "confirmed" ? "lime" : "muted"}`}>
                {identityReview.research?.status ?? identityReview.latest_version?.review_status ?? "pending"}
              </span>
            </div>
            {identityReview.review_reasons.length > 0 && (
              <p className="identity-review-warning">冲突：{identityReview.review_reasons.join("、")}</p>
            )}
            <button className="secondary-button" disabled={applyingIdentityCorrections}
              onClick={onIdentityResearch}>研究冲突并生成候选</button>
            <div className="identity-candidate-list">
              {identityReview.candidates.map((candidate) => {
                const selected = selectedCandidateId === candidate.id;
                return (
                  <article className={`identity-candidate${selected ? " selected" : ""}`} key={candidate.id}>
                    <div className="identity-candidate-heading">
                      <div>
                        <span>候选 {candidate.rank}</span>
                        <strong>{candidate.identity.parallel_name}</strong>
                      </div>
                      <b>{Math.round(Number(candidate.confidence) * 100)}%</b>
                    </div>
                    <p className="identity-candidate-meta">
                      {candidate.identity.season_start}-{String(candidate.identity.season_end).slice(-2)} ·
                      卡号 #{candidate.identity.card_number} · 实编 {candidate.identity.serial_number}/{candidate.identity.serial_total}
                    </p>
                    <ul>
                      {candidate.visual_reasons.map((reason) => (
                        <li key={reason}>{({
                          "front has large gold faceted Diamante pattern": "正面为大块金色 Diamante 多面碎冰纹",
                          "front serial stamp reads 27/50": "正面限编钢印为 27/50",
                          "back is card 31 and contains completed 2024-25 statistics": "背面卡号 31，包含完整 2024–25 赛季统计",
                          "back copyright is 2025": "背面版权年份为 2025",
                        } as Record<string, string>)[reason] ?? reason}</li>
                      ))}
                    </ul>
                    <small>清单 {candidate.checklist_evidence[0]?.source_version ?? "—"} · {candidate.market_evidence.length} 笔精确成交</small>
                    <button type="button" className={selected ? "primary-button" : "secondary-button"}
                      disabled={!candidate.is_valid}
                      onClick={() => {
                        setReviewDraft(candidate.identity);
                        setSelectedCandidateId(candidate.id);
                        window.setTimeout(() => confirmationFormRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
                      }}>
                      {selected ? "已选择此候选 ✓" : "选择并填写确认表"}
                    </button>
                  </article>
                );
              })}
            </div>
            <div className="identity-confirmation-form" ref={confirmationFormRef}>
              <h4>{selectedCandidateId ? "请核对并确认所选身份" : "请先选择上方候选"}</h4>
            <div className="identity-review-grid">
              {([
                ["player_name", "球员"], ["season_start", "赛季起"], ["season_end", "赛季止"],
                ["brand_name", "品牌"], ["product_name", "产品"], ["set_name", "Set"],
                ["card_number", "卡号"], ["insert_name", "Insert"], ["parallel_name", "平行"],
                ["serial_number", "限编号"], ["serial_total", "限编总数"],
                ["grade_company", "评级公司"], ["grade_value", "评级"],
              ] as Array<[keyof ConfirmedCardIdentity, string]>).map(([field, label]) => (
                <label key={field}>
                  <span>{label}</span>
                  <input
                    value={reviewDraft[field] == null ? "" : String(reviewDraft[field])}
                    onChange={(event) => setReviewDraft({
                      ...reviewDraft,
                      [field]: ["season_start", "season_end", "serial_number", "serial_total", "grade_value"].includes(field)
                        ? (event.target.value === "" ? null : Number(event.target.value))
                        : (event.target.value || null),
                    })}
                  />
                  <small>
                    当前：{correctionValue(identityReview.current_identity[field] as string | number | boolean | null)}
                    {identityReview.field_provenance[field] ? " · 有来源记录" : ""}
                  </small>
                </label>
              ))}
            </div>
            <div className="identity-review-toggles">
              {([ ["is_rookie", "RC"], ["is_autograph", "签字"], ["is_memorabilia", "物料"] ] as Array<[keyof ConfirmedCardIdentity, string]>).map(([field, label]) => (
                <label key={field}><input type="checkbox" checked={Boolean(reviewDraft[field])}
                  onChange={(event) => setReviewDraft({ ...reviewDraft, [field]: event.target.checked })} /> {label}</label>
              ))}
            </div>
            <label className="identity-review-reason"><span>确认理由</span><input value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} /></label>
            <div className="identity-review-sales">
              <strong>关联成交（{identityReview.market_evidence.filter((sale) => sale.decision === "accepted").length} 接受 / {identityReview.market_evidence.filter((sale) => sale.decision === "rejected").length} 拒绝）</strong>
              {identityReview.market_evidence.map((sale) => (
                <a key={sale.id} href={sale.source_url} target="_blank" rel="noreferrer">
                  <span>{sale.decision === "accepted" ? "接受" : "隔离"} · {sale.raw_title}</span>
                  <small>{sale.reason_codes.join("、")} · {money(sale.sale_price, sale.currency)}</small>
                </a>
              ))}
            </div>
            <button className="primary-button" disabled={!selectedCandidateId || applyingIdentityCorrections || reviewReason.trim().length < 3}
              onClick={() => void onIdentityConfirm(reviewDraft, reviewReason)}>
              {applyingIdentityCorrections ? "确认中…" : "创建不可变确认版本"}
            </button>
            {identityReview.latest_version?.review_status === "confirmed" && identityReview.latest_shadow?.fair_value && (
              <div className="identity-publish-panel">
                <div>
                  <span>已确认身份的影子估值</span>
                  <strong>{money(identityReview.latest_shadow.fair_value, identityReview.latest_shadow.currency)}</strong>
                  <small>
                    {money(identityReview.latest_shadow.low_value ?? identityReview.latest_shadow.fair_value, identityReview.latest_shadow.currency)} – {money(identityReview.latest_shadow.high_value ?? identityReview.latest_shadow.fair_value, identityReview.latest_shadow.currency)}
                    {" · "}{identityReview.latest_shadow.evidence_count} 笔精确成交
                  </small>
                </div>
                <button type="button" className="primary-button" disabled={applyingIdentityCorrections}
                  onClick={onPublishValuation}>
                  {applyingIdentityCorrections ? "更新中…" : "采用此价格并更新估值"}
                </button>
              </div>
            )}
            </div>
          </section>
        )}

        {identityCorrections.length > 0 && (
          <section className="dossier-section identity-correction-section">
            <div className="dossier-section-heading">
              <div>
                <p className="eyebrow">IDENTITY CHECK / 技术信息核验</p>
                <h3>发现 {identityCorrections.length} 项可能的录入错误</h3>
              </div>
              <button
                type="button"
                className="primary-button identity-repair-button"
                onClick={onIdentityRepair}
                disabled={applyingIdentityCorrections}
              >
                {applyingIdentityCorrections ? "修复并重新估值中…" : "一键修复卡片信息"}
              </button>
            </div>
            <p className="identity-correction-disclaimer">
              建议来自估值成交证据的技术字段共识；修复前不会自动改动卡片档案。
            </p>
            <div className="identity-correction-list">
              {identityCorrections.map((correction) => (
                <article key={correction.id}>
                  <div className="identity-correction-change">
                    <strong>
                      {identityCorrectionLabels[correction.field_name] ??
                        correction.field_name}
                    </strong>
                    <span>{correctionValue(correction.current_value)}</span>
                    <b>→</b>
                    <span className="suggested-value">
                      {correctionValue(correction.suggested_value)}
                    </span>
                    <small>
                      {Math.round(Number(correction.confidence) * 100)}% 置信度
                    </small>
                  </div>
                  <div className="identity-correction-evidence">
                    {correction.evidence.map((evidence) => (
                      <a
                        key={`${correction.id}-${evidence.market_comp_id}`}
                        href={evidence.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {evidence.title}
                      </a>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        <section className="dossier-section catalog-section">
          <div className="dossier-section-heading">
            <div>
              <p className="eyebrow">CHECKLIST MATCH</p>
              <h3>图鉴与卡片补全</h3>
            </div>
            <button type="button" className="ghost-button" onClick={onCatalogRefresh} disabled={catalogLoading}>
              {catalogLoading ? "联网核验中…" : "重新核验"}
            </button>
          </div>
          {catalogLoading && !catalog ? (
            <div className="dossier-loading"><span className="valuation-spinner" /> 正在检索厂商及权威图鉴…</div>
          ) : (
            <>
              {(referenceFront || referenceBack) && (
                <div className="catalog-reference-gallery">
                  <div className="catalog-reference-heading">
                    <div>
                      <strong>卡淘高清参考图</strong>
                      <span>
                        严格匹配度{" "}
                        {Math.round(Number(catalog?.reference_image_match_confidence ?? 0) * 100)}%
                      </span>
                    </div>
                    {catalog?.reference_image_source_url && (
                      <a href={catalog.reference_image_source_url} target="_blank" rel="noreferrer">
                        查看卡淘原页
                      </a>
                    )}
                  </div>
                  <div className="catalog-reference-pictures">
                    {referenceFront && (
                      <article>
                        <OpenableImage src={referenceFront} alt={`${item.player_name} 卡淘高清正面`} />
                        <div>
                          <span>高清正面</span>
                          <button
                            type="button"
                            className="ghost-button"
                            disabled={applyingCatalogImages !== null}
                            onClick={() => void applyReferenceImages("front")}
                          >
                            {applyingCatalogImages === "front" ? "替换中…" : "替换正面"}
                          </button>
                        </div>
                      </article>
                    )}
                    {referenceBack && (
                      <article>
                        <OpenableImage src={referenceBack} alt={`${item.player_name} 卡淘高清背面`} />
                        <div>
                          <span>高清背面</span>
                          <button
                            type="button"
                            className="ghost-button"
                            disabled={applyingCatalogImages !== null}
                            onClick={() => void applyReferenceImages("back")}
                          >
                            {applyingCatalogImages === "back" ? "替换中…" : "替换背面"}
                          </button>
                        </div>
                      </article>
                    )}
                  </div>
                  {referenceFront && referenceBack && (
                    <button
                      type="button"
                      className="secondary-button catalog-apply-both"
                      disabled={applyingCatalogImages !== null}
                      onClick={() => void applyReferenceImages("both")}
                    >
                      {applyingCatalogImages === "both" ? "正在替换正反面…" : "一键替换正反面高清图"}
                    </button>
                  )}
                  {catalogImageMessage && <p className="catalog-image-success">{catalogImageMessage}</p>}
                </div>
              )}
              <div className="catalog-evidence-grid">
                {Object.entries(catalog?.field_evidence ?? {}).map(([field, values]) => (
                  <article key={field}>
                    <span>{recognitionFieldLabels[field] ?? field}</span>
                    <p>{values.join("；")}</p>
                  </article>
                ))}
                {Object.keys(catalog?.field_evidence ?? {}).length === 0 && (
                  <p className="muted-copy">当前档案已展示入库信息；暂无额外权威图鉴证据。</p>
                )}
              </div>
              {catalog?.web_candidates && catalog.web_candidates.length > 0 && (
                <div className="catalog-candidates">
                  {catalog.web_candidates.map((candidate, index) => (
                    <article key={`${candidate.title}-${index}`}>
                      {candidate.image_url && (
                        <div className="catalog-reference-image">
                          <OpenableImage src={candidate.image_url} alt={`${candidate.title} 图鉴参考图`} />
                        </div>
                      )}
                      {!candidate.image_url && (
                        <div className="catalog-image-unavailable">
                          <span>暂无公开高清图</span>
                          <small>来源站点未开放图片读取</small>
                        </div>
                      )}
                      <div>
                        <strong>{candidate.title}</strong>
                        <span>匹配度 {Math.round(Number(candidate.match_confidence) * 100)}%</span>
                      </div>
                      <small>
                        {candidate.matching_fields.length
                          ? `匹配：${candidate.matching_fields.join("、")}`
                          : "候选图鉴条目"}
                      </small>
                      <div>
                        {candidate.source_urls.map((url) => (
                          <a key={url} href={url} target="_blank" rel="noreferrer">
                            {evidenceSourceHost(url)}
                          </a>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {catalog?.caveats && catalog.caveats.length > 0 && (
                <p className="catalog-caveat">{catalog.caveats.join("；")}</p>
              )}
            </>
          )}
        </section>

        <section className="dossier-section advice-section">
          <div className="dossier-section-heading">
            <div>
              <p className="eyebrow">INVESTMENT VIEW</p>
              <h3>卡片建议</h3>
            </div>
            {!analysis && (
              <button type="button" className="secondary-button" onClick={onAnalyze} disabled={analysisLoading}>
                {analysisLoading ? "分析中…" : "生成建议"}
              </button>
            )}
          </div>
          {analysisLoading ? (
            <div className="dossier-loading"><span className="valuation-spinner" /> 正在综合球员、卡种与市场证据…</div>
          ) : analysis ? (
            <div className="detail-advice">
              <div className="detail-score">
                <strong>{analysis.total_score}</strong>
                <span>/ 100 · {ratingLabels[analysis.rating]}</span>
              </div>
              <div className="detail-advice-metrics">
                <div><span>价格判断</span><strong>{analysis.price_assessment}</strong></div>
                <div><span>合理区间</span><strong>{money(analysis.fair_value_low, currency)} – {money(analysis.fair_value_high, currency)}</strong></div>
                <div><span>目标卖出</span><strong>{money(analysis.target_sell_low, currency)} – {money(analysis.target_sell_high, currency)}</strong></div>
                <div><span>建议持有</span><strong>{analysis.holding_period_min_months}–{analysis.holding_period_max_months} 个月</strong></div>
              </div>
              <div className="detail-advice-columns">
                <div><h4>潜在催化剂</h4>{analysis.catalysts.map((value, index) => <p key={`${value}-${index}`}>{value}</p>)}</div>
                <div><h4>主要风险</h4>{analysis.risks.map((value, index) => <p key={`${value}-${index}`}>{value}</p>)}</div>
              </div>
            </div>
          ) : (
            <p className="muted-copy">生成建议后，这里会展示价格判断、持有周期、催化剂和风险。</p>
          )}
        </section>

        <details className="dossier-management">
          <summary>交易、手动估值与历史记录</summary>
          <div className="management-grid">
            <section>
              <h3>记录交易</h3>
              <form onSubmit={onTransaction} className="compact-form">
                <label>类型<select name="transactionType"><option value="sell">卖出</option><option value="buy">追加买入</option><option value="fee">费用</option><option value="adjustment">成本调整</option></select></label>
                <label>数量<input name="quantity" type="number" min="0.0001" step="0.0001" defaultValue="1" required /></label>
                <label>单价<input name="unitPrice" type="number" min="0" step="0.01" defaultValue="0" required /></label>
                <label>费用<input name="feeAmount" type="number" min="0" step="0.01" defaultValue="0" required /></label>
                <label className="wide">渠道<input name="venue" placeholder="eBay / 卡店 / 卡展" /></label>
                <button className="secondary-button wide" type="submit">保存交易</button>
              </form>
            </section>
            <section>
              <h3>更新估值</h3>
              <form onSubmit={onValuation} className="compact-form">
                <label>区间下限<input name="lowValue" type="number" min="0" step="0.01" required /></label>
                <label>合理价值<input name="fairValue" type="number" min="0" step="0.01" required /></label>
                <label>区间上限<input name="highValue" type="number" min="0" step="0.01" required /></label>
                <label>置信度 %<input name="confidence" type="number" min="0" max="100" defaultValue="70" required /></label>
                <button className="secondary-button wide" type="submit">保存估值快照</button>
              </form>
            </section>
          </div>
          <div className="history-grid">
            <section><h3>交易历史</h3><div className="history-list">
              {detail.transactions.map((row) => (
                <div key={row.id}>
                  <span className={`history-type ${row.transaction_type}`}>{transactionLabels[row.transaction_type]}</span>
                  <strong>{money(Number(row.unit_price) * Number(row.quantity), row.currency)}</strong>
                  <small>{new Date(row.transacted_at).toLocaleDateString("zh-CN")} · 数量 {row.quantity}{Number(row.fee_amount) > 0 ? ` · 费用 ${money(row.fee_amount, row.currency)}` : ""}</small>
                </div>
              ))}
              {detail.transactions.length === 0 && <p>暂无记录</p>}
            </div></section>
            <section><h3>估值历史</h3><div className="history-list">
              {detail.valuations.map((row) => (
                <div key={row.id}>
                  <span className="history-type valuation">估值</span>
                  <strong>{money(row.fair_value, row.currency)}</strong>
                  <small>{new Date(row.valued_at).toLocaleDateString("zh-CN")} · {money(row.low_value, row.currency)} – {money(row.high_value, row.currency)}</small>
                </div>
              ))}
              {detail.valuations.length === 0 && <p>暂无记录</p>}
            </div></section>
          </div>
        </details>
        <button type="button" className="danger-button" onClick={onDelete}>
          <Trash2 size={14} /> 删除卡片（移入回收站）
        </button>
      </div>
    </div>
  );
}

const valuationStatusLabels: Record<string, string> = {
  queued: "等待处理",
  identifying: "正在确认卡片身份",
  searching: "正在检索真实成交",
  calculating: "正在计算估值区间",
  succeeded: "估值完成",
  insufficient_evidence: "有效成交证据不足",
  failed: "估值失败",
};

function AutomatedValuationDialog({
  item,
  detail,
  onClose,
  onRetry,
  onIdentityReview,
}: {
  item: PortfolioItem;
  detail: AutomatedValuationDetail;
  onClose: () => void;
  onRetry: () => Promise<void>;
  onIdentityReview: () => Promise<void>;
}) {
  const [retrying, setRetrying] = useState(false);
  const valuation = detail.valuation;
  const shadow = detail.shadow_valuation;
  const evidence = valuation?.evidence;
  const comps = evidence?.comparables ?? [];
  const running = isValuationRunning(detail.status);
  const researchStatus = String(detail.result_summary.research_status ?? "");
  const awaitingIdentityConfirmation = researchStatus === "awaiting_confirmation";
  return (
    <div className="modal-backdrop">
      <div className="modal valuation-detail-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">AUTOMATED VALUATION</p>
            <h2>{item.player_name} · 估值详情</h2>
            <p className="detail-subtitle">
              {valuationStatusLabels[detail.status ?? ""] ?? "尚未估值"}
            </p>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        {running && (
          <div className="valuation-running-panel">
            <span className="valuation-spinner" />
            <div>
              <strong>{valuationStatusLabels[detail.status ?? ""]}</strong>
              <p>系统正在后台处理，完成后列表中的价格会自动更新。</p>
            </div>
          </div>
        )}
        {shadow && (
          <section className="valuation-method">
            <h3>新算法影子估值</h3>
            {shadow.fair_value ? (
              <p>
                {money(shadow.fair_value, shadow.currency)}（
                {money(shadow.low_value ?? shadow.fair_value, shadow.currency)} – {" "}
                {money(shadow.high_value ?? shadow.fair_value, shadow.currency)}）
              </p>
            ) : (
              <p>证据不足，未生成价格。</p>
            )}
            <small>
              {shadow.model_version} · {shadow.evidence_count} 条精确证据 · 置信度 {" "}
              {Math.round(Number(shadow.confidence) * 100)}% · 不影响当前展示估值
            </small>
            {shadow.review_reasons.length > 0 && (
              <small>需复核：{shadow.review_reasons.join("、")}</small>
            )}
          </section>
        )}
        {valuation && (
          <>
            <div className="valuation-hero">
              <div>
                <span>当前合理估值</span>
                <strong>{money(valuation.fair_value, valuation.currency)}</strong>
              </div>
              <div>
                <span>估值区间</span>
                <strong>
                  {money(valuation.low_value, valuation.currency)} –{" "}
                  {money(valuation.high_value, valuation.currency)}
                </strong>
              </div>
              <div>
                <span>置信度</span>
                <strong>
                  {valuation.confidence
                    ? `${Math.round(Number(valuation.confidence) * 100)}%`
                    : "—"}
                </strong>
              </div>
            </div>
            <section className="valuation-method">
              <h3>估值逻辑</h3>
              <p>{evidence?.strategy ?? "可比成交估值"}</p>
              <small>{evidence?.formula}</small>
              <small>
                模型 {valuation.model_version ?? "—"} ·{" "}
                {new Date(valuation.valued_at).toLocaleString("zh-CN")}
              </small>
              {evidence?.landed_price && (
                <small>
                  含运到手价（单独展示）：
                  {money(evidence.landed_price.fair, valuation.currency)}，区间{" "}
                  {money(evidence.landed_price.low, valuation.currency)} –{" "}
                  {money(evidence.landed_price.high, valuation.currency)}
                </small>
              )}
            </section>
            {evidence?.search_queries && evidence.search_queries.length > 0 && (
              <section className="valuation-queries">
                <h3>检索关键词</h3>
                <div>
                  {evidence.search_queries.map((query, index) => (
                    <code key={`${query}-${index}`}>{query}</code>
                  ))}
                </div>
              </section>
            )}
            {evidence?.player_adjustments && evidence.player_adjustments.length > 0 && (
              <section className="player-adjustment-list">
                <h3>跨球员折算依据</h3>
                {evidence.player_adjustments.map((adjustment) => (
                  <article key={`${adjustment.target_player}-${adjustment.reference_player}`}>
                    <strong>
                      {adjustment.target_player} / {adjustment.reference_player} ={" "}
                      {Math.round(Number(adjustment.coefficient) * 100)}%
                    </strong>
                    <p>
                      {adjustment.segment} · {adjustment.sample_count} 组配对 · 比率范围{" "}
                      {Math.round(Number(adjustment.ratio_low) * 100)}%–
                      {Math.round(Number(adjustment.ratio_high) * 100)}%
                    </p>
                    <details>
                      <summary>查看配对成交</summary>
                      {adjustment.pairs.map((pair, index) => (
                        <div key={`${pair.target_url}-${pair.reference_url}-${index}`}>
                          <a href={pair.target_url} target="_blank" rel="noreferrer">
                            {pair.target_title} · {money(pair.target_price_base, valuation.currency)}
                          </a>
                          <a href={pair.reference_url} target="_blank" rel="noreferrer">
                            {pair.reference_title} ·{" "}
                            {money(pair.reference_price_base, valuation.currency)}
                          </a>
                        </div>
                      ))}
                    </details>
                  </article>
                ))}
              </section>
            )}
            <section>
              <h3>参考成交 · {comps.length}</h3>
              <div className="valuation-comp-list">
                {comps.map((comp) => (
                  <article key={comp.id}>
                    <div>
                      <strong>
                        {money(
                          comp.normalized_card_price ?? comp.normalized_price ?? 0,
                          valuation.currency,
                        )}
                      </strong>
                      <span>
                        {comp.tier ? `${comp.tier} · ` : ""}
                        匹配度{" "}
                        {Math.round(Number(comp.similarity ?? comp.match_confidence ?? 0) * 100)}%
                        {comp.source_reliability
                          ? ` · 来源 ${Math.round(Number(comp.source_reliability) * 100)}%`
                          : ""}
                      </span>
                    </div>
                    <p>{comp.title || "未命名成交"}</p>
                    <small>
                      {new Date(comp.occurred_at).toLocaleDateString("zh-CN")} · 原价{" "}
                      {money(comp.sale_price ?? comp.original_price ?? 0, comp.currency)}
                      {comp.shipping_price ? ` + 运费 ${money(comp.shipping_price, comp.currency)}` : ""}
                      {comp.age_days !== undefined ? ` · ${comp.age_days} 天前` : ""}
                    </small>
                    {comp.final_weight !== undefined && (
                      <small>
                        原始权重 {Number(comp.raw_weight ?? 0).toFixed(4)} · 最终权重{" "}
                        {(Number(comp.final_weight) * 100).toFixed(1)}%
                        {comp.used_for_center ? " · 参与中心估值" : " · 仅区间/异常检查"}
                      </small>
                    )}
                    {comp.adjustment_basis && (
                      <small>
                        调整：{comp.adjustment_basis}（×{comp.adjustment_factor ?? "1"}）
                      </small>
                    )}
                    {comp.source === "cardhobby" && (
                      <small>
                        卡淘 · {comp.evidence_level === "confirmed_transaction"
                          ? "交易评价确认成交"
                          : "有出价的结束拍卖（未确认付款）"}
                      </small>
                    )}
                    {comp.adjustment && (
                      <small>
                        {comp.evidence_type ? `${comp.evidence_type} · ` : ""}
                        {comp.adjustment}
                      </small>
                    )}
                    {comp.player_coefficient && comp.player_coefficient !== "1" && (
                      <small>
                        {comp.player_name} 原始参考价 × 球员系数{" "}
                        {Math.round(Number(comp.player_coefficient) * 100)}%
                      </small>
                    )}
                    <small>
                      状态：
                      {comp.condition_type === "graded"
                        ? `${comp.grade_company ?? "评级"} ${comp.grade_value ?? ""}`
                        : "裸卡"}
                    </small>
                    {comp.source_url && (
                      <a href={externalSourceUrl(comp.source_url)} target="_blank" rel="noreferrer">
                        查看原始成交来源
                      </a>
                    )}
                  </article>
                ))}
                {comps.length === 0 && <p>暂无满足匹配要求的成交证据。</p>}
              </div>
            </section>
          </>
        )}
        {!valuation && !running && (
          <div className="valuation-empty">
            <strong>{valuationStatusLabels[detail.status ?? ""] ?? "暂无估值"}</strong>
            <p>
              {detail.error_message ??
                String(detail.result_summary.reason ?? "没有找到足够可靠的真实成交。")}
            </p>
          </div>
        )}
        {awaitingIdentityConfirmation && (
          <div className="valuation-running-panel">
            <ShieldAlert size={20} />
            <div>
              <strong>已找到候选身份，等待你确认</strong>
              <p>
                系统已生成 {String(detail.result_summary.candidate_count ?? 0)} 个候选；
                确认前不会产生或覆盖价格。
              </p>
              <button className="primary-button" onClick={() => void onIdentityReview()}>
                打开身份复核并确认候选
              </button>
            </div>
          </div>
        )}
        {!running && !awaitingIdentityConfirmation && (
          <button
            className="secondary-button full"
            disabled={retrying}
            onClick={async () => {
              setRetrying(true);
              try {
                await onRetry();
              } finally {
                setRetrying(false);
              }
            }}
          >
            {retrying ? "正在发起…" : "重新发起估值"}
          </button>
        )}
        <button className="ghost-button full" onClick={onClose}>
          关闭
        </button>
      </div>
    </div>
  );
}

function MarketDialog({
  item,
  comps,
  currency,
  onClose,
  onComp,
  onEstimate,
}: {
  item: PortfolioItem;
  comps: MarketComp[];
  currency: string;
  onClose: () => void;
  onComp: (event: FormEvent<HTMLFormElement>) => void;
  onEstimate: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal market-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">MARKET EVIDENCE</p>
            <h2>{item.player_name} · 市场证据</h2>
            <p className="detail-subtitle">
              录入真实成交后，以时间和匹配度加权推导估值区间。
            </p>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <form className="comp-form" onSubmit={onComp}>
          <label>
            数据渠道
            <select name="sourceCode" defaultValue="market_import">
              <option value="market_import">其他授权导入</option>
              <option value="cardhobby">卡淘 / Card Hobby</option>
              <option value="ebay">eBay</option>
              <option value="130point">130point</option>
              <option value="weikajia">微卡家</option>
            </select>
          </label>
          <label>
            原始成交市场
            <select name="originSourceCode" defaultValue="">
              <option value="">与数据渠道相同</option>
              <option value="cardhobby">卡淘 / Card Hobby</option>
              <option value="ebay">eBay</option>
              <option value="weikajia">微卡家</option>
            </select>
          </label>
          <label>
            成交日期
            <input name="occurredAt" type="date" required />
          </label>
          <label>
            成交价
            <input
              name="salePrice"
              type="number"
              min="0.01"
              step="0.01"
              required
            />
          </label>
          <label>
            运费
            <input
              name="shippingPrice"
              type="number"
              min="0"
              step="0.01"
              defaultValue="0"
              required
            />
          </label>
          <label>
            币种
            <select name="compCurrency" defaultValue={currency}>
              <option value="CNY">CNY</option>
              <option value="USD">USD</option>
            </select>
          </label>
          <label>
            入账汇率
            <input
              name="compFxRate"
              type="number"
              min="0.000001"
              step="0.000001"
              defaultValue="1"
              required
            />
          </label>
          <label>
            匹配度 %
            <input
              name="matchConfidence"
              type="number"
              min="50"
              max="100"
              defaultValue="85"
              required
            />
          </label>
          <label>
            来源可靠度 %
            <input
              name="sourceReliability"
              type="number"
              min="0"
              max="100"
              defaultValue="80"
              required
            />
          </label>
          <label className="wide">
            成交标题
            <input name="listingTitle" placeholder="平台成交标题或卡片描述" />
          </label>
          <label className="wide">
            底层成交 ID
            <input
              name="externalId"
              placeholder="例如 eBay item ID；130point 聚合记录请填原始 eBay ID"
            />
          </label>
          <label className="wide">
            证据链接
            <input name="sourceUrl" type="url" placeholder="https://..." />
          </label>
          <p className="muted-copy wide">
            130point若聚合自eBay，请将“原始成交市场”选为eBay并填写底层成交ID，
            系统会与eBay直连记录合并，避免同一成交重复计价。
          </p>
          <button className="secondary-button wide" type="submit">
            添加成交证据
          </button>
        </form>
        <div className="comp-heading">
          <h3>可比成交 · {comps.length}</h3>
          <button
            className="primary-button"
            onClick={onEstimate}
            disabled={comps.length === 0}
          >
            根据成交生成估值
          </button>
        </div>
        <div className="comp-list">
          {comps.map((comp) => (
            <article key={comp.id}>
              <div>
                <strong>
                  {money(
                    Number(comp.sale_price) + Number(comp.shipping_price),
                    comp.currency,
                  )}
                </strong>
                <small>
                  {new Date(comp.occurred_at).toLocaleDateString("zh-CN")} ·
                  匹配度 {Math.round(Number(comp.match_confidence) * 100)}%
                  {comp.classification?.source_code
                    ? ` · ${comp.classification.source_code}`
                    : ""}
                  {comp.classification?.origin_source_code &&
                  comp.classification.origin_source_code !== comp.classification.source_code
                    ? `（底层 ${comp.classification.origin_source_code}）`
                    : ""}
                </small>
              </div>
              <p>{comp.listing_title || "未填写成交标题"}</p>
              {comp.source_url && (
                <a href={externalSourceUrl(comp.source_url)} target="_blank" rel="noreferrer">
                  查看证据
                </a>
              )}
            </article>
          ))}
          {comps.length === 0 && (
            <p className="muted-copy">
              还没有可比成交。至少录入一条匹配度不低于 50% 的记录。
            </p>
          )}
        </div>
        <button className="ghost-button full" onClick={onClose}>
          关闭
        </button>
      </div>
    </div>
  );
}

function PotentialDialog({
  item,
  onClose,
  onSubmit,
}: {
  item: PortfolioItem;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal potential-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">POTENTIAL MODEL</p>
            <h2>{item.player_name} · 潜力评估</h2>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <p>
          所有输入均为 0–100 标准分；表现趋势使用 -100 到
          100。结果会保存为可复盘快照。
        </p>
        <form onSubmit={onSubmit}>
          <div className="potential-grid">
            <label>
              当前年龄
              <input name="age" type="number" min="18" max="60" required />
            </label>
            <ScoreInput name="performanceScore" label="当前表现" value={70} />
            <label>
              表现趋势
              <input
                name="performanceTrend"
                type="number"
                min="-100"
                max="100"
                defaultValue="10"
                required
              />
            </label>
            <ScoreInput name="availabilityScore" label="健康出勤" value={75} />
            <ScoreInput name="roleScore" label="球队角色" value={70} />
            <ScoreInput
              name="teamEnvironmentScore"
              label="球队环境"
              value={65}
            />
          </div>
          <div className="modal-actions">
            <button type="button" className="ghost-button" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="primary-button">
              保存潜力快照
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ImageDialog({
  item,
  onClose,
  onSubmit,
}: {
  item: PortfolioItem;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal image-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">CARD IMAGES</p>
            <h2>{item.player_name} · 卡片图片</h2>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="image-preview-grid">
          <ImagePreview label="正面" url={item.image_front_url} />
          <ImagePreview label="背面" url={item.image_back_url} />
        </div>
        <form onSubmit={onSubmit}>
          <div className="form-grid">
            <label>
              上传正面
              <input
                name="frontImage"
                type="file"
                accept="image/jpeg,image/png,image/webp"
              />
            </label>
            <label>
              上传背面
              <input
                name="backImage"
                type="file"
                accept="image/jpeg,image/png,image/webp"
              />
            </label>
          </div>
          <p className="upload-hint">
            支持 JPEG、PNG、WebP；单张大小受服务端配置限制。
          </p>
          <div className="modal-actions">
            <button type="button" className="ghost-button" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="primary-button">
              保存图片
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ImagePreview({ label, url }: { label: string; url: string | null }) {
  return (
    <figure>
      <div>
        {url ? (
          <OpenableImage src={assetUrl(url) ?? undefined} alt={`卡片${label}`} />
        ) : (
          <span>暂无{label}图片</span>
        )}
      </div>
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function RecognitionDialog({
  result,
  mode,
  batchDrafts,
  selectedBatchIndex,
  loading,
  progress,
  phase,
  error,
  scannerJobs,
  scannerResetToken,
  selectedScannerJobId,
  onClose,
  onSubmit,
  onUse,
  onModeChange,
  onSelectBatch,
  onOpenScannerJob,
  onRetryScannerJob,
  onDeleteScannerJob,
}: {
  result: CardRecognition | null;
  mode: RecognitionMode;
  batchDrafts: BatchRecognitionDraft[];
  selectedBatchIndex: number | null;
  loading: boolean;
  progress: number;
  phase: string;
  error: string;
  scannerJobs: ScannerRecognitionJob[];
  scannerResetToken: number;
  selectedScannerJobId: string | null;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onUse: (event: FormEvent<HTMLFormElement>) => void;
  onModeChange: (mode: RecognitionMode) => void;
  onSelectBatch: (index: number) => void;
  onOpenScannerJob: (job: ScannerRecognitionJob) => void;
  onRetryScannerJob: (jobId: string) => void;
  onDeleteScannerJob: (jobId: string) => void;
}) {
  const [batchFileCount, setBatchFileCount] = useState(0);

  return (
    <div className="modal-backdrop">
      <div className="modal recognition-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">AI CARD VISION</p>
            <h2>识别卡面信息</h2>
          </div>
          <button className="icon-button" onClick={onClose} disabled={loading}>
            <X size={20} />
          </button>
        </div>
        <div className="recognition-modes">
          <button
            type="button"
            disabled={loading}
            className={mode === "single" ? "active" : ""}
            onClick={() => {
              setBatchFileCount(0);
              onModeChange("single");
            }}
          >
            单卡识别
            <small>自动判断单卡或多卡</small>
          </button>
          <button
            type="button"
            disabled={loading}
            className={mode === "scanner" ? "active" : ""}
            onClick={() => {
              setBatchFileCount(0);
              onModeChange("scanner");
            }}
          >
            高拍仪扫描
            <small>NETUM 正反面采集</small>
          </button>
          <button
            type="button"
            disabled={loading}
            className={mode === "pair" ? "active" : ""}
            onClick={() => {
              setBatchFileCount(0);
              onModeChange("pair");
            }}
          >
            正反面识别
            <small>联合正面与背面</small>
          </button>
          <button
            type="button"
            disabled={loading}
            className={mode === "batch" ? "active" : ""}
            onClick={() => {
              setBatchFileCount(0);
              onModeChange("batch");
            }}
          >
            批量识别
            <small>最多 20 张单卡图</small>
          </button>
        </div>
        <form key={mode} className="recognition-form" onSubmit={onSubmit}>
          {mode === "single" && (
            <label>
              选择卡片图片或订单截图
              <input
                name="recognitionImage"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                required
              />
            </label>
          )}
          {mode === "pair" && (
            <div className="recognition-pair-inputs">
              <label>
                卡片正面
                <input
                  name="frontImage"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  required
                />
              </label>
              <label>
                卡片背面
                <input
                  name="backImage"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  required
                />
              </label>
            </div>
          )}
          {mode === "scanner" && (
            <ScannerCapture disabled={loading} resetToken={scannerResetToken} />
          )}
          {mode === "batch" && (
            <label className="batch-file-picker">
              <span>选择多张卡片图片</span>
              <input
                key="batch-images-multiple"
                name="batchImages"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple={true}
                required
                onChange={(event) =>
                  setBatchFileCount(event.currentTarget.files?.length ?? 0)
                }
              />
              <small>
                {batchFileCount > 0
                  ? `已选择 ${batchFileCount} 张图片`
                  : "在文件窗口中按住 Command 或 Shift 可一次选择多张，最多 20 张"}
              </small>
            </label>
          )}
          <button type="submit" className="primary-button" disabled={loading}>
            <Sparkles size={15} />
            {loading
              ? mode === "scanner" ? "正在裁切并入队…" : "识别处理中…"
              : mode === "batch"
                ? "开始批量识别"
                : mode === "scanner"
                  ? "加入后台识别队列"
                  : "开始识别"}
          </button>
        </form>
        {loading && (
          <div className="recognition-progress" role="status" aria-live="polite">
            <div>
              <strong>{phase}</strong>
              <span>{progress}%</span>
            </div>
            <progress max="100" value={progress} />
            <small>
              {mode === "scanner"
                ? "这里只等待本地裁切和入队；AI 识别会在后台继续执行。"
                : "视觉识别和联网核验通常需要 10–40 秒；批量任务会按图片逐张处理。"}
            </small>
          </div>
        )}
        {error && <p className="recognition-error">{error}</p>}
        {mode === "scanner" && scannerJobs.length > 0 && (
          <div className="scanner-job-list">
            <div className="batch-recognition-heading">
              <strong>后台识别队列</strong>
              <span>
                {scannerJobs.filter((job) => ["queued", "running"].includes(job.status)).length} 个处理中
              </span>
            </div>
            {scannerJobs.map((job) => (
              <article
                key={job.id}
                className={selectedScannerJobId === job.id ? "active" : ""}
              >
                <div className="scanner-job-images">
                  <OpenableImage src={assetUrl(job.front_image_url) ?? undefined} alt="自动裁切后的卡片正面" />
                  <OpenableImage src={assetUrl(job.back_image_url) ?? undefined} alt="自动裁切后的卡片背面" />
                </div>
                <div className="scanner-job-copy">
                  <strong>
                    {job.status === "queued" && "等待识别"}
                    {job.status === "running" && "AI 识别中"}
                    {job.status === "succeeded" && (job.result?.player_name || "识别完成")}
                    {job.status === "failed" && "识别失败"}
                    {job.status === "cancelled" && "已取消"}
                  </strong>
                  <small>
                    正面{job.front_transformed ? "已自动裁切" : "保留原图"} · 背面
                    {job.back_transformed ? "已自动裁切" : "保留原图"}
                  </small>
                  {job.front_orientation_method !== "pending" && (
                    <small>
                      方向：正面 {job.front_rotation_degrees}° · 背面 {job.back_rotation_degrees}°
                      {job.front_orientation_method === "vision_model" || job.back_orientation_method === "vision_model"
                        ? "（视觉模型复核）"
                        : job.front_orientation_method === "unresolved" || job.back_orientation_method === "unresolved"
                          ? "（建议录入后人工检查）"
                          : "（本地识别）"}
                    </small>
                  )}
                  {job.error_message && <small className="candidate-conflict">{job.error_message}</small>}
                </div>
                <div className="scanner-job-actions">
                  {job.status === "succeeded" && (
                    <button type="button" className="ghost-button" onClick={() => onOpenScannerJob(job)}>
                      查看并录入
                    </button>
                  )}
                  {job.status === "failed" && job.attempt_count < 3 && (
                    <button type="button" className="ghost-button" onClick={() => onRetryScannerJob(job.id)}>
                      重试
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-button scanner-delete-button"
                    aria-label={`删除 ${job.result?.player_name || "扫描任务"}`}
                    title="删除扫描任务"
                    onClick={() => onDeleteScannerJob(job.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
        {batchDrafts.length > 1 && (
          <div className="batch-recognition-list">
            <div className="batch-recognition-heading">
              <strong>识别队列</strong>
              <span>{batchDrafts.length} 张待处理</span>
            </div>
            {batchDrafts.map((draft, index) => (
              <article
                key={`${draft.filename}-${index}`}
                className={selectedBatchIndex === index ? "active" : ""}
              >
                <FileThumbnail file={draft.file} alt={`识别卡牌 ${index + 1}`} />
                <button
                  type="button"
                  disabled={!draft.result}
                  onClick={() => onSelectBatch(index)}
                >
                  <span>{draft.filename}</span>
                  <strong>
                    {draft.error
                      ? `失败：${draft.error}`
                      : draft.result?.player_name || "待人工确认"}
                  </strong>
                  <small>{draft.result ? "校正并录入" : "无法录入"}</small>
                  {draft.acquisition?.purchase_price && (
                    <small>
                      {draft.acquisition.currency ?? "CNY"} {draft.acquisition.purchase_price}
                      {draft.acquisition.order_number
                        ? ` · 订单 ${draft.acquisition.order_number}`
                        : ""}
                    </small>
                  )}
                </button>
              </article>
            ))}
          </div>
        )}
        {result && (
          <div className="recognition-result">
            {selectedBatchIndex !== null && (
              <div className="recognized-crop-preview">
                <FileThumbnail
                  file={batchDrafts[selectedBatchIndex]?.file ?? null}
                  alt="当前识别卡牌裁剪预览"
                />
                <span>
                  {batchDrafts[selectedBatchIndex]?.file
                    ? "卡牌图片已自动裁剪，录入后将保存到卡牌列表"
                    : "未能可靠定位卡片图片，本次不会保存错误截图"}
                </span>
              </div>
            )}
            <div className="recognition-confidence">
              <span>整体置信度</span>
              <strong>{Math.round(Number(result.confidence) * 100)}%</strong>
            </div>
            {result.search_queries?.length > 0 && (
              <div className="recognition-search-evidence">
                <div className="recognition-search-heading">
                  <strong>联网目录核验</strong>
                  <span>{result.web_candidates.length} 个候选</span>
                </div>
                <div className="recognition-queries">
                  {result.search_queries.map((query, index) => (
                    <code key={`${query}-${index}`}>{query}</code>
                  ))}
                </div>
                {Object.entries(result.field_evidence).map(([field, evidence]) => {
                  const descriptions = evidence.filter((value) => !isEvidenceUrl(value));
                  const sourceCount = evidence.length - descriptions.length;
                  return (
                    <div className="field-evidence-row" key={field}>
                      <strong>{recognitionFieldLabels[field] ?? field}</strong>
                      <span>
                        {descriptions.length > 0
                          ? descriptions.join("；")
                          : sourceCount > 0
                            ? `已找到 ${sourceCount} 个来源，待结合候选人工确认`
                            : "已参与候选匹配"}
                      </span>
                    </div>
                  );
                })}
                {result.web_candidates.map((candidate, index) => (
                  <article key={`${candidate.title}-${index}`}>
                    <div>
                      <strong>{candidate.title}</strong>
                      <span>
                        匹配度 {Math.round(Number(candidate.match_confidence) * 100)}%
                      </span>
                    </div>
                    {candidate.matching_fields.length > 0 && (
                      <p>匹配：{candidate.matching_fields.join("、")}</p>
                    )}
                    {candidate.conflicts.length > 0 && (
                      <p className="candidate-conflict">
                        冲突：{candidate.conflicts.join("、")}
                      </p>
                    )}
                    <small className="candidate-source-count">
                      {candidate.source_urls.length > 0
                        ? `${candidate.source_urls.length} 个参考来源`
                        : "没有可验证来源"}
                    </small>
                  </article>
                ))}
                {recognitionSourceUrls(result).length > 0 && (
                  <details className="recognition-sources">
                    <summary>
                      参考来源 · {recognitionSourceUrls(result).length} 个（已去重）
                    </summary>
                    <div>
                      {recognitionSourceUrls(result).map((url, index) => {
                        return (
                          <a
                            href={url}
                            key={`${url}-${index}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <span>{evidenceSourceHost(url)}</span>
                            <small>来源 {index + 1}</small>
                          </a>
                        );
                      })}
                    </div>
                  </details>
                )}
              </div>
            )}
            <p className="recognition-edit-hint">
              AI 结果仅作为草稿。请检查并修改后再带入录卡。
            </p>
            <form className="recognition-edit-form" onSubmit={onUse}>
              <div className="form-grid">
                <label className="wide">
                  球员
                  <input name="playerName" defaultValue={result.player_name ?? ""} />
                </label>
                <label>
                  赛季起始年
                  <input
                    name="seasonStart"
                    type="number"
                    min="1940"
                    max="2200"
                    defaultValue={result.season_start ?? ""}
                  />
                </label>
                <label>
                  赛季结束年
                  <input
                    name="seasonEnd"
                    type="number"
                    min="1940"
                    max="2200"
                    defaultValue={result.season_end ?? ""}
                  />
                </label>
                <label>
                  品牌
                  <input name="brandName" defaultValue={result.brand_name ?? ""} />
                </label>
                <label>
                  产品线
                  <input name="productName" defaultValue={result.product_name ?? ""} />
                </label>
                <label>
                  Insert / 卡种
                  <input name="insertName" defaultValue={result.insert_name ?? ""} />
                </label>
                <label>
                  Parallel / 平行版本
                  <input name="parallelName" defaultValue={result.parallel_name ?? ""} />
                </label>
                <label>
                  卡号
                  <input name="cardNumber" defaultValue={result.card_number ?? ""} />
                </label>
                <label>
                  编号
                  <input
                    name="serialNumber"
                    type="number"
                    min="1"
                    defaultValue={result.serial_number ?? ""}
                  />
                </label>
                <label>
                  总限编
                  <input
                    name="serialTotal"
                    type="number"
                    min="1"
                    defaultValue={result.serial_total ?? ""}
                  />
                </label>
                <fieldset className="wide">
                  <legend>卡片属性</legend>
                  <label className="check">
                    <input name="isRookie" type="checkbox" defaultChecked={result.is_rookie} /> RC
                  </label>
                  <label className="check">
                    <input
                      name="isAutograph"
                      type="checkbox"
                      defaultChecked={result.is_autograph}
                    />{" "}
                    Auto
                  </label>
                  <label className="check">
                    <input name="isPatch" type="checkbox" defaultChecked={result.is_patch} /> Patch
                  </label>
                </fieldset>
              </div>
              <button type="submit" className="primary-button full">
                确认修改并带入录卡
              </button>
            </form>
            {result.caveats.length > 0 && (
              <div className="recognition-caveats">
                <strong>需人工确认</strong>
                {result.caveats.map((value, index) => (
                  <p key={`${value}-${index}`}>{value}</p>
                ))}
              </div>
            )}
          </div>
        )}
        <button className="ghost-button full" onClick={onClose}>
          关闭
        </button>
      </div>
    </div>
  );
}

type ScannerCapturePreferences = {
  zoom: number;
  zoomCenter: { x: number; y: number };
  brightness: number;
  exposureMode: "auto" | "manual" | null;
  exposureTime: number | null;
};

const SCANNER_PREFERENCES_KEY = "card-platform:scanner-capture-preferences:v1";
const DEFAULT_SCANNER_PREFERENCES: ScannerCapturePreferences = {
  zoom: 1,
  zoomCenter: { x: 0.5, y: 0.5 },
  brightness: 0.85,
  exposureMode: null,
  exposureTime: null,
};

function readScannerCapturePreferences(): ScannerCapturePreferences {
  if (typeof window === "undefined") return DEFAULT_SCANNER_PREFERENCES;
  try {
    const stored = JSON.parse(localStorage.getItem(SCANNER_PREFERENCES_KEY) || "null") as Partial<ScannerCapturePreferences> | null;
    const zoom = Math.min(3, Math.max(1, Number(stored?.zoom) || 1));
    const halfVisible = 1 / (2 * zoom);
    const centerX = Number(stored?.zoomCenter?.x);
    const centerY = Number(stored?.zoomCenter?.y);
    const brightness = Number(stored?.brightness);
    const exposureTime = Number(stored?.exposureTime);
    return {
      zoom,
      zoomCenter: {
        x: Math.min(1 - halfVisible, Math.max(halfVisible, Number.isFinite(centerX) ? centerX : 0.5)),
        y: Math.min(1 - halfVisible, Math.max(halfVisible, Number.isFinite(centerY) ? centerY : 0.5)),
      },
      brightness: Math.min(1, Math.max(0.55, Number.isFinite(brightness) ? brightness : 0.85)),
      exposureMode: stored?.exposureMode === "auto" || stored?.exposureMode === "manual"
        ? stored.exposureMode
        : null,
      exposureTime: Number.isFinite(exposureTime) && exposureTime > 0 ? exposureTime : null,
    };
  } catch {
    return DEFAULT_SCANNER_PREFERENCES;
  }
}

function ScannerCapture({
  disabled,
  resetToken,
}: {
  disabled: boolean;
  resetToken: number;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const startRequestRef = useRef(0);
  const panRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const frontInputRef = useRef<HTMLInputElement>(null);
  const backInputRef = useRef<HTMLInputElement>(null);
  const frontRawInputRef = useRef<HTMLInputElement>(null);
  const backRawInputRef = useRef<HTMLInputElement>(null);
  const initialPreferences = useMemo(readScannerCapturePreferences, []);
  const preferencesRef = useRef<ScannerCapturePreferences>(initialPreferences);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [front, setFront] = useState<File | null>(null);
  const [back, setBack] = useState<File | null>(null);
  const [frontResolution, setFrontResolution] = useState("");
  const [backResolution, setBackResolution] = useState("");
  const [cameraError, setCameraError] = useState("");
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraStatus, setCameraStatus] = useState("准备自动连接高拍仪…");
  const [cameraStarting, setCameraStarting] = useState(false);
  const [zoom, setZoom] = useState(preferencesRef.current.zoom);
  const [zoomCenter, setZoomCenter] = useState(preferencesRef.current.zoomCenter);
  const [brightness, setBrightness] = useState(preferencesRef.current.brightness);
  const [hardwareControls, setHardwareControls] = useState<ScannerCameraControls | null>(null);
  const [hardwareControlError, setHardwareControlError] = useState("");
  const [hardwareControlBusy, setHardwareControlBusy] = useState(false);
  const [exposureDraft, setExposureDraft] = useState(1);

  function rememberPreferences(changes: Partial<ScannerCapturePreferences>) {
    const next = { ...preferencesRef.current, ...changes };
    preferencesRef.current = next;
    try {
      localStorage.setItem(SCANNER_PREFERENCES_KEY, JSON.stringify(next));
    } catch {
      // Camera controls still work when browser storage is unavailable; only
      // cross-session restoration is skipped.
    }
  }

  function constrainZoomCenter(center: { x: number; y: number }, nextZoom = zoom) {
    const halfVisible = 1 / (2 * nextZoom);
    return {
      x: Math.min(1 - halfVisible, Math.max(halfVisible, center.x)),
      y: Math.min(1 - halfVisible, Math.max(halfVisible, center.y)),
    };
  }

  const stopCamera = useCallback(() => {
    startRequestRef.current += 1;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setCameraActive(false);
  }, []);

  useEffect(() => stopCamera, [stopCamera]);

  useEffect(() => {
    void startCamera();
    // Entering scanner mode mounts this component; connect exactly once for
    // that scanner session. Device changes explicitly reconnect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setFront(null);
    setBack(null);
    setFrontResolution("");
    setBackResolution("");
    const empty = new DataTransfer().files;
    if (frontInputRef.current) frontInputRef.current.files = empty;
    if (backInputRef.current) backInputRef.current.files = empty;
    if (frontRawInputRef.current) frontRawInputRef.current.files = empty;
    if (backRawInputRef.current) backRawInputRef.current.files = empty;
  }, [resetToken]);

  async function startCamera(requestedDeviceId = deviceId) {
    setCameraError("");
    stopCamera();
    const requestId = startRequestRef.current;
    setCameraStarting(true);
    setCameraStatus("正在连接摄像头…");
    try {
      let maximumResolutionRequested = Boolean(requestedDeviceId);
      let stream = requestedDeviceId
        ? await getMaximumResolutionCameraStream(requestedDeviceId)
        : await getCameraStream({
            audio: false,
            video: { width: { ideal: 5120 }, height: { ideal: 3840 } },
          });
      const available = (await navigator.mediaDevices.enumerateDevices()).filter(
        (device) => device.kind === "videoinput",
      );
      const preferred = requestedDeviceId
        ? available.find((device) => device.deviceId === requestedDeviceId)
        : available.find((device) => /NETUM|KornVision/i.test(device.label));
      const activeId = stream.getVideoTracks()[0]?.getSettings().deviceId ?? "";
      if (
        preferred?.deviceId &&
        (preferred.deviceId !== activeId || !maximumResolutionRequested)
      ) {
        stream.getTracks().forEach((track) => track.stop());
        stream = await getMaximumResolutionCameraStream(preferred.deviceId);
        maximumResolutionRequested = true;
      }
      if (requestId !== startRequestRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      const track = stream.getVideoTracks()[0];
      track.addEventListener("ended", () => {
        if (streamRef.current === stream) {
          setCameraActive(false);
          setCameraStatus("高拍仪视频流已停止");
        }
      });
      if (videoRef.current) videoRef.current.srcObject = stream;
      setDevices(available);
      const settings = track.getSettings();
      const finalDeviceId = settings.deviceId ?? preferred?.deviceId ?? "";
      if (finalDeviceId) setDeviceId(finalDeviceId);
      const selected = available.find((device) => device.deviceId === finalDeviceId);
      const atMaximumResolution = settings.width === 5120 && settings.height === 3840;
      setCameraActive(true);
      setCameraStatus(
        `${selected?.label || track.label || "摄像头"} 已连接 · ${settings.width || "?"}×${settings.height || "?"}${atMaximumResolution ? " · 最大分辨率" : maximumResolutionRequested ? " · 已自动降级" : ""}`,
      );
      void refreshHardwareControls();
    } catch (reason) {
      if (requestId !== startRequestRef.current) return;
      setCameraActive(false);
      setCameraStatus("连接失败");
      setCameraError(
        reason instanceof DOMException && reason.name === "NotAllowedError"
          ? "摄像头权限未开启，请在系统设置 → 隐私与安全性 → 摄像头中允许当前浏览器。"
          : reason instanceof DOMException && reason.name === "NotReadableError"
            ? "高拍仪正被其他程序占用，请关闭预览软件后重试。"
          : reason instanceof Error
            ? reason.message
            : "无法启动高拍仪",
      );
    } finally {
      if (requestId === startRequestRef.current) setCameraStarting(false);
    }
  }

  async function refreshHardwareControls() {
    setHardwareControlError("");
    try {
      let controls = await getScannerCameraControls();
      const preferences = preferencesRef.current;
      if (preferences.exposureMode) {
        const requestedExposure = preferences.exposureTime === null
          ? controls.exposure_time.current
          : Math.min(
              controls.exposure_time.maximum,
              Math.max(controls.exposure_time.minimum, preferences.exposureTime),
            );
        const modeChanged = controls.exposure_mode !== preferences.exposureMode;
        const exposureChanged =
          preferences.exposureMode === "manual" &&
          Math.round(controls.exposure_time.current) !== Math.round(requestedExposure);
        if (modeChanged || exposureChanged) {
          controls = await updateScannerCameraControls({
            exposure_mode: preferences.exposureMode,
            exposure_time: preferences.exposureMode === "manual" ? requestedExposure : undefined,
          });
        }
      }
      setHardwareControls(controls);
      setExposureDraft(controls.exposure_time.current);
    } catch (reason) {
      setHardwareControls(null);
      setHardwareControlError(
        reason instanceof Error ? reason.message : "无法读取高拍仪硬件曝光设置",
      );
    }
  }

  async function changeExposureMode(mode: "auto" | "manual") {
    setHardwareControlBusy(true);
    setHardwareControlError("");
    try {
      const controls = await updateScannerCameraControls({ exposure_mode: mode });
      setHardwareControls(controls);
      setExposureDraft(controls.exposure_time.current);
      rememberPreferences({
        exposureMode: controls.exposure_mode,
        exposureTime: controls.exposure_time.current,
      });
    } catch (reason) {
      setHardwareControlError(reason instanceof Error ? reason.message : "曝光模式切换失败");
    } finally {
      setHardwareControlBusy(false);
    }
  }

  async function applyManualExposure() {
    setHardwareControlBusy(true);
    setHardwareControlError("");
    try {
      const controls = await updateScannerCameraControls({
        exposure_mode: "manual",
        exposure_time: exposureDraft,
      });
      setHardwareControls(controls);
      setExposureDraft(controls.exposure_time.current);
      rememberPreferences({
        exposureMode: "manual",
        exposureTime: controls.exposure_time.current,
      });
    } catch (reason) {
      setHardwareControlError(reason instanceof Error ? reason.message : "手动曝光设置失败");
    } finally {
      setHardwareControlBusy(false);
    }
  }

  async function capture(side: "front" | "back") {
    const video = videoRef.current;
    const track = streamRef.current?.getVideoTracks()[0];
    if (!video?.videoWidth || !video.videoHeight || !track) {
      setCameraError("视频画面尚未就绪，请等待预览出现后再拍摄。");
      return;
    }
    setCameraError("");
    let captured: Awaited<ReturnType<typeof captureMaximumResolutionFrame>>;
    try {
      captured = await captureMaximumResolutionFrame(video, track);
    } catch (reason) {
      setCameraError(reason instanceof Error ? reason.message : "最高分辨率拍摄失败，请重试。");
      return;
    }
    const canvas = document.createElement("canvas");
    const originalWidth = captured.bitmap.width;
    const originalHeight = captured.bitmap.height;
    const sourceWidth = captured.bitmap.width / zoom;
    const sourceHeight = captured.bitmap.height / zoom;
    const sourceX = Math.min(
      captured.bitmap.width - sourceWidth,
      Math.max(0, zoomCenter.x * captured.bitmap.width - sourceWidth / 2),
    );
    const sourceY = Math.min(
      captured.bitmap.height - sourceHeight,
      Math.max(0, zoomCenter.y * captured.bitmap.height - sourceHeight / 2),
    );
    canvas.width = Math.max(1, Math.round(sourceWidth));
    canvas.height = Math.max(1, Math.round(sourceHeight));
    const context = canvas.getContext("2d");
    if (!context) return;
    context.filter = `brightness(${brightness})`;
    let rawBlob = captured.sourceBlob;
    let rawCanvas: HTMLCanvasElement | null = null;
    try {
      if (!rawBlob) {
        rawCanvas = document.createElement("canvas");
        rawCanvas.width = originalWidth;
        rawCanvas.height = originalHeight;
        const rawContext = rawCanvas.getContext("2d");
        if (!rawContext) return;
        rawContext.drawImage(captured.bitmap, 0, 0);
      }
      context.drawImage(
        captured.bitmap,
        sourceX,
        sourceY,
        sourceWidth,
        sourceHeight,
        0,
        0,
        canvas.width,
        canvas.height,
      );
    } finally {
      captured.bitmap.close();
    }
    if (!rawBlob && rawCanvas) {
      rawBlob = await new Promise<Blob | null>((resolve) =>
        rawCanvas?.toBlob(resolve, "image/jpeg", 0.97),
      );
    }
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));
    if (!blob || !rawBlob) return;
    const file = new File([blob], `netum-${side}-${Date.now()}.jpg`, {
      type: "image/jpeg",
    });
    const rawType = rawBlob.type || "image/jpeg";
    const rawExtension = rawType === "image/png" ? "png" : rawType === "image/webp" ? "webp" : "jpg";
    const rawFile = new File([rawBlob], `netum-${side}-raw-${Date.now()}.${rawExtension}`, {
      type: rawType,
    });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    const rawTransfer = new DataTransfer();
    rawTransfer.items.add(rawFile);
    const resolution = `原片 ${originalWidth}×${originalHeight} · 识别图 ${canvas.width}×${canvas.height}${captured.method === "video-frame" ? "（视频帧）" : ""}`;
    if (side === "front") {
      setFront(file);
      setFrontResolution(resolution);
      if (frontInputRef.current) frontInputRef.current.files = transfer.files;
      if (frontRawInputRef.current) frontRawInputRef.current.files = rawTransfer.files;
    } else {
      setBack(file);
      setBackResolution(resolution);
      if (backInputRef.current) backInputRef.current.files = transfer.files;
      if (backRawInputRef.current) backRawInputRef.current.files = rawTransfer.files;
    }
  }

  function moveZoomViewport(event: React.PointerEvent<HTMLDivElement>) {
    const previous = panRef.current;
    if (!previous || previous.pointerId !== event.pointerId || zoom <= 1) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const deltaX = event.clientX - previous.x;
    const deltaY = event.clientY - previous.y;
    panRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    setZoomCenter((current) => {
      const nextCenter = constrainZoomCenter({
        x: current.x - deltaX / (bounds.width * zoom),
        y: current.y - deltaY / (bounds.height * zoom),
      });
      preferencesRef.current = { ...preferencesRef.current, zoomCenter: nextCenter };
      return nextCenter;
    });
  }

  const previewTranslateX = -(zoomCenter.x - 0.5) * zoom * 100;
  const previewTranslateY = -(zoomCenter.y - 0.5) * zoom * 100;

  return (
    <div className="scanner-capture">
      <div className="scanner-toolbar">
        {devices.length > 0 && (
          <select
            aria-label="摄像头设备"
            value={deviceId}
            disabled={disabled || cameraStarting}
            onChange={(event) => {
              const next = event.currentTarget.value;
              setDeviceId(next);
              void startCamera(next);
            }}
          >
            {devices.map((device, index) => (
              <option key={device.deviceId} value={device.deviceId}>
                {device.label || `摄像头 ${index + 1}`}
              </option>
            ))}
          </select>
        )}
        {!cameraStarting && !cameraActive && cameraError && (
          <button type="button" className="ghost-button" onClick={() => void startCamera()} disabled={disabled}>
            <Camera size={15} /> 重新连接
          </button>
        )}
      </div>
      <div
        className={zoom > 1 ? "scanner-video-stage zoomed" : "scanner-video-stage"}
        onPointerDown={(event) => {
          if (zoom <= 1) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          panRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
        }}
        onPointerMove={moveZoomViewport}
        onPointerUp={(event) => {
          if (panRef.current?.pointerId === event.pointerId) {
            panRef.current = null;
            rememberPreferences({ zoomCenter: preferencesRef.current.zoomCenter });
          }
        }}
        onPointerCancel={() => {
          panRef.current = null;
          rememberPreferences({ zoomCenter: preferencesRef.current.zoomCenter });
        }}
      >
        <video
          ref={videoRef}
          className="scanner-video"
          style={{
            transform: `translate(${previewTranslateX}%, ${previewTranslateY}%) scale(${zoom})`,
            filter: `brightness(${brightness})`,
          }}
          autoPlay
          playsInline
          muted
        />
        <span className="scanner-frame-guide" aria-hidden="true" />
      </div>
      <div className="scanner-zoom-controls">
        <label htmlFor="scanner-zoom">数字变焦</label>
        <input
          id="scanner-zoom"
          type="range"
          min="1"
          max="3"
          step="0.1"
          value={zoom}
          disabled={disabled || !cameraActive}
          onChange={(event) => {
            const nextZoom = Number(event.currentTarget.value);
            setZoom(nextZoom);
            setZoomCenter((current) => {
              const nextCenter = constrainZoomCenter(current, nextZoom);
              rememberPreferences({ zoom: nextZoom, zoomCenter: nextCenter });
              return nextCenter;
            });
          }}
        />
        <strong>{zoom.toFixed(1)}×</strong>
        <button
          type="button"
          className="ghost-button"
          disabled={disabled || (zoom === 1 && zoomCenter.x === 0.5 && zoomCenter.y === 0.5)}
          onClick={() => {
            setZoom(1);
            setZoomCenter({ x: 0.5, y: 0.5 });
            rememberPreferences({ zoom: 1, zoomCenter: { x: 0.5, y: 0.5 } });
          }}
        >
          复位取景
        </button>
      </div>
      {hardwareControls && (
        <div className="scanner-hardware-controls">
          <div className="scanner-control-heading">
            <div>
              <strong>硬件曝光</strong>
              <small>{hardwareControls.device_name} · 数值越小画面越暗</small>
            </div>
            <select
              aria-label="曝光模式"
              value={hardwareControls.exposure_mode}
              disabled={disabled || hardwareControlBusy}
              onChange={(event) =>
                void changeExposureMode(event.currentTarget.value as "auto" | "manual")
              }
            >
              <option value="auto">自动曝光</option>
              <option value="manual">手动曝光</option>
            </select>
          </div>
          <div className="scanner-exposure-slider">
            <label htmlFor="scanner-exposure">曝光时间</label>
            <input
              id="scanner-exposure"
              type="range"
              min="0"
              max="100"
              step="1"
              value={exposureToSlider(
                exposureDraft,
                hardwareControls.exposure_time.minimum,
                hardwareControls.exposure_time.maximum,
              )}
              disabled={
                disabled || hardwareControlBusy || hardwareControls.exposure_mode !== "manual"
              }
              onChange={(event) =>
                setExposureDraft(
                  sliderToExposure(
                    Number(event.currentTarget.value),
                    hardwareControls.exposure_time.minimum,
                    hardwareControls.exposure_time.maximum,
                    hardwareControls.exposure_time.step,
                  ),
                )
              }
            />
            <strong>{Math.round(exposureDraft)}</strong>
            <button
              type="button"
              className="ghost-button"
              disabled={
                disabled || hardwareControlBusy || hardwareControls.exposure_mode !== "manual"
              }
              onClick={() => void applyManualExposure()}
            >
              {hardwareControlBusy ? "设置中…" : "应用曝光"}
            </button>
          </div>
        </div>
      )}
      {hardwareControlError && (
        <small className="scanner-control-error">
          硬件曝光不可用：{hardwareControlError}。仍可使用下面的软件亮度。
        </small>
      )}
      <div className="scanner-zoom-controls scanner-brightness-controls">
        <label htmlFor="scanner-brightness">画面亮度</label>
        <input
          id="scanner-brightness"
          type="range"
          min="0.55"
          max="1"
          step="0.05"
          value={brightness}
          disabled={disabled || !cameraActive}
          onChange={(event) => {
            const nextBrightness = Number(event.currentTarget.value);
            setBrightness(nextBrightness);
            rememberPreferences({ brightness: nextBrightness });
          }}
        />
        <strong>{Math.round(brightness * 100)}%</strong>
        <button
          type="button"
          className="ghost-button"
          disabled={disabled || brightness === 0.85}
          onClick={() => {
            setBrightness(0.85);
            rememberPreferences({ brightness: 0.85 });
          }}
        >
          恢复默认
        </button>
      </div>
      <small className="scanner-placement-hint">
        放卡方向不限；放大后可按住画面拖动取景，系统会裁切、透视矫正并自动转正。
      </small>
      <small className={cameraActive ? "scanner-status active" : "scanner-status"}>
        {cameraStatus}
      </small>
      {cameraError && <p className="recognition-error">{cameraError}</p>}
      <div className="scanner-shots">
        <button type="button" className="ghost-button" disabled={disabled || !cameraActive} onClick={() => void capture("front")}>
          拍摄正面
        </button>
        <span>{front ? `正面已拍摄 · ${frontResolution}` : "等待正面"}</span>
        <button type="button" className="ghost-button" disabled={disabled || !cameraActive} onClick={() => void capture("back")}>
          拍摄背面
        </button>
        <span>{back ? `背面已拍摄 · ${backResolution}` : "等待背面"}</span>
      </div>
      <input ref={frontInputRef} name="frontImage" type="file" className="scanner-file-input" required />
      <input ref={backInputRef} name="backImage" type="file" className="scanner-file-input" required />
      <input ref={frontRawInputRef} name="frontRawImage" type="file" className="scanner-file-input" />
      <input ref={backRawInputRef} name="backRawImage" type="file" className="scanner-file-input" />
    </div>
  );
}

const NETUM_CAPTURE_RESOLUTIONS = [
  [5120, 3840],
  [4904, 3678],
  [4624, 3468],
] as const;

type BrowserImageCapture = {
  getPhotoCapabilities: () => Promise<{
    imageWidth?: { max: number };
    imageHeight?: { max: number };
  }>;
  takePhoto: (settings?: { imageWidth?: number; imageHeight?: number }) => Promise<Blob>;
};

type BrowserImageCaptureConstructor = new (track: MediaStreamTrack) => BrowserImageCapture;

async function captureMaximumResolutionFrame(
  video: HTMLVideoElement,
  track: MediaStreamTrack,
): Promise<{
  bitmap: ImageBitmap;
  method: "photo" | "video-frame";
  sourceBlob: Blob | null;
}> {
  const ImageCaptureApi = (
    window as typeof window & { ImageCapture?: BrowserImageCaptureConstructor }
  ).ImageCapture;
  if (ImageCaptureApi) {
    try {
      const capture = new ImageCaptureApi(track);
      const capabilities = await capture.getPhotoCapabilities();
      const imageWidth = capabilities.imageWidth?.max;
      const imageHeight = capabilities.imageHeight?.max;
      const photo = await capture.takePhoto(
        imageWidth && imageHeight ? { imageWidth, imageHeight } : undefined,
      );
      const bitmap = await createImageBitmap(photo);
      if (bitmap.width >= video.videoWidth && bitmap.height >= video.videoHeight) {
        return { bitmap, method: "photo", sourceBlob: photo };
      }
      bitmap.close();
    } catch {
      // Some Chromium/macOS UVC combinations expose ImageCapture but reject
      // still-photo settings. The negotiated maximum-resolution video frame is
      // the safe fallback and still preserves the original sensor pixels.
    }
  }
  return { bitmap: await createImageBitmap(video), method: "video-frame", sourceBlob: null };
}

async function getMaximumResolutionCameraStream(deviceId: string): Promise<MediaStream> {
  for (const [width, height] of NETUM_CAPTURE_RESOLUTIONS) {
    try {
      return await getCameraStream({
        audio: false,
        video: {
          deviceId: { exact: deviceId },
          width: { exact: width },
          height: { exact: height },
        },
      });
    } catch (reason) {
      if (
        reason instanceof DOMException &&
        !["OverconstrainedError", "ConstraintNotSatisfiedError"].includes(reason.name)
      ) {
        throw reason;
      }
    }
  }
  return getCameraStream({
    audio: false,
    video: {
      deviceId: { exact: deviceId },
      width: { ideal: 5120 },
      height: { ideal: 3840 },
    },
  });
}

function exposureToSlider(value: number, minimum: number, maximum: number) {
  const safeMinimum = Math.max(0.001, minimum);
  const safeMaximum = Math.max(safeMinimum, maximum);
  const safeValue = Math.min(safeMaximum, Math.max(safeMinimum, value));
  if (safeMaximum === safeMinimum) return 0;
  return Math.round(
    ((Math.log(safeValue) - Math.log(safeMinimum)) /
      (Math.log(safeMaximum) - Math.log(safeMinimum))) *
      100,
  );
}

function sliderToExposure(value: number, minimum: number, maximum: number, step: number) {
  const safeMinimum = Math.max(0.001, minimum);
  const safeMaximum = Math.max(safeMinimum, maximum);
  const raw = Math.exp(
    Math.log(safeMinimum) + (value / 100) * (Math.log(safeMaximum) - Math.log(safeMinimum)),
  );
  const safeStep = Math.max(0.001, step);
  return Math.min(maximum, Math.max(minimum, Math.round(raw / safeStep) * safeStep));
}

function getCameraStream(constraints: MediaStreamConstraints): Promise<MediaStream> {
  return new Promise((resolve, reject) => {
    let finished = false;
    const timer = window.setTimeout(() => {
      finished = true;
      reject(
        new Error(
          "等待摄像头授权超时。请点击浏览器地址栏左侧的摄像头图标，允许访问后重新连接。",
        ),
      );
    }, 15000);
    navigator.mediaDevices.getUserMedia(constraints).then(
      (stream) => {
        if (finished) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        finished = true;
        window.clearTimeout(timer);
        resolve(stream);
      },
      (reason) => {
        if (finished) return;
        finished = true;
        window.clearTimeout(timer);
        reject(reason);
      },
    );
  });
}

function FileThumbnail({ file, alt }: { file: File | null; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!file) {
      setUrl(null);
      return;
    }
    const nextUrl = URL.createObjectURL(file);
    setUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [file]);

  return url ? <OpenableImage className="recognized-crop" src={url} alt={alt} /> : <span>无裁剪图</span>;
}
