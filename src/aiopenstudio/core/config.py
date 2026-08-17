"""Environment-backed application configuration."""

from pathlib import Path
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
    data_dir: Path = Path("data")
    models_dir: Path = Path("data/models")
    cache_dir: Path = Path("data/cache")
    input_dir: Path = Path("data/inputs")
    output_dir: Path = Path("data/outputs")
    log_dir: Path = Path("data/logs")
    database_url: str | None = None
