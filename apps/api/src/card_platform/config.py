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
    nba_stats_base_url: str = "https://stats.nba.com/stats"
    news_rss_url_template: str | None = (
        "https://news.google.com/rss/search?q={player}+NBA&hl=en-US&gl=US&ceid=US:en"
    )
    injury_rss_url_template: str | None = (
        "https://news.google.com/rss/search?q={player}+NBA+injury&hl=en-US&gl=US&ceid=US:en"
    )
    provider_timeout_seconds: float = 15.0
    upload_dir: str = "uploads"
    max_image_bytes: int = 10 * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
