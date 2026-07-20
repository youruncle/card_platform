from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        return self


class PurchaseInput(ApiModel):
    transacted_at: datetime
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    fee_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    fx_rate_to_base: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=8)
    venue: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class CollectionItemCreate(ApiModel):
    owner_id: UUID
    card: CardIdentityInput
    serial_number: int | None = Field(default=None, gt=0)
    serial_total: int | None = Field(default=None, gt=0)
    grade_company: str | None = Field(default=None, max_length=40)
    grade_value: Decimal | None = Field(default=None, ge=0, le=10)
    grade_cert_number: str | None = Field(default=None, max_length=80)
    strategy_tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    purchase: PurchaseInput | None = None

    @model_validator(mode="after")
    def validate_serial(self) -> "CollectionItemCreate":
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


class UserCreate(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(default="CNY", min_length=3, max_length=3)


class PortfolioItem(ApiModel):
    id: UUID
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
    is_patch: bool
    serial_number: int | None
    serial_total: int | None
    grade_company: str | None
    grade_value: Decimal | None
    image_front_url: str | None
    image_back_url: str | None
    strategy_tags: list[str]
    quantity_held: Decimal
    remaining_cost: Decimal
    realized_profit: Decimal
    valuation_currency: str | None
    valuation_low: Decimal | None
    fair_value: Decimal | None
    valuation_high: Decimal | None
    market_comp_count: int
    unrealized_profit: Decimal | None


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


class ImageUploadResult(ApiModel):
    item_id: UUID
    side: str
    image_url: str


class CardRecognitionResult(ApiModel):
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
    is_rookie: bool = False
    is_autograph: bool = False
    is_patch: bool = False
    confidence: Decimal = Field(ge=0, le=1)
    caveats: list[str] = Field(default_factory=list)
