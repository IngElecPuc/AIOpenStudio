"""Environment-backed application configuration."""

from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Validated local configuration with safe development defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AIOPENSTUDIO_",
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    ollama_base_url: AnyHttpUrl = "http://localhost:11434"  # type: ignore[assignment]
    data_dir: Path = Path("data")
    manifests_dir: Path = Path("models")
    weights_dir: Path = Path("data/models")
    cache_dir: Path = Path("data/cache")
    input_dir: Path = Path("data/inputs")
    output_dir: Path = Path("data/outputs")
    log_dir: Path = Path("data/logs")
    sqlite_path: Path = Path("data/runtime/memory.sqlite3")
    sqlite_enable_vectors: bool = False
    sqlite_busy_timeout_ms: int = Field(default=5_000, gt=0, le=60_000)
    database_url: SecretStr | None = None

    @staticmethod
    def resolve_path(path: Path, base_dir: Path | None = None) -> Path:
        """Resolve a configured path without creating it."""
        expanded = path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return ((base_dir or Path.cwd()) / expanded).resolve()
