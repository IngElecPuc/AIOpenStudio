"""Portable contracts for the shared local model library."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ArtifactKind(StrEnum):
    LLM = "llm"
    SPEECH = "speech"
    IMAGE = "image"
    EMBEDDING = "embedding"


class DownloadProvider(StrEnum):
    OLLAMA = "ollama"
    HUGGINGFACE = "huggingface"
    HTTP_FILE = "http_file"


class ArtifactStatus(StrEnum):
    INSTALLED = "installed"
    MISSING = "missing"
    PARTIAL = "partial"
    FAILED = "failed"


class DownloadSpec(BaseModel):
    """One selectable artifact in the versioned download manifest."""

    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    display_name: str = Field(min_length=1)
    kind: ArtifactKind
    provider: DownloadProvider
    source: str = Field(min_length=1)
    local_path: PurePosixPath
    family: str = Field(min_length=1)
    variant: str | None = None
    quantization: str | None = None
    revision: str | None = None
    runtime_reference: str | None = None
    license_name: str = Field(min_length=1)
    license_url: str | None = None
    source_url: str
    expected_size_bytes: int | None = Field(default=None, ge=0)
    capabilities: tuple[str, ...] = ()
    notes: str | None = None

    @field_validator("local_path")
    @classmethod
    def validate_local_path(cls, value: PurePosixPath) -> PurePosixPath:
        if value.is_absolute() or ".." in value.parts or str(value) in {"", "."}:
            raise ValueError("local_path must be a non-empty relative path without '..'")
        return value

    @model_validator(mode="after")
    def validate_provider_fields(self) -> DownloadSpec:
        if self.provider is DownloadProvider.OLLAMA and not self.runtime_reference:
            raise ValueError("Ollama entries require runtime_reference")
        if self.provider is DownloadProvider.HTTP_FILE and self.local_path.suffix == "":
            raise ValueError("HTTP file entries require a file destination")
        return self


class DownloadManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(ge=1)
    artifacts: tuple[DownloadSpec, ...]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> DownloadManifest:
        ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(ids) != len(set(ids)):
            raise ValueError("artifact_id values must be unique")
        return self


class InstalledArtifact(BaseModel):
    """Portable row representation for an installed artifact."""

    model_config = ConfigDict(frozen=True)

    artifact_id: str
    display_name: str
    kind: ArtifactKind
    provider: DownloadProvider
    family: str
    variant: str | None = None
    quantization: str | None = None
    source: str
    source_url: str
    revision: str | None = None
    runtime_reference: str | None = None
    relative_path: PurePosixPath
    license_name: str
    license_url: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    capabilities: tuple[str, ...] = ()
    installed_at: datetime
    verified_at: datetime | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ModelLibrarySettings(BaseSettings):
    """Resolve a portable library from one absolute root and relative children."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AIOPENSTUDIO_",
        env_ignore_empty=True,
        extra="ignore",
    )

    model_library_root: Path = Path("data/models")
    model_catalog_path: Path = Path("catalog/model-library.sqlite3")
    ollama_models_dir: Path = Path("ollama")
    huggingface_home: Path = Path("huggingface")
    whisper_models_dir: Path = Path("whisper")
    fooocus_models_dir: Path = Path("fooocus")
    embedding_models_dir: Path = Path("embeddings")
    model_manifests_dir: Path = Path("manifests")
    model_cache_dir: Path = Path("cache")
    model_temp_dir: Path = Path("temp")
    model_checklist_path: Path = Path("download-checklist.md")

    @field_validator(
        "model_catalog_path",
        "ollama_models_dir",
        "huggingface_home",
        "whisper_models_dir",
        "fooocus_models_dir",
        "embedding_models_dir",
        "model_manifests_dir",
        "model_cache_dir",
        "model_temp_dir",
        "model_checklist_path",
    )
    @classmethod
    def validate_portable_child(cls, value: Path) -> Path:
        if value.is_absolute() or ".." in value.parts or str(value) in {"", "."}:
            raise ValueError("model library child paths must be relative and remain under the root")
        return value

    def resolve(self, configured_path: Path) -> Path:
        """Resolve a child path under the configured root without creating it."""
        expanded = configured_path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return (self.model_library_root.expanduser() / expanded).resolve()

    @property
    def catalog_file(self) -> Path:
        return self.resolve(self.model_catalog_path)

    @property
    def checklist_file(self) -> Path:
        return self.resolve(self.model_checklist_path)

    @property
    def managed_directories(self) -> tuple[Path, ...]:
        configured = tuple(
            self.resolve(path)
            for path in (
                self.ollama_models_dir,
                self.huggingface_home,
                self.whisper_models_dir,
                self.fooocus_models_dir,
                self.embedding_models_dir,
                self.model_manifests_dir,
                self.model_cache_dir,
                self.model_temp_dir,
                self.model_catalog_path.parent,
            )
        )
        fooocus_root = self.resolve(self.fooocus_models_dir)
        fooocus_children = tuple(
            fooocus_root / relative
            for relative in (
                "checkpoints",
                "loras",
                "embeddings",
                "vae",
                "vae_approx",
                "upscale_models",
                "inpaint",
                "controlnet",
                "clip_vision",
                "prompt_expansion/fooocus_expansion",
                "outputs",
            )
        )
        return configured + fooocus_children
