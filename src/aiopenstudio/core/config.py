"""Environment-backed application configuration."""

from typing import Literal

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Validated local configuration with safe development defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AIOPENSTUDIO_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    ollama_base_url: AnyHttpUrl = "http://localhost:11434"  # type: ignore[assignment]
    database_url: str | None = None
