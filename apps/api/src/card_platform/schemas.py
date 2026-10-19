from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from card_platform.news_alerts import NewsAlertView


class ApiModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class TransactionType(StrEnum):
    BUY = "buy"
    SELL = "sell"
    FEE = "fee"
    ADJUSTMENT = "adjustment"


class ValuationMethod(StrEnum):
    MARKET_COMP = "market_comp"
    LISTING = "listing"
    MANUAL = "manual"
    MODEL = "model"
    AI_ESTIMATE = "ai_estimate"


class CardIdentityInput(ApiModel):
    player_name: str = Field(min_length=1, max_length=160)
    player_birth_date: date | None = None
    brand_name: str = Field(min_length=1, max_length=120)
    manufacturer: str | None = Field(default=None, max_length=120)
    brand_tier: int | None = Field(default=None, ge=1, le=5)
    season_start: int = Field(ge=1940, le=2200)
    season_end: int | None = Field(default=None, ge=1940, le=2200)
    product_name: str = Field(min_length=1, max_length=180)
    set_name: str | None = Field(default=None, max_length=180)
    card_number: str | None = Field(default=None, max_length=80)
    insert_name: str | None = Field(default=None, max_length=180)
    parallel_name: str | None = Field(default=None, max_length=180)
    is_rookie: bool = False
    is_autograph: bool = False
    autograph_type: Literal["on_card", "sticker", "cut", "unknown", "none"] = "none"
    has_inscription: bool = False
    inscription_text: str | None = Field(default=None, max_length=500)
    is_memorabilia: bool = False
    memorabilia_types: list[str] = Field(default_factory=list)
    memorabilia_usage: Literal[
        "game_used",
        "player_worn",
        "event_worn",
        "not_from_specific_game_or_event",
        "manufactured",
        "unknown",
        "none",
    ] = "none"
    memorabilia_player: str | None = Field(default=None, max_length=160)
    is_patch: bool = False
    is_case_hit: bool = False
    print_run: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_season(self) -> "CardIdentityInput":
        if self.season_end is not None and self.season_end not in {
            self.season_start,
            self.season_start + 1,
        }:
            raise ValueError("season_end must equal season_start or season_start + 1")
        if self.inscription_text:
            self.has_inscription = True
        if self.has_inscription:
            self.is_autograph = True
        if not self.is_autograph:
            self.autograph_type = "none"
            self.has_inscription = False
            self.inscription_text = None
        elif self.autograph_type == "none":
            self.autograph_type = "unknown"
        if not self.has_inscription:
            self.inscription_text = None
        if self.is_patch:
            self.is_memorabilia = True
            if "patch" not in self.memorabilia_types:
                self.memorabilia_types.append("patch")
        if not self.is_memorabilia:
            self.memorabilia_types = []
            self.memorabilia_usage = "none"
            self.memorabilia_player = None
        elif self.memorabilia_usage == "none":
            self.memorabilia_usage = "unknown"
        return self


class PurchaseInput(ApiModel):
    transacted_at: datetime
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    fee_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    fx_rate_to_base: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=8)
    venue: str | None = Field(default=None, max_length=120)
    external_order_id: str | None = Field(default=None, max_length=160)
    notes: str | None = None


class CollectionItemCreate(ApiModel):
    owner_id: UUID
    card: CardIdentityInput
    serial_number: int | None = Field(default=None, gt=0)
    serial_total: int | None = Field(default=None, gt=0)
    is_numbered: bool = False
    grade_company: str | None = Field(default=None, max_length=40)
    grade_value: Decimal | None = Field(default=None, ge=0, le=10)
    grade_cert_number: str | None = Field(default=None, max_length=80)
    strategy_tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    purchase: PurchaseInput | None = None

    @model_validator(mode="after")
    def validate_serial(self) -> "CollectionItemCreate":
        if self.serial_number is not None or self.serial_total is not None:
            self.is_numbered = True
        if (
            self.serial_number is not None
            and self.serial_total is not None
            and self.serial_number > self.serial_total
        ):
            raise ValueError("serial_number cannot exceed serial_total")
        return self


