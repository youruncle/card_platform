from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    database_url: str = "postgresql://card_platform:card_platform@localhost:5432/card_platform"
    default_currency: str = "CNY"
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    allowed_origins: str = "http://localhost:3000"
    ai_provider: str = "deepseek"
    ai_api_key: str | None = None
    ai_base_url: str = "https://api.deepseek.com"
    ai_model: str = "deepseek-v4-flash"
    ai_thinking_enabled: bool = False
    vision_api_key: str | None = None
    vision_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vision_model: str = "qwen3-vl-flash"
    vision_plus_model: str = "qwen3-vl-plus"
    vision_finish_confirmation_model: str = "qwen3-vl-plus"
    vision_max_model: str = "qwen3.8-max"
    vision_plus_threshold: float = 0.88
    vision_max_threshold: float = 0.74
    vision_max_enabled: bool = True
    vision_thinking_enabled: bool = False
    vision_timeout_seconds: float = 60.0
    card_search_enabled: bool = True
    card_search_model: str = "qwen-plus"
    card_search_timeout_seconds: float = 45.0
    recognition_vision_stage_timeout_seconds: float = 240.0
    recognition_enrichment_stage_timeout_seconds: float = 90.0
    recognition_sample_timeout_seconds: float = 650.0
    recognition_concurrency: int = 4
    valuation_search_model: str = "qwen-plus"
    valuation_search_timeout_seconds: float = 90.0
    valuation_visual_concurrency: int = Field(default=2, ge=1, le=8)
    valuation_visual_stage_timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    valuation_visual_request_timeout_seconds: float = Field(default=90.0, gt=0, le=180)
    player_price_worker_enabled: bool = True
    purchase_cost_worker_enabled: bool = True
    purchase_cost_archive_dir: str | None = None
    valuation_worker_enabled: bool = True
    valuation_worker_concurrency: int = 3
    recognition_worker_enabled: bool = True
    valuation_worker_poll_seconds: float = 2.0
    valuation_verify_sources: bool = True
    valuation_cardhobby_only: bool = True
    valuation_public_image_index_path: str | None = str(
        Path(__file__).resolve().parents[4] / '.runtime/public-image-index.sqlite'
    )
    valuation_source_verify_timeout_seconds: float = 8.0
    search_index_brave_api_key: str | None = None
    search_index_serpapi_key: str | None = None
    search_index_timeout_seconds: float = 12.0
    search_index_max_queries: int = 7
    search_index_concurrency: int = 2
    nba_stats_base_url: str = "https://stats.nba.com/stats"
    news_rss_url_template: str | None = (
        "https://news.google.com/rss/search?q={player}+NBA&hl=en-US&gl=US&ceid=US:en"
    )
    injury_rss_url_template: str | None = (
        "https://news.google.com/rss/search?q={player}+NBA+injury&hl=en-US&gl=US&ceid=US:en"
    )
    provider_timeout_seconds: float = 15.0
    cardhobby_concurrent_limit: int = 1
    cardhobby_min_interval_ms: int = 1500
    cardhobby_error_budget: int = 5
    cardhobby_account_key_file: str = str(
        Path(__file__).resolve().parents[4] / '.runtime/secrets/cardhobby-account.key'
    )
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_marketplace_id: str = "EBAY_US"
    ebay_token_url: str = "https://api.ebay.com/identity/v1/oauth2/token"
    ebay_insights_url: str = (
        "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
    )
    ebay_marketplace_deletion_endpoint: str | None = None
    ebay_marketplace_deletion_verification_token: str | None = None
    ebay_notification_public_key_url: str = (
        "https://api.ebay.com/commerce/notification/v1/public_key"
    )
    ebay_notification_signature_required: bool = True
    market_hot_ttl_seconds: int = 7200
    market_warm_ttl_seconds: int = 28800
    market_cold_ttl_seconds: int = 172800
    upload_dir: str = "uploads"
    max_image_bytes: int = 10 * 1024 * 1024
    auth_cookie_name: str = "card_platform_session"
    auth_session_days: int = 30
    auth_cookie_secure: bool = False
    auth_registration_enabled: bool = True
    auth_max_failed_logins: int = 5
    auth_lock_minutes: int = 15
    scanner_worker_enabled: bool = True
    scanner_worker_concurrency: int = 4
    scanner_worker_poll_seconds: float = 1.0
    scanner_job_max_attempts: int = 3
    scanner_job_dir: str = "data/scanner-jobs"
    scanner_orientation_script: str = "scripts/orient_card_images.swift"
    scanner_orientation_local_threshold: float = 0.72
    scanner_nssdk_library_path: str | None = None
    flatbed_scan_dir: str = "data/flatbed-scans"
    flatbed_bridge_script: str = "scripts/canon_lide400_bridge.swift"
    flatbed_ocr_script: str = "scripts/ocr_card_region.swift"
    flatbed_ml_enabled: bool = False
    flatbed_ml_python: str = ".runtime/flatbed-ml/bin/python"
    flatbed_ml_worker_script: str = "scripts/flatbed_sam2_worker.py"
    flatbed_ml_checkpoint: str = ".runtime/models/sam2.1_hiera_tiny.pt"
    flatbed_ml_timeout_seconds: float = 20.0

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def resolved_upload_dir(self) -> Path:
        configured = Path(self.upload_dir).expanduser()
        if configured.is_absolute():
            return configured.resolve()
        repository_relative = Path.cwd() / "apps" / "api" / configured
        if repository_relative.is_dir():
            return repository_relative.resolve()
        api_relative = Path(__file__).resolve().parents[2] / configured
        if api_relative.is_dir():
            return api_relative.resolve()
        return configured.resolve()

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
