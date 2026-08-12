from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    vision_api_key: str | None = None
    vision_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vision_model: str = "qwen3-vl-flash"
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
    valuation_worker_enabled: bool = True
    recognition_worker_enabled: bool = True
    valuation_worker_poll_seconds: float = 2.0
    valuation_verify_sources: bool = True
    valuation_source_verify_timeout_seconds: float = 8.0
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
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_marketplace_id: str = "EBAY_US"
    ebay_token_url: str = "https://api.ebay.com/identity/v1/oauth2/token"
    ebay_insights_url: str = (
        "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
    )
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
    scanner_worker_poll_seconds: float = 1.0
    scanner_job_max_attempts: int = 3
    scanner_job_dir: str = "data/scanner-jobs"
    scanner_orientation_script: str = "scripts/orient_card_images.swift"
    scanner_orientation_local_threshold: float = 0.72
    scanner_nssdk_library_path: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