class PortfolioItemQuickUpdate(ApiModel):
    holding_status: Literal["held", "sold"] = "held"
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    purchase_currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_rookie: bool | None = None
    is_numbered: bool | None = None
    is_autograph: bool | None = None
    is_memorabilia: bool | None = None
    serial_number: int | None = Field(default=None, gt=0)
    serial_total: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_serial(self) -> "PortfolioItemQuickUpdate":
        if (
            self.serial_number is not None
            and self.serial_total is not None
            and self.serial_number > self.serial_total
        ):
            raise ValueError("serial_number cannot exceed serial_total")
        return self


class TransactionCreate(ApiModel):
    transaction_type: TransactionType
    transacted_at: datetime
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=12, decimal_places=4)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    fee_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    fx_rate_to_base: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=8)
    venue: str | None = Field(default=None, max_length=120)
    external_order_id: str | None = Field(default=None, max_length=160)
    notes: str | None = None


class ValuationCreate(ApiModel):
    valued_at: datetime | None = None
    method: ValuationMethod = ValuationMethod.MANUAL
    currency: str = Field(min_length=3, max_length=3)
    low_value: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    fair_value: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    high_value: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    evidence: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_range(self) -> "ValuationCreate":
        if not self.low_value <= self.fair_value <= self.high_value:
            raise ValueError("valuation must satisfy low_value <= fair_value <= high_value")
        return self


class CreatedResource(ApiModel):
    id: UUID


class CardHobbyOrderCapture(ApiModel):
    external_order_id: str | None = Field(default=None, max_length=160)
    cardhobby_item_id: str = Field(min_length=1, max_length=40)
    item_url: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    image_urls: list[str] = Field(default_factory=list, max_length=10)
    purchased_at: datetime | None = None
    sale_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    shipping_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    raw_text: str | None = Field(default=None, max_length=10000)


class CardHobbyImportPreviewRequest(ApiModel):
    owner_id: UUID
    source_url: str = Field(min_length=1, max_length=500)
    orders: list[CardHobbyOrderCapture] = Field(min_length=1, max_length=100)


class CardHobbyImportDraft(ApiModel):
    id: UUID
    duplicate: bool = False
    status: str
    capture: CardHobbyOrderCapture
    proposed_item: CollectionItemCreate | None = None
    warnings: list[str] = Field(default_factory=list)


class CardHobbyImportPreviewResult(ApiModel):
    batch_id: UUID
    drafts: list[CardHobbyImportDraft]


class CardHobbyImportConfirmation(ApiModel):
    draft_id: UUID
    item: CollectionItemCreate


class CardHobbyImportConfirmRequest(ApiModel):
    owner_id: UUID
    items: list[CardHobbyImportConfirmation] = Field(min_length=1, max_length=100)


class CardHobbyImportConfirmResult(ApiModel):
    imported_item_ids: list[UUID] = Field(default_factory=list)
    skipped_draft_ids: list[UUID] = Field(default_factory=list)


