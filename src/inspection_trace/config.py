"""Application settings, read from environment variables or an optional .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. DATABASE_URL is required and has no default on purpose."""

    # Real environment variables win over .env. Other keys in .env (POSTGRES_*) are ignored.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str


@lru_cache
def get_settings() -> Settings:
    """Build the settings once, on first use, so importing this module reads nothing."""
    return Settings()
