import os

from pydantic import BaseModel, Field


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings(BaseModel):
    app_name: str = os.getenv("APP_NAME", "Intelligent Global Disease Observatory API")
    app_version: str = os.getenv("APP_VERSION", "1.1.0")
    cache_ttl_seconds: int = _env_int("CACHE_TTL_SECONDS", 600)
    outbreak_feed_url: str = os.getenv("OUTBREAK_FEED_URL", "https://www.who.int/rss-feeds/news-english.xml")
    api_key: str = os.getenv("API_KEY", "")
    rate_limit_window_seconds: int = _env_int("RATE_LIMIT_WINDOW_SECONDS", 60)
    rate_limit_max_requests: int = _env_int("RATE_LIMIT_MAX_REQUESTS", 120)
    allowed_origins: list[str] = Field(default_factory=lambda: _env_list("ALLOWED_ORIGINS"))
    ncbi_api_key: str = os.getenv("NCBI_API_KEY", "")
    redis_url: str = os.getenv("REDIS_URL", "")
    alert_poll_seconds: int = _env_int("ALERT_POLL_SECONDS", 900)
    promed_feed_url: str = os.getenv("PROMED_FEED_URL", "https://promedmail.org/promed-posts/feed/")
    healthmap_feed_url: str = os.getenv("HEALTHMAP_FEED_URL", "https://www.healthmap.org/en/?rss=1")
    who_gho_api_base: str = os.getenv("WHO_GHO_API_BASE", "https://ghoapi.azureedge.net/api")


settings = Settings()
