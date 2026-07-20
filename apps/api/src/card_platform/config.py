from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://card_platform:card_platform@localhost:5432/card_platform"
    default_currency: str = "CNY"
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    allowed_origins: str = "http://localhost:3000"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
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
