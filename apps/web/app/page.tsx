"use client";

import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  ChevronRight,
  CircleDollarSign,
  LayoutDashboard,
  Menu,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  addTransaction,
  addValuation,
  addMarketComp,
  analyzeItem,
  automaticallyAnalyzeItem,
  createItem,
  createUser,
  getPortfolio,
  generateNarrative,
  syncPlayer,
  assessPotential,
  getPlayerIntelligence,
  getItemDetail,
  getMarketComps,
  estimateMarket,
  recognizeCard,
  recognizeCardBatch,
  recognizeCardPair,
  uploadItemImage,
  assetUrl,
  type AnalysisResult,
  type AINarrativeResult,
  type PortfolioItem,
  type PortfolioSummary,
  type PlayerIntelligence,
  type ItemDetail,
  type MarketComp,
  type CardRecognition,
  type BatchCardRecognition,
} from "@/lib/api";

type RecognitionMode = "single" | "pair" | "batch";
type BatchRecognitionDraft = BatchCardRecognition & { file: File };

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

const OWNER_KEY = "card-alpha-owner-id";

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

export default function Dashboard() {
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [summary, setSummary] = useState(emptySummary);
  const [items, setItems] = useState<PortfolioItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [showSetup, setShowSetup] = useState(false);
  const [showAddCard, setShowAddCard] = useState(false);
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
  const [marketComps, setMarketComps] = useState<MarketComp[]>([]);
  const [marketItem, setMarketItem] = useState<PortfolioItem | null>(null);
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
  const [cardDraft, setCardDraft] = useState<CardRecognition | null>(null);

  const refresh = useCallback(async (id: string) => {
    setLoading(true);
    setError("");
    try {
      const [nextSummary, nextItems] = await getPortfolio(id);
      setSummary(nextSummary);
      setItems(nextItems);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "暂时无法读取卡库");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const savedOwner = window.localStorage.getItem(OWNER_KEY);
    if (!savedOwner) {
      setLoading(false);
      setShowSetup(true);
      return;
    }
    setOwnerId(savedOwner);
    void refresh(savedOwner);
  }, [refresh]);

  const filteredItems = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    if (!keyword) return items;
    return items.filter((item) =>
      [item.player_name, item.brand_name, item.product_name, item.insert_name]
        .filter(Boolean)
        .some((value) => value?.toLocaleLowerCase().includes(keyword)),
    );
  }, [items, search]);

  const unrealized = Number(summary.unrealized_profit);
  const returnRate = Number(summary.total_cost)
    ? (unrealized / Number(summary.total_cost)) * 100
    : 0;

  async function handleSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    try {
      const result = await createUser({
        email: String(form.get("email")),
        display_name: String(form.get("displayName")),
        base_currency: String(form.get("currency")),
      });
      window.localStorage.setItem(OWNER_KEY, result.id);
      setOwnerId(result.id);
      setShowSetup(false);
      await refresh(result.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建账户失败");
    }
  }

  async function handleAddCard(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId) return;
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
          transacted_at: new Date().toISOString(),
          price: form.get("price"),
          currency: form.get("currency"),
          fx_rate_to_base: 1,
        },
      });
      if (cardDraft && recognitionFile) {
        await uploadItemImage(created.id, ownerId, "front", recognitionFile);
      }
      if (cardDraft && recognitionBackFile) {
        await uploadItemImage(created.id, ownerId, "back", recognitionBackFile);
      }
      setShowAddCard(false);
      setCardDraft(null);
      if (recognitionMode === "batch" && selectedBatchIndex !== null) {
        const remaining = batchDrafts.filter((_, index) => index !== selectedBatchIndex);
        setBatchDrafts(remaining);
        setSelectedBatchIndex(null);
        setRecognitionResult(null);
        setRecognitionFile(null);
        setRecognitionBackFile(null);
        setShowRecognition(remaining.length > 0);
      } else {
        setRecognitionFile(null);
        setRecognitionBackFile(null);
      }
      await refresh(ownerId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "录入卡片失败");
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
        setRecognitionResult(await recognizeCard(file));
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
      } else {
        const input = event.currentTarget.elements.namedItem("batchImages");
        if (!(input instanceof HTMLInputElement) || !input.files?.length) return;
        const files = Array.from(input.files);
        const results = await recognizeCardBatch(files);
        setBatchDrafts(results.map((result, index) => ({ ...result, file: files[index] })));
        setRecognitionResult(null);
        setSelectedBatchIndex(null);
      }
      setRecognitionProgress(100);
      setRecognitionPhase("识别完成，正在整理结果");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "卡片图片识别失败");
      setRecognitionPhase("识别失败");
    } finally {
      window.clearInterval(progressTimer);
      window.setTimeout(() => setRecognitionLoading(false), 350);
    }
  }

  function selectBatchDraft(index: number) {
    const draft = batchDrafts[index];
    if (!draft?.result) return;
    setSelectedBatchIndex(index);
    setRecognitionFile(draft.file);
    setRecognitionBackFile(null);
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
    try {
      const [detail, comps] = await Promise.all([
        getItemDetail(itemId, ownerId),
        getMarketComps(itemId, ownerId),
      ]);
      setItemDetail(detail);
      setMarketComps(comps);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "持仓详情读取失败");
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

  async function handleTransaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ownerId || !itemDetail) return;
    const form = new FormData(event.currentTarget);
    try {
      await addTransaction(itemDetail.item.id, {
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
      await addValuation(itemDetail.item.id, {
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
        </nav>
        <div className="sidebar-insight">
          <div className="eyebrow">
            <ShieldCheck size={14} /> 投资纪律
          </div>
          <strong>价格不是价值</strong>
          <p>记录买入逻辑、催化剂与退出条件，持续复盘每一次决策。</p>
        </div>
        <div className="user-chip">
          <div className="avatar">投</div>
          <div>
            <strong>我的组合</strong>
            <small>{summary.base_currency} 基准</small>
          </div>
          <ChevronRight size={16} />
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
                <small>相对持仓成本</small>
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
              <button className="text-button">
                查看全部 <ChevronRight size={16} />
              </button>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
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
                        <td>
                          <div className="card-identity">
                            <div
                              className={`card-thumb ${item.image_front_url ? "has-image" : ""}`}
                            >
                              {item.image_front_url ? (
                                <img
                                  src={
                                    assetUrl(item.image_front_url) ?? undefined
                                  }
                                  alt={`${item.player_name} 卡片正面`}
                                />
                              ) : (
                                item.player_name
                                  .split(" ")
                                  .map((part) => part[0])
                                  .join("")
                                  .slice(0, 2)
                              )}
                            </div>
                            <div>
                              <button
                                className="player-link"
                                onClick={() =>
                                  void openPlayerIntelligence(item.player_id)
                                }
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
                          {item.fair_value ? (
                            money(
                              item.fair_value,
                              item.valuation_currency ?? summary.base_currency,
                            )
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
                              className="manage-button"
                              onClick={() => void openItemDetail(item.id)}
                            >
                              管理
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {!loading && filteredItems.length === 0 && (
                    <tr>
                      <td colSpan={6}>
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
          detail={itemDetail}
          currency={summary.base_currency}
          onClose={() => setItemDetail(null)}
          onTransaction={handleTransaction}
          onValuation={handleValuation}
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
          }}
          onSelectBatch={selectBatchDraft}
        />
      )}
    </main>
  );
}

function SetupDialog({
  onSubmit,
  error,
}: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  error: string;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal setup-modal">
        <div className="modal-icon">
          <Sparkles />
        </div>
        <p className="eyebrow">WELCOME TO CARD ALPHA</p>
        <h2>建立你的投资档案</h2>
        <p>只需一个名称和邮箱。卡库数据将保存在你的数据库中。</p>
        <form onSubmit={onSubmit}>
          <label>
            显示名称
            <input
              name="displayName"
              placeholder="例如：Song 的卡库"
              required
            />
          </label>
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
            基准币种
            <select name="currency" defaultValue="CNY">
              <option value="CNY">CNY · 人民币</option>
              <option value="USD">USD · 美元</option>
            </select>
          </label>
          {error && <p className="form-error">{error}</p>}
          <button className="primary-button full" type="submit">
            进入我的组合 <ChevronRight size={17} />
          </button>
        </form>
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
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  currency: string;
  initialValues: CardRecognition | null;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal card-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">NEW POSITION</p>
            <h2>录入一张卡</h2>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <form onSubmit={onSubmit}>
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
                required
              />
            </label>
            <label>
              币种
              <select name="currency" defaultValue={currency}>
                <option value="CNY">CNY</option>
                <option value="USD">USD</option>
              </select>
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
          <div className="modal-actions">
            <button type="button" className="ghost-button" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="primary-button">
              保存到卡库
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

function ItemDetailDialog({
  detail,
  currency,
  onClose,
  onTransaction,
  onValuation,
}: {
  detail: ItemDetail;
  currency: string;
  onClose: () => void;
  onTransaction: (event: FormEvent<HTMLFormElement>) => void;
  onValuation: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal detail-modal">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">POSITION MANAGEMENT</p>
            <h2>{detail.item.player_name}</h2>
            <p className="detail-subtitle">
              {detail.item.product_name} · 剩余成本{" "}
              {money(detail.item.remaining_cost, currency)}
            </p>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="management-grid">
          <section>
            <h3>记录交易</h3>
            <form onSubmit={onTransaction} className="compact-form">
              <label>
                类型
                <select name="transactionType">
                  <option value="sell">卖出</option>
                  <option value="buy">追加买入</option>
                  <option value="fee">费用</option>
                  <option value="adjustment">成本调整</option>
                </select>
              </label>
              <label>
                数量
                <input
                  name="quantity"
                  type="number"
                  min="0.0001"
                  step="0.0001"
                  defaultValue="1"
                  required
                />
              </label>
              <label>
                单价
                <input
                  name="unitPrice"
                  type="number"
                  min="0"
                  step="0.01"
                  defaultValue="0"
                  required
                />
              </label>
              <label>
                费用
                <input
                  name="feeAmount"
                  type="number"
                  min="0"
                  step="0.01"
                  defaultValue="0"
                  required
                />
              </label>
              <label className="wide">
                渠道
                <input name="venue" placeholder="eBay / 卡店 / 卡展" />
              </label>
              <button className="secondary-button wide" type="submit">
                保存交易
              </button>
            </form>
          </section>
          <section>
            <h3>更新估值</h3>
            <form onSubmit={onValuation} className="compact-form">
              <label>
                区间下限
                <input
                  name="lowValue"
                  type="number"
                  min="0"
                  step="0.01"
                  required
                />
              </label>
              <label>
                合理价值
                <input
                  name="fairValue"
                  type="number"
                  min="0"
                  step="0.01"
                  required
                />
              </label>
              <label>
                区间上限
                <input
                  name="highValue"
                  type="number"
                  min="0"
                  step="0.01"
                  required
                />
              </label>
              <label>
                置信度 %
                <input
                  name="confidence"
                  type="number"
                  min="0"
                  max="100"
                  defaultValue="70"
                  required
                />
              </label>
              <button className="secondary-button wide" type="submit">
                保存估值快照
              </button>
            </form>
          </section>
        </div>
        <div className="history-grid">
          <section>
            <h3>交易历史</h3>
            <div className="history-list">
              {detail.transactions.map((row) => (
                <div key={row.id}>
                  <span className={`history-type ${row.transaction_type}`}>
                    {transactionLabels[row.transaction_type]}
                  </span>
                  <strong>
                    {money(
                      Number(row.unit_price) * Number(row.quantity),
                      row.currency,
                    )}
                  </strong>
                  <small>
                    {new Date(row.transacted_at).toLocaleDateString("zh-CN")} ·
                    数量 {row.quantity}
                    {Number(row.fee_amount) > 0
                      ? ` · 费用 ${money(row.fee_amount, row.currency)}`
                      : ""}
                  </small>
                </div>
              ))}
              {detail.transactions.length === 0 && <p>暂无记录</p>}
            </div>
          </section>
          <section>
            <h3>估值历史</h3>
            <div className="history-list">
              {detail.valuations.map((row) => (
                <div key={row.id}>
                  <span className="history-type valuation">估值</span>
                  <strong>{money(row.fair_value, row.currency)}</strong>
                  <small>
                    {new Date(row.valued_at).toLocaleDateString("zh-CN")} ·{" "}
                    {money(row.low_value, row.currency)} –{" "}
                    {money(row.high_value, row.currency)}
                  </small>
                </div>
              ))}
              {detail.valuations.length === 0 && <p>暂无记录</p>}
            </div>
          </section>
        </div>
        <button className="primary-button full" onClick={onClose}>
          完成
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
          <label className="wide">
            成交标题
            <input name="listingTitle" placeholder="平台成交标题或卡片描述" />
          </label>
          <label className="wide">
            证据链接
            <input name="sourceUrl" type="url" placeholder="https://..." />
          </label>
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
                </small>
              </div>
              <p>{comp.listing_title || "未填写成交标题"}</p>
              {comp.source_url && (
                <a href={comp.source_url} target="_blank" rel="noreferrer">
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
          <img src={assetUrl(url) ?? undefined} alt={`卡片${label}`} />
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
  onClose,
  onSubmit,
  onUse,
  onModeChange,
  onSelectBatch,
}: {
  result: CardRecognition | null;
  mode: RecognitionMode;
  batchDrafts: BatchRecognitionDraft[];
  selectedBatchIndex: number | null;
  loading: boolean;
  progress: number;
  phase: string;
  error: string;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onUse: (event: FormEvent<HTMLFormElement>) => void;
  onModeChange: (mode: RecognitionMode) => void;
  onSelectBatch: (index: number) => void;
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
            <small>一张图片快速提取</small>
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
              选择单张卡片图片
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
              ? "识别处理中…"
              : mode === "batch"
                ? "开始批量识别"
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
              视觉识别和联网核验通常需要 10–40 秒；批量任务会按图片逐张处理。
            </small>
          </div>
        )}
        {error && <p className="recognition-error">{error}</p>}
        {mode === "batch" && batchDrafts.length > 0 && (
          <div className="batch-recognition-list">
            <div className="batch-recognition-heading">
              <strong>识别队列</strong>
              <span>{batchDrafts.length} 张待处理</span>
            </div>
            {batchDrafts.map((draft, index) => (
              <button
                type="button"
                key={`${draft.filename}-${index}`}
                className={selectedBatchIndex === index ? "active" : ""}
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
              </button>
            ))}
          </div>
        )}
        {result && (
          <div className="recognition-result">
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
