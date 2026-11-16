"""Application configuration loaded from environment variables.

All settings live here. Modules import `settings` and never read os.environ directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (backend/app/config.py -> repo root)
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_mode: Literal["cloud", "local"] = Field(default="cloud", alias="APP_MODE")
    local_data_dir: Path = Field(default=ROOT_DIR / ".local", alias="LOCAL_DATA_DIR")
    local_secret_key_file: Path | None = Field(default=None, alias="LOCAL_SECRET_KEY_FILE")

    @field_validator("local_data_dir")
    @classmethod
    def resolve_local_directory(cls, value: Path) -> Path:
        return (value if value.is_absolute() else ROOT_DIR / value).resolve()

    # --- LLM ---
    # Which provider to use. "groq" is recommended for the free tier
    # (much higher daily quota than Gemini free tier).
    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")

    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_model: str | None = Field(default=None, alias="LLM_MODEL")

    # Per-provider model names
    primary_model: str = Field(default="gemini-2.0-flash", alias="PRIMARY_MODEL")
    validator_model: str = Field(default="gemini-2.0-flash", alias="VALIDATOR_MODEL")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")

    # --- Data ---
    dataset_path: Path = Field(
        default=ROOT_DIR / "data" / "olist_v1_flat.csv",
        alias="DATASET_PATH",
    )
    dataset_table_name: str = Field(default="orders", alias="DATASET_TABLE_NAME")

    # --- Agent behavior ---
    max_agent_retries: int = Field(default=3, alias="MAX_AGENT_RETRIES")
    max_investigation_steps: int = Field(default=6, ge=1, le=20, alias="MAX_INVESTIGATION_STEPS")
    max_model_calls_per_task: int = Field(default=30, ge=1, le=100, alias="MAX_MODEL_CALLS_PER_TASK")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Session memory ---
    # memory (temporary), sqlite (local single-user), supabase (cloud).
    session_backend: str = Field(default="memory", alias="SESSION_BACKEND")
    session_ttl_seconds: int = Field(default=0, alias="SESSION_TTL_SECONDS")
    context_memory_tokens: int = Field(default=16000,ge=4000,le=64000,alias="CONTEXT_MEMORY_TOKENS")
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_key: str | None = Field(default=None, alias="SUPABASE_KEY")
    supabase_jwt_secret: str | None = Field(default=None, alias="SUPABASE_JWT_SECRET")

    # --- Safety limits ---
    sql_timeout_seconds: float = Field(default=10.0, alias="SQL_TIMEOUT_SECONDS")
    docker_wsl_distro: str = Field(default="", alias="DOCKER_WSL_DISTRO")
    docker_bind_root: str = Field(default="", alias="DOCKER_BIND_ROOT")
    rate_limit_ask: str = Field(default="10/minute", alias="RATE_LIMIT_ASK")
    rate_limit_default: str = Field(default="60/minute", alias="RATE_LIMIT_DEFAULT")
    cors_allowed_origins: str = Field(default="http://localhost:3000", alias="CORS_ALLOWED_ORIGINS")

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ALLOWED_ORIGINS into a list (comma-separated, or single '*')."""
        raw = self.cors_allowed_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()  # singleton; raises at import time if required env is missing
