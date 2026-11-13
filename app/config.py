from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All environment variables from Section 2 of the backend document."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite is the local default. Production sets these to Supabase URLs
    # (postgresql+psycopg://..., see .env.example). MySQL also works (app/db_engine.py).
    DATABASE_URL: str = "sqlite:///./data/mapoly_wifi.db"
    RADIUS_DATABASE_URL: str = "sqlite:///./data/radius.db"
    # Local default. Render sets its own value in the dashboard.
    REDIS_URL: str = "redis://localhost:6379/0"

    SECRET_KEY: str
    # Only Argon2 is implemented (app/security/passwords.py). Any other value fails at
    # startup, so the setting can never claim a scheme the code does not use.
    PASSWORD_HASH_SCHEME: Literal["argon2"] = "argon2"

    PAYSTACK_SECRET_KEY: str
    PAYSTACK_PUBLIC_KEY: str = ""

    FRONTEND_ORIGIN: str
    LOGIN_TOKEN_TTL_SECONDS: int = 60
    DEFAULT_MAX_DEVICES: int = 2

    # Open accounting sessions with no interim update for this long are closed.
    # MikroTik sends an interim update every 5 minutes, so 15 means three missed.
    STALE_SESSION_MINUTES: int = 15

    # True only on Render, behind its proxy (see app/utils/client_ip.py).
    TRUST_PROXY_HEADERS: bool = False

    # CoA (disconnect) to the MikroTik. Empty means CoA is skipped, not failed.
    # COA_NAS_HOST is the router's management IP (Section 11, item 10, still open).
    COA_NAS_HOST: str = ""
    RADIUS_COA_SECRET: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