class UserCreate(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(default="CNY", min_length=3, max_length=3)


class AuthRegister(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(default="CNY", min_length=3, max_length=3)


class AuthLogin(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class AuthPasswordChange(ApiModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class AuthUser(ApiModel):
    id: UUID
    email: str
    display_name: str
    base_currency: str
    role: str


class AuthSessionView(ApiModel):
    id: UUID
    current: bool
    user_agent: str | None
    ip_address: str | None
    last_seen_at: datetime
    expires_at: datetime
    created_at: datetime


class PortfolioItem(ApiModel):
    holding_status: Literal["held", "sold"] = "held"
    news_alerts: list[NewsAlertView] = Field(default_factory=list)
    is_redemption: bool = False
    redemption_status: Literal["unknown", "unredeemed", "redeemed", "expired"] = "unknown"
    id: UUID
    display_id: int
    owner_id: UUID
    player_id: UUID
    player_name: str
    brand_name: str
    season_start: int
    season_end: int | None
    product_name: str
    set_name: str | None
    card_number: str | None
    insert_name: str | None
    parallel_name: str | None
    is_rookie: bool
    is_autograph: bool
    autograph_type: str
    has_inscription: bool
    inscription_text: str | None
    is_memorabilia: bool
    memorabilia_types: list[str]
    memorabilia_usage: str
    memorabilia_player: str | None
    is_patch: bool
    is_numbered: bool
    serial_number: int | None
    serial_total: int | None
    grade_company: str | None
    grade_value: Decimal | None
    image_front_url: str | None
    image_back_url: str | None
    strategy_tags: list[str]
    quantity_held: Decimal
    remaining_cost: Decimal
    purchase_price: Decimal | None
    purchase_total_cost: Decimal | None = None
    purchase_currency: str | None
    realized_profit: Decimal
    valuation_currency: str | None
    valuation_low: Decimal | None
    fair_value: Decimal | None
    valuation_high: Decimal | None
    codex_analysis_version: int | None = None
    valuation_synthesis: dict = Field(default_factory=dict)
    codex_valuation_currency: str | None = None
    codex_valuation_low: Decimal | None = None
    codex_fair_value: Decimal | None = None
    codex_valuation_high: Decimal | None = None
    shadow_valuation_currency: str | None = None
    shadow_valuation_low: Decimal | None = None
    shadow_fair_value: Decimal | None = None
    shadow_valuation_high: Decimal | None = None
    shadow_valuation_confidence: Decimal | None = None
    shadow_valuation_status: str | None = None
    shadow_valuation_model: str | None = None
    market_comp_count: int
    unrealized_profit: Decimal | None
    valuation_status: str | None = None
    valuation_result_summary: dict = Field(default_factory=dict)
    valuation_job_id: UUID | None = None
    valuation_error: str | None = None
    recognition_confidence: Decimal | None = None
    recognition_status: Literal["resolved", "research_required"] | None = None
    recognition_quality_issues: list[str] = Field(default_factory=list)
    deleted_at: datetime | None = None
    deleted_reason: str | None = None


class PortfolioPage(ApiModel):
    items: list[PortfolioItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=1)


class CollectionItemDelete(ApiModel):
    owner_id: UUID
    reason: str | None = Field(default=None, max_length=500)


class CollectionItemOwnerAction(ApiModel):
    owner_id: UUID


class PortfolioSummary(ApiModel):
    owner_id: UUID
    base_currency: str
    item_count: int
    valued_item_count: int
    unvalued_item_count: int
    total_cost: Decimal
    total_market_value: Decimal
    unrealized_profit: Decimal
    realized_profit: Decimal


class FactorScores(ApiModel):
    historical_standing: Decimal = Field(ge=0, le=100)
    current_performance: Decimal = Field(ge=0, le=100)
    age_potential: Decimal = Field(ge=0, le=100)
    card_scarcity: Decimal = Field(ge=0, le=100)
    brand_tier: Decimal = Field(ge=0, le=100)
    market_heat: Decimal = Field(ge=0, le=100)


class CardAnalysisCreate(ApiModel):
    owner_id: UUID
    asking_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    fair_value_low: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    fair_value_high: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    evidence_count: int = Field(default=0, ge=0)
    factors: FactorScores
    factor_inputs: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fair_value(self) -> "CardAnalysisCreate":
        if self.fair_value_high < self.fair_value_low:
            raise ValueError("fair_value_high must be greater than or equal to fair_value_low")
        return self


class AutoCardAnalysisCreate(ApiModel):
    owner_id: UUID
    asking_price: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)


class CardAnalysisResult(ApiModel):
    analysis_id: UUID
    score_id: UUID
    total_score: Decimal
    factor_scores: FactorScores
    contributions: dict[str, Decimal]
    price_assessment: str
    rating: str
    currency: str
    fair_value_low: Decimal
    fair_value_high: Decimal
    target_sell_low: Decimal
    target_sell_high: Decimal
    holding_period_min_months: int
    holding_period_max_months: int
    confidence: Decimal
    catalysts: list[str]
    risks: list[str]


class ExpertAnalysisRun(ApiModel):
    force: bool = False


class ExpertAnalysisSnapshot(ApiModel):
    id: UUID
    job_id: UUID
    collection_item_id: UUID
    analysis_version: int
    source: str
    model_name: str
    identity_version_id: UUID | None
    identity_summary: str
    identity_confidence: Decimal
    currency: str | None
    fair_value_low: Decimal | None
    fair_value: Decimal | None
    fair_value_high: Decimal | None
    valuation_confidence: Decimal | None
    evidence_summary: list[dict]
    value_drivers: list[str]
    risks_and_conflicts: list[str]
    investment_conclusion: str | None
    recommended_actions: list[str]
    raw_result: dict
    status: str
    error_message: str | None
    created_at: datetime

    @field_validator("evidence_summary", mode="before")
    @classmethod
    def normalize_legacy_evidence(cls, value):
        # Historical snapshots are immutable; adapt old text evidence only on read.
        if isinstance(value, list):
            return [
                {"type": "legacy_text", "detail": entry} if isinstance(entry, str) else entry
                for entry in value
            ]
        return value


class ExpertAnalysisJobView(ApiModel):
    id: UUID
    collection_item_id: UUID
    status: str
    attempt_count: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExpertAnalysisHistory(ApiModel):
    latest: ExpertAnalysisSnapshot | None
    history: list[ExpertAnalysisSnapshot]
    current_job: ExpertAnalysisJobView | None


class ExpertAnalysisBatchResult(ApiModel):
    total: int
    succeeded: int
    failed: int
    item_ids: list[UUID]


class PlayerMetricUpsert(ApiModel):
    season: str = Field(min_length=4, max_length=16)
    scope: str = Field(default="season", min_length=1, max_length=30)
    scope_date: date | None = None
    metrics: dict[str, float | int | str | None]


class PlayerEventCreate(ApiModel):
    event_type: str = Field(pattern="^(news|injury|trade|team_context|award|milestone)$")
    occurred_at: datetime
    headline: str = Field(min_length=1, max_length=300)
    summary: str | None = None
    sentiment: Decimal | None = Field(default=None, ge=-1, le=1)
    impact_score: Decimal | None = Field(default=None, ge=0, le=100)
    source_url: str | None = None


class PotentialInput(ApiModel):
    age: int = Field(ge=18, le=60)
    performance_score: Decimal = Field(ge=0, le=100)
    performance_trend: Decimal = Field(ge=-100, le=100)
    availability_score: Decimal = Field(ge=0, le=100)
    role_score: Decimal = Field(ge=0, le=100)
    team_environment_score: Decimal = Field(ge=0, le=100)


class PlayerPotential(ApiModel):
    score: Decimal
    label: str
    components: dict[str, Decimal]


class PlayerMetricView(ApiModel):
    season: str
    scope: str
    scope_date: date | None
    metrics: dict
    captured_at: datetime


class PlayerEventView(ApiModel):
    id: UUID
    event_type: str
    occurred_at: datetime
    headline: str
    summary: str | None
    sentiment: Decimal | None
    impact_score: Decimal | None
    source_url: str | None


class PlayerIntelligence(ApiModel):
    player_id: UUID
    player_name: str
    metrics: list[PlayerMetricView]
    events: list[PlayerEventView]
    potential: PlayerPotential | None


class TransactionView(ApiModel):
    id: UUID
    transaction_type: TransactionType
    transacted_at: datetime
    quantity: Decimal
    unit_price: Decimal
    fee_amount: Decimal
    currency: str
    fx_rate_to_base: Decimal
    venue: str | None
    notes: str | None


class ValuationView(ApiModel):
    id: UUID
    valued_at: datetime
    method: ValuationMethod
    currency: str
    low_value: Decimal
    fair_value: Decimal
    high_value: Decimal
    confidence: Decimal | None


class PortfolioItemDetail(ApiModel):
    item: PortfolioItem
    transactions: list[TransactionView]
    valuations: list[ValuationView]


class AINarrative(ApiModel):
    thesis: str
    price_reasoning: str
    catalysts: list[str]
    risks: list[str]
    disclaimer: str


class AINarrativeResult(ApiModel):
    analysis_id: UUID
    provider: str
    model: str
    narrative: AINarrative


class PlayerSyncRequest(ApiModel):
    nba_person_id: int | None = Field(default=None, gt=0)
    sources: list[str] = Field(default_factory=lambda: ["nba_stats", "news", "injury"])


class PlayerSyncResult(ApiModel):
    run_id: UUID
    status: str
    source_results: dict[str, dict]


class MarketCompCreate(ApiModel):
    owner_id: UUID
    occurred_at: datetime
    sale_price: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    shipping_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    fx_rate_to_base: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=8)
    grade_company: str | None = None
    grade_value: Decimal | None = Field(default=None, ge=0, le=10)
    listing_title: str | None = None
    source_url: str | None = None
    source_code: str = Field(
        default="market_import",
        pattern="^(market_import|cardhobby|ebay|130point|weikajia)$",
    )
    external_id: str | None = Field(default=None, max_length=300)
    origin_source_code: str | None = Field(
        default=None,
        pattern="^(cardhobby|ebay|130point|weikajia)$",
    )
    source_reliability: Decimal | None = Field(default=None, ge=0, le=1)
    match_confidence: Decimal = Field(default=Decimal("0.8"), ge=0, le=1)


class MarketCompView(ApiModel):
    id: UUID
    occurred_at: datetime
    sale_price: Decimal
    shipping_price: Decimal
    currency: str
    fx_rate_to_base: Decimal | None
    grade_company: str | None
    grade_value: Decimal | None
    listing_title: str | None
    source_url: str | None
    match_confidence: Decimal | None
    source_reliability: Decimal | None = None
    similarity: Decimal | None = None
    comparable_tier: str | None = None
    classification: dict = Field(default_factory=dict)


class MarketEstimateCreate(ApiModel):
    owner_id: UUID
    currency: str = Field(min_length=3, max_length=3)


class MarketEstimateResult(ApiModel):
    valuation_id: UUID
    currency: str
    low_value: Decimal
    fair_value: Decimal
    high_value: Decimal
    confidence: Decimal
    evidence_count: int


class AutomatedValuationSnapshot(ApiModel):
    id: UUID
    valued_at: datetime
    currency: str
    low_value: Decimal
    fair_value: Decimal
    high_value: Decimal
    confidence: Decimal | None
    evidence: dict
    model_version: str | None


class ShadowValuationSnapshot(ApiModel):
    id: UUID
    created_at: datetime
    status: str
    currency: str
    low_value: Decimal | None
    fair_value: Decimal | None
    high_value: Decimal | None
    confidence: Decimal
    evidence_count: int
    review_reasons: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    model_version: str


class AutomatedValuationDetail(ApiModel):
    job_id: UUID | None = None
    status: str | None = None
    error_message: str | None = None
    result_summary: dict = Field(default_factory=dict)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    valuation: AutomatedValuationSnapshot | None = None
    shadow_valuation: ShadowValuationSnapshot | None = None
    comparables: list[MarketCompView] = Field(default_factory=list)


class ImageUploadResult(ApiModel):
    item_id: UUID
    side: str
    image_url: str


class CardCandidateIdentity(ApiModel):
    player_name: str | None = None
    season_start: int | None = Field(default=None, ge=1940, le=2200)
    season_end: int | None = Field(default=None, ge=1940, le=2200)
    brand_name: str | None = None
    product_name: str | None = None
    insert_name: str | None = None
    parallel_name: str | None = None
    card_number: str | None = None
    serial_number: int | None = Field(default=None, gt=0)
    serial_total: int | None = Field(default=None, gt=0)
    is_numbered: bool | None = None
    grade_company: str | None = None
    grade_value: Decimal | None = Field(default=None, ge=0, le=10)
    is_rookie: bool | None = None
    rookie_evidence: list[str] = Field(default_factory=list)
    is_autograph: bool | None = None
    autograph_type: Literal["on_card", "sticker", "cut", "unknown", "none"] | None = None
    has_inscription: bool | None = None
    inscription_text: str | None = None
    is_memorabilia: bool | None = None
    memorabilia_types: list[str] = Field(default_factory=list)
    memorabilia_usage: (
        Literal[
            "game_used",
            "player_worn",
            "event_worn",
            "not_from_specific_game_or_event",
            "manufactured",
            "unknown",
            "none",
        ]
        | None
    ) = None
    memorabilia_player: str | None = None
    is_patch: bool | None = None

    @field_validator("rookie_evidence", "memorabilia_types", mode="before")
    @classmethod
    def normalize_list_fields(cls, value):
        if value in (None, ""):
            return []
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def normalize_attributes(self):
        if self.serial_number is not None or self.serial_total is not None:
            self.is_numbered = True
        if self.inscription_text:
            self.has_inscription = True
        if self.has_inscription is True:
            self.is_autograph = True
        if self.is_autograph is False:
            self.autograph_type = "none"
            self.has_inscription = False
            self.inscription_text = None
        elif self.is_autograph is True and self.autograph_type in {None, "none"}:
            self.autograph_type = "unknown"
        if self.is_memorabilia is False:
            self.memorabilia_types = []
            self.memorabilia_usage = "none"
            self.memorabilia_player = None
            self.is_patch = False
        elif self.is_memorabilia is True and self.memorabilia_usage in {None, "none"}:
            self.memorabilia_usage = "unknown"
        if self.is_patch is True:
            self.is_memorabilia = True
            if "patch" not in self.memorabilia_types:
                self.memorabilia_types.append("patch")
        return self


class CardWebCandidate(ApiModel):
    title: str
    identity: CardCandidateIdentity = Field(default_factory=CardCandidateIdentity)
    source_urls: list[str] = Field(default_factory=list)
    image_url: str | None = None
    matching_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    match_confidence: Decimal = Field(ge=0, le=1)


class RecognitionFieldAssessment(ApiModel):
    value: str | int | bool | None = None
    status: Literal["unknown", "proposed", "verified", "conflicted"] = "unknown"
    source: Literal["none", "visual_model", "local_ocr", "catalog"] = "none"
    evidence: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class CardFaceObservation(ApiModel):
    role: Literal["front", "back", "ad", "unknown"] = "unknown"
    player_names: list[str] = Field(default_factory=list)
    brand_name: str | None = None
    product_name: str | None = None
    insert_name: str | None = None
    card_number: str | None = None
    multi_subject: bool = False
    evidence: list[str] = Field(default_factory=list)


class CardPairValidation(ApiModel):
    status: Literal["consistent", "conflict", "unknown"] = "unknown"
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    front: CardFaceObservation = Field(default_factory=CardFaceObservation)
    back: CardFaceObservation = Field(default_factory=CardFaceObservation)
    image_content_hashes: dict[str, str] = Field(default_factory=dict)
    version: int = 1


class CardRecognitionResult(ApiModel):
    pair_validation: CardPairValidation | None = None
    is_redemption: bool | None = None
    redemption_status: Literal["unknown", "unredeemed", "redeemed", "expired"] = "unknown"
    image_role: Literal["front", "back", "ad", "unknown"] = "unknown"
    player_name: str | None = None
    season_start: int | None = Field(default=None, ge=1940, le=2200)
    season_end: int | None = Field(default=None, ge=1940, le=2200)
    copyright_year: int | None = Field(default=None, ge=1940, le=2200)
    brand_name: str | None = None
    product_name: str | None = None
    insert_name: str | None = None
    parallel_name: str | None = None
    card_number: str | None = None
    serial_number: int | None = Field(default=None, gt=0)
    serial_total: int | None = Field(default=None, gt=0)
    is_numbered: bool | None = None
    grade_company: str | None = None
    grade_value: Decimal | None = Field(default=None, ge=0, le=10)
    is_rookie: bool | None = None
    rookie_evidence: list[str] = Field(default_factory=list)
    is_autograph: bool | None = None
    autograph_type: Literal["on_card", "sticker", "cut", "unknown", "none"] | None = None
    has_inscription: bool | None = None
    inscription_text: str | None = None
    is_memorabilia: bool | None = None
    memorabilia_types: list[str] = Field(default_factory=list)
    memorabilia_usage: (
        Literal[
            "game_used",
            "player_worn",
            "event_worn",
            "not_from_specific_game_or_event",
            "manufactured",
            "unknown",
            "none",
        ]
        | None
    ) = None
    memorabilia_player: str | None = None
    is_patch: bool | None = None
    confidence: Decimal = Field(ge=0, le=1)
    caveats: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    field_evidence: dict[str, list[str]] = Field(default_factory=dict)
    field_assessments: dict[str, RecognitionFieldAssessment] = Field(default_factory=dict)
    identity_storage_binding: dict[str, object] = Field(default_factory=dict)
    catalog_verified_fields: dict[str, str | int | bool] = Field(default_factory=dict)
    local_ocr_observed_fields: dict[str, str | int | bool] = Field(default_factory=dict)
    web_candidates: list[CardWebCandidate] = Field(default_factory=list)
    reference_image_front_url: str | None = None
    reference_image_back_url: str | None = None
    reference_image_source_url: str | None = None
    reference_image_provider: str | None = None
    reference_image_match_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    recognition_model: str | None = None
    recognition_route: list[dict[str, object]] = Field(default_factory=list)
    recognition_estimated_cost_cny: Decimal | None = Field(default=Decimal("0"), ge=0)
    recognition_status: Literal["resolved", "research_required"] = "resolved"
    recognition_quality_issues: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_shape(cls, value):
        """Repair common, unambiguous JSON shape drift from vision providers."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        pair = normalized.get("pair_validation")
        if pair is not None:
            try:
                normalized["pair_validation"] = CardPairValidation.model_validate(pair)
            except (ValueError, TypeError):
                normalized["pair_validation"] = CardPairValidation(
                    status="conflict" if isinstance(pair, dict) and pair.get("status") == "conflict" else "unknown",
                    reason_codes=["invalid_pair_validation"],
                )

        # Providers use null for a status without visible evidence. Keep that
        # uncertainty instead of rejecting the entire card recognition.
        if normalized.get("redemption_status") is None:
            normalized["redemption_status"] = "unknown"

        evidence = normalized.get("field_evidence")
        if isinstance(evidence, dict):
            normalized["field_evidence"] = {
                str(field): []
                if item in (None, "")
                else [item]
                if isinstance(item, str)
                else [str(item)]
                if not isinstance(item, list)
                else item
                for field, item in evidence.items()
            }

        # Models occasionally OCR an x/y serial fraction as x.y or x:y and
        # put the whole value into serial_number. Recover only two positive
        # integer components; leave all other malformed values unknown.
        serial = normalized.get("serial_number")
        if isinstance(serial, str):
            match = re.fullmatch(r"\s*0*(\d+)\s*[/.:]\s*0*(\d+)\s*", serial)
            if match:
                number, total = (int(part) for part in match.groups())
                if number > 0 and total > 0 and number <= total:
                    normalized["serial_number"] = number
                    if normalized.get("serial_total") in (None, ""):
                        normalized["serial_total"] = total
            elif not re.fullmatch(r"\s*\d+\s*", serial):
                normalized["serial_number"] = None

        return normalized

    @field_validator("rookie_evidence", mode="before")
    @classmethod
    def normalize_rookie_evidence(cls, value):
        if value in (None, ""):
            return []
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def normalize_card_attributes(self):
        from card_platform.domain.redemption import redemption_identity

        if self.is_redemption is None:
            visible = " ".join([
                *self.caveats, *self.field_evidence.get("is_redemption", []),
            ])
            if redemption_identity({"notes": visible})[0]:
                self.is_redemption = True
        if self.is_redemption:
            # These describe the promised card, not visible fabric on the voucher.
            description = self.insert_name or ""
            if re.search(r"\b(?:relic|jersey|patch|memorabilia)\b", description, re.I):
                self.is_memorabilia = True
            if re.search(r"\bautograph\b", description, re.I):
                self.is_autograph = True
        if self.serial_number is not None or self.serial_total is not None:
            self.is_numbered = True
        if self.inscription_text:
            self.has_inscription = True
        if self.has_inscription is True:
            self.is_autograph = True
        if self.is_autograph is False:
            self.autograph_type = "none"
            self.has_inscription = False
            self.inscription_text = None
        elif self.is_autograph is True and self.autograph_type in {None, "none"}:
            self.autograph_type = "unknown"
        if self.has_inscription is False:
            self.inscription_text = None
        if self.is_memorabilia is False:
            self.memorabilia_types = []
            self.memorabilia_usage = "none"
            self.memorabilia_player = None
            self.is_patch = False
        elif self.is_memorabilia is True and self.memorabilia_usage in {None, "none"}:
            self.memorabilia_usage = "unknown"
        if self.is_patch is True:
            self.is_memorabilia = True
            if "patch" not in self.memorabilia_types:
                self.memorabilia_types.append("patch")
        return self


class CardSearchEnrichment(ApiModel):
    resolved_fields: dict[str, object] = Field(default_factory=dict)
    field_evidence: dict[str, list[str]] = Field(default_factory=dict)
    candidates: list[CardWebCandidate] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class ScannerRecognitionJob(ApiModel):
    id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    front_image_url: str
    back_image_url: str
    front_crop_confidence: Decimal
    back_crop_confidence: Decimal
    front_transformed: bool
    back_transformed: bool
    front_rotation_degrees: Literal[0, 90, 180, 270] = 0
    back_rotation_degrees: Literal[0, 90, 180, 270] = 0
    front_orientation_confidence: Decimal = Decimal("0")
    back_orientation_confidence: Decimal = Decimal("0")
    front_orientation_method: Literal[
        "pending",
        "apple_vision",
        "apple_vision_text",
        "apple_vision_face_text",
        "apple_vision_face_override",
        "vision_model",
        "manual",
        "unresolved",
    ] = "pending"
    back_orientation_method: Literal[
        "pending",
        "apple_vision",
        "apple_vision_text",
        "apple_vision_face_text",
        "apple_vision_face_override",
        "vision_model",
        "manual",
        "unresolved",
    ] = "pending"
    result: CardRecognitionResult | None = None
    collection_item_id: UUID | None = None
    imported_at: datetime | None = None
    error_message: str | None = None
    attempt_count: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScannerCameraProperty(ApiModel):
    minimum: float
    maximum: float
    step: float
    default: float
    current: float
    writable: bool


class ScannerCameraControls(ApiModel):
    device_name: str
    exposure_mode: Literal["auto", "manual"]
    exposure_time: ScannerCameraProperty
    gain: ScannerCameraProperty | None = None


class ScannerCameraControlUpdate(ApiModel):
    exposure_mode: Literal["auto", "manual"] | None = None
    exposure_time: Annotated[float, Field(allow_inf_nan=False)] | None = None


class BatchCardRecognitionItem(ApiModel):
    filename: str
    result: CardRecognitionResult | None = None
    error: str | None = None


class SheetCardRecognitionItem(ApiModel):
    """One independently editable card discovered inside a composite image."""

    result: CardRecognitionResult
    card_image_bbox: (
        tuple[
            Annotated[int, Field(ge=0, le=1000)],
            Annotated[int, Field(ge=0, le=1000)],
            Annotated[int, Field(ge=0, le=1000)],
            Annotated[int, Field(ge=0, le=1000)],
        ]
        | None
    ) = None
    listing_title: str | None = None
    visible_description: str | None = None
    text_derived_fields: dict[str, str] = Field(default_factory=dict)
    order_number: str | None = None
    product_number: str | None = None
    purchased_at: datetime | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    shipping_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    quantity: int = Field(default=1, ge=1)
    seller_name: str | None = None
    source_region: str | None = None
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_card_image_bbox(self) -> "SheetCardRecognitionItem":
        if self.card_image_bbox is not None:
            left, top, right, bottom = self.card_image_bbox
            if right <= left or bottom <= top:
                raise ValueError("card_image_bbox must have positive width and height")
        return self


class SheetCardRecognitionResult(ApiModel):
    detected_count: int = Field(ge=0, le=50)
    cards: list[SheetCardRecognitionItem] = Field(default_factory=list, max_length=50)
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_detected_count(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("cards"), list):
            return value
        actual_count = len(value["cards"])
        if value.get("detected_count") == actual_count:
            return value
        normalized = dict(value)
        reported_count = normalized.get("detected_count")
        normalized["detected_count"] = actual_count
        normalized["caveats"] = [
            *(normalized.get("caveats") or []),
            f"模型报告数量 {reported_count} 与实际返回卡片数量 {actual_count} 不一致，已按实际结果修正。",
        ]
        return normalized


class CaptureImage(ApiModel):
    url: str
    revision: str
    source_url: str | None = None
    source_hash: str | None = None
    crop_recipe: dict | None = None
    thumb_url: str | None = None
    preview_url: str | None = None
    original_url: str | None = None


class CaptureJob(ScannerRecognitionJob):
    front_image_url: str | None = None
    back_image_url: str | None = None
    available_sides: list[Literal['front', 'back']]
    images: dict[str, CaptureImage | None]
    source: str
    require_confirmation: bool
    revision: int
    target_item_id: UUID | None = None
