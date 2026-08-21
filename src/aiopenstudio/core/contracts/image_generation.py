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


class ImageOperation(StrEnum):
    TEXT_TO_IMAGE = "text_to_image"
    VARY_SUBTLE = "vary_subtle"
    VARY_STRONG = "vary_strong"
    UPSCALE_1_5 = "upscale_1_5"
    UPSCALE_2 = "upscale_2"
    UPSCALE_FAST_2 = "upscale_fast_2"
    INPAINT = "inpaint"
    OUTPAINT = "outpaint"
    IMAGE_PROMPT = "image_prompt"
    DESCRIBE = "describe"
    ENHANCE = "enhance"


class ImagePromptKind(StrEnum):
    IMAGE_PROMPT = "image_prompt"
    PYRA_CANNY = "pyra_canny"
    CPDS = "cpds"
    FACE_SWAP = "face_swap"


class OutpaintDirection(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


class InpaintMode(StrEnum):
    DEFAULT = "default"
    DETAIL = "detail"
    MODIFY = "modify"


class DescribeContent(StrEnum):
    PHOTOGRAPH = "photograph"
    ART_ANIME = "art_anime"


class EnhanceOrder(StrEnum):
    BEFORE = "before"
    AFTER = "after"


class EnhancePromptSource(StrEnum):
    ORIGINAL = "original"
    LAST_FILLED = "last_filled"


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
    DESCRIPTION = "description"
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


class ImagePromptReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    kind: ImagePromptKind = ImagePromptKind.IMAGE_PROMPT
    stop_at: float = Field(default=0.5, ge=0, le=1)
    weight: float = Field(default=0.6, ge=0, le=2)
    enabled: bool = True


class EnhancementStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    detection_prompt: str = Field(default="", max_length=2_000)
    positive_prompt: str = Field(default="", max_length=10_000)
    negative_prompt: str = Field(default="", max_length=10_000)
    mask_model: str = Field(default="sam", min_length=1, max_length=100)
    cloth_category: str = Field(default="full", min_length=1, max_length=100)
    sam_model: str = Field(default="vit_b", min_length=1, max_length=100)
    text_threshold: float = Field(default=0.25, ge=0, le=1)
    box_threshold: float = Field(default=0.3, ge=0, le=1)
    max_detections: int = Field(default=0, ge=0, le=100)
    inpaint_mode: InpaintMode = InpaintMode.DEFAULT
    denoising_strength: float = Field(default=1.0, ge=0, le=1)
    respective_field: float = Field(default=0.618, ge=0, le=1)
    mask_erode_or_dilate: int = Field(default=0, ge=-64, le=64)
    invert_mask: bool = False


class EnhanceOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    uov_operation: ImageOperation | None = None
    order: EnhanceOrder = EnhanceOrder.BEFORE
    prompt_source: EnhancePromptSource = EnhancePromptSource.ORIGINAL
    steps: tuple[EnhancementStep, ...] = (EnhancementStep(),)
    save_only_final: bool = False

    @model_validator(mode="after")
    def validate_uov_operation(self) -> EnhanceOptions:
        supported = {
            ImageOperation.VARY_SUBTLE,
            ImageOperation.VARY_STRONG,
            ImageOperation.UPSCALE_1_5,
            ImageOperation.UPSCALE_2,
            ImageOperation.UPSCALE_FAST_2,
        }
        if self.uov_operation is not None and self.uov_operation not in supported:
            raise ValueError("enhance uov_operation must be a variation or upscale operation")
        if len(self.steps) > 8:
            raise ValueError("enhance supports at most 8 configured steps")
        return self


class ImageGenerationCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    operations: frozenset[ImageOperation] = frozenset({ImageOperation.TEXT_TO_IMAGE})
    prompt_kinds: frozenset[ImagePromptKind] = frozenset()
    max_reference_images: int = Field(default=0, ge=0, le=32)
    max_enhancement_steps: int = Field(default=0, ge=0, le=16)
    input_extensions: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".bmp"})
    schema_source: str = Field(default="unavailable", pattern=r"^(live|cached|unavailable)$")


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    model: ModelId
    prompt: str = Field(default="", max_length=10_000)
    negative_prompt: str = Field(default="", max_length=10_000)
    options: ImageGenerationOptions = Field(default_factory=ImageGenerationOptions)
    operation: ImageOperation = ImageOperation.TEXT_TO_IMAGE
    source_image: Path | None = None
    mask_image: Path | None = None
    outpaint_directions: tuple[OutpaintDirection, ...] = ()
    inpaint_mode: InpaintMode = InpaintMode.DEFAULT
    inpaint_prompt: str = Field(default="", max_length=10_000)
    references: tuple[ImagePromptReference, ...] = ()
    mix_references: bool = False
    describe_content: tuple[DescribeContent, ...] = (DescribeContent.PHOTOGRAPH,)
    describe_apply_styles: bool = True
    enhance: EnhanceOptions | None = None

    @model_validator(mode="after")
    def validate_operation_inputs(self) -> ImageGenerationRequest:
        source_operations = {
            ImageOperation.VARY_SUBTLE,
            ImageOperation.VARY_STRONG,
            ImageOperation.UPSCALE_1_5,
            ImageOperation.UPSCALE_2,
            ImageOperation.UPSCALE_FAST_2,
            ImageOperation.INPAINT,
            ImageOperation.OUTPAINT,
            ImageOperation.DESCRIBE,
            ImageOperation.ENHANCE,
        }
        if self.operation is ImageOperation.TEXT_TO_IMAGE and not self.prompt.strip():
            raise ValueError("text-to-image requires a prompt")
        if self.operation in source_operations and self.source_image is None:
            raise ValueError("the selected image operation requires a source image")
        if self.operation is ImageOperation.INPAINT and self.mask_image is None:
            raise ValueError("inpaint requires a mask image")
        if self.operation is ImageOperation.OUTPAINT and not self.outpaint_directions:
            raise ValueError("outpaint requires at least one direction")
        enabled_references = tuple(reference for reference in self.references if reference.enabled)
        if self.operation is ImageOperation.IMAGE_PROMPT and not enabled_references:
            raise ValueError("image prompt requires at least one enabled reference")
        if (
            self.operation is not ImageOperation.IMAGE_PROMPT
            and enabled_references
            and not self.mix_references
        ):
            raise ValueError("enable reference mixing to combine references with this operation")
        if self.operation is ImageOperation.DESCRIBE and not self.describe_content:
            raise ValueError("describe requires at least one content type")
        if self.operation is ImageOperation.ENHANCE and self.enhance is None:
            raise ValueError("enhance requires enhancement options")
        return self


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
    description: str | None = None


class ImageGenerationRuntime(ModelLifecycleRuntime, Protocol):
    """Runtime that streams previews and completed local image paths."""

    @property
    def process_id(self) -> int | None: ...

    def preflight(self) -> tuple[str, ...]: ...

    def preflight_for(self, request: ImageGenerationRequest) -> tuple[str, ...]: ...

    async def list_styles(self) -> Sequence[str]: ...

    async def image_capabilities(self) -> ImageGenerationCapabilities: ...

    def generate(self, request: ImageGenerationRequest) -> AsyncIterator[ImageGenerationEvent]: ...

    async def cancel(self, operation_id: str) -> None: ...
