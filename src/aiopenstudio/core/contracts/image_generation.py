"""Backend-neutral contracts for controlled local image generation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ModelId
from .runtime import ModelLifecycleRuntime


class ImagePerformance(StrEnum):
    SPEED = "speed"
    QUALITY = "quality"
    EXTREME_SPEED = "extreme_speed"


class ImageGenerationStage(StrEnum):
    QUEUED = "queued"
    WAITING_FOR_DEVICE = "waiting_for_device"
    STARTING_RUNTIME = "starting_runtime"
    LOADING = "loading"
    GENERATING = "generating"
    FINALIZING = "finalizing"
    RESTORING = "restoring"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ImageGenerationEventKind(StrEnum):
    QUEUED = "queued"
    STARTED = "started"
    PROGRESS = "progress"
    PREVIEW = "preview"
    IMAGE = "image"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ImageGenerationOptions(BaseModel):
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    image_count: int = Field(default=1, ge=1, le=8)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    performance: ImagePerformance = ImagePerformance.SPEED
    guidance_scale: float = Field(default=4.0, ge=1, le=30)
    sharpness: float = Field(default=2.0, ge=0, le=30)
    styles: tuple[str, ...] = ("Fooocus V2",)
    output_format: str = Field(default="png", pattern=r"^(png|jpeg|webp)$")

    @model_validator(mode="after")
    def validate_dimensions(self) -> ImageGenerationOptions:
        if self.width % 64 or self.height % 64:
            raise ValueError("image dimensions must be multiples of 64")
        return self


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    model: ModelId
    prompt: str = Field(min_length=1, max_length=10_000)
    negative_prompt: str = Field(default="", max_length=10_000)
    options: ImageGenerationOptions = Field(default_factory=ImageGenerationOptions)


class ImageProgress(BaseModel):
    stage: ImageGenerationStage
    fraction: float | None = Field(default=None, ge=0, le=1)
    detail: str | None = None
    queue_position: int | None = Field(default=None, ge=0)


class ImageArtifact(BaseModel):
    path: Path
    metadata_path: Path
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ImageGenerationResult(BaseModel):
    operation_id: str
    model: ModelId
    run_directory: Path
    elapsed_seconds: float = Field(ge=0)
    images: tuple[ImageArtifact, ...] = ()
    cancelled: bool = False
    warnings: tuple[str, ...] = ()


class ImageGenerationEvent(BaseModel):
    operation_id: str
    kind: ImageGenerationEventKind
    progress: ImageProgress | None = None
    preview_path: Path | None = None
    source_path: Path | None = None
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    result: ImageGenerationResult | None = None
    message: str | None = None


class ImageGenerationRuntime(ModelLifecycleRuntime, Protocol):
    """Runtime that streams previews and completed local image paths."""

    @property
    def process_id(self) -> int | None: ...

    def preflight(self) -> tuple[str, ...]: ...

    async def list_styles(self) -> Sequence[str]: ...

    def generate(self, request: ImageGenerationRequest) -> AsyncIterator[ImageGenerationEvent]: ...

    async def cancel(self, operation_id: str) -> None: ...
