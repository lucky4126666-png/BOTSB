import os
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> Tuple[str, dict]:
    connect_args = {}

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    sslmode = query.pop("sslmode", None)
    if sslmode == "require":
        connect_args["ssl"] = "require"

    new_query = urlencode(query)
    url = urlunparse(parsed._replace(query=new_query))

    return url, connect_args


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BOT_TOKEN: str
    BASE_URL: str
    DATABASE_URL: str

    REDIS_URL: Optional[str] = None

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_SYSTEM_PROMPT: str = "You are a helpful Telegram assistant."

    ADMIN_WEB_KEY: str
    SESSION_SECRET: str
    TELEGRAM_WEBHOOK_SECRET: str = ""

    PORT: int = 8000

    @property
    def base_url(self) -> str:
        return self.BASE_URL.rstrip("/")

    @property
    def webhook_url(self) -> str:
        return f"{self.base_url}/webhook"

    @property
    def db_url_and_args(self):
        return normalize_database_url(self.DATABASE_URL)


settings = Settings()
