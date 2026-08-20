"""Environment-backed application configuration."""

from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
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
    model_library_root: Path = Path("data/models")
    model_catalog_path: Path = Path("catalog/model-library.sqlite3")
    ollama_models_dir: Path = Path("ollama")
    huggingface_home: Path = Path("huggingface")
    whisper_models_dir: Path = Path("whisper")
    whisper_cancel_grace_seconds: float = Field(default=2.0, ge=0.5, le=30)
    whisper_max_input_bytes: int = Field(default=4 * 1024**3, ge=1024**2)
    fooocus_models_dir: Path = Path("fooocus")
    fooocus_home: Path = Path("data/runtime/fooocus/app")
    fooocus_python: Path = Path("data/runtime/fooocus/env/Scripts/python.exe")
    fooocus_host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    fooocus_port: int = Field(default=7865, ge=1024, le=65_535)
    fooocus_startup_timeout_seconds: float = Field(default=180.0, ge=10, le=900)
    fooocus_cancel_grace_seconds: float = Field(default=3.0, ge=0.5, le=30)
    fooocus_max_image_bytes: int = Field(default=256 * 1024**2, ge=1024**2)
    embedding_models_dir: Path = Path("embeddings")
    model_manifests_dir: Path = Path("manifests")
    model_cache_dir: Path = Path("cache")
    model_temp_dir: Path = Path("temp")
    model_checklist_path: Path = Path("download-checklist.md")
    sqlite_path: Path = Path("data/runtime/memory.sqlite3")
    sqlite_enable_vectors: bool = False
    sqlite_busy_timeout_ms: int = Field(default=5_000, gt=0, le=60_000)
    monitoring_enabled: bool = True
    monitoring_interval_seconds: float = Field(default=1.0, ge=0.5, le=60)
    monitoring_history_samples: int = Field(default=120, ge=10, le=3_600)
    monitoring_diagnostics_enabled: bool = False
    monitoring_auto_release_enabled: bool = False
    monitoring_idle_timeout_seconds: float = Field(default=600.0, ge=30)
    monitoring_max_managed_models: int = Field(default=1, ge=1, le=32)
    monitoring_ram_soft_limit: float = Field(default=0.85, gt=0, lt=1)
    monitoring_ram_hard_limit: float = Field(default=0.92, gt=0, le=1)
    monitoring_vram_soft_limit: float = Field(default=0.80, gt=0, lt=1)
    monitoring_vram_hard_limit: float = Field(default=0.90, gt=0, le=1)
    database_url: SecretStr | None = None

    @model_validator(mode="after")
    def validate_monitoring_limits(self) -> Self:
        if self.monitoring_ram_soft_limit >= self.monitoring_ram_hard_limit:
            raise ValueError("El límite blando de RAM debe ser menor que el límite duro.")
        if self.monitoring_vram_soft_limit >= self.monitoring_vram_hard_limit:
            raise ValueError("El límite blando de VRAM debe ser menor que el límite duro.")
        return self

    @staticmethod
    def resolve_path(path: Path, base_dir: Path | None = None) -> Path:
        """Resolve a configured path without creating it."""
        expanded = path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return ((base_dir or Path.cwd()) / expanded).resolve()

    def resolve_model_library_path(self, path: Path) -> Path:
        """Resolve a portable child path from the shared model library root."""
        expanded = path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return (self.model_library_root.expanduser() / expanded).resolve()
