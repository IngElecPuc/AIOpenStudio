"""Backend-neutral resource telemetry and residency policy contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from .models import ModelId, ModelState, ProcessState, RuntimeHealth
from .runtime import LoadPolicy


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProviderStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class MeasurementQuality(StrEnum):
    MEASURED = "measured"
    RUNTIME_REPORTED = "runtime_reported"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class MemoryLocation(StrEnum):
    RAM = "ram"
    VRAM = "vram"


class MemoryCategory(StrEnum):
    WEIGHTS = "weights"
    KV_CACHE = "kv_cache"
    ACTIVATIONS = "activations"
    FRAMEWORK_RESERVED = "framework_reserved"
    RUNTIME_OTHER = "runtime_other"
    PROCESS = "process"
    UNKNOWN = "unknown"


class SystemTelemetry(BaseModel):
    cpu_percent: float = Field(ge=0, le=100)
    ram_total_bytes: int = Field(ge=0)
    ram_used_bytes: int = Field(ge=0)
    ram_available_bytes: int = Field(ge=0)
    process_count: int = Field(ge=0)


class ProcessTelemetry(BaseModel):
    pid: int = Field(gt=0)
    name: str
    runtime: str | None = None
    cpu_percent: float | None = Field(default=None, ge=0)
    ram_bytes: int | None = Field(default=None, ge=0)
    vram_bytes: int | None = Field(default=None, ge=0)
    owned_by_app: bool = False


class GpuTelemetry(BaseModel):
    index: int = Field(ge=0)
    name: str
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    memory_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    vram_total_bytes: int = Field(ge=0)
    vram_used_bytes: int = Field(ge=0)
    vram_free_bytes: int = Field(ge=0)
    temperature_celsius: float | None = None
    power_watts: float | None = Field(default=None, ge=0)


class MemoryAllocation(BaseModel):
    owner_id: str
    label: str
    location: MemoryLocation
    category: MemoryCategory
    bytes: int = Field(ge=0)
    quality: MeasurementQuality
    runtime: str | None = None
    model: ModelId | None = None
    process_id: int | None = Field(default=None, gt=0)
    detail: str | None = None


class RuntimeSetting(BaseModel):
    name: str
    value: str
    source: str
    restart_required: bool = False


class RuntimeModelTelemetry(BaseModel):
    model: ModelId
    total_bytes: int | None = Field(default=None, ge=0)
    ram_bytes: int | None = Field(default=None, ge=0)
    vram_bytes: int | None = Field(default=None, ge=0)
    context_length: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    owned_by_app: bool = False
    pinned: bool = False
    last_used_at: datetime | None = None


class RuntimeTelemetry(BaseModel):
    name: str
    health: RuntimeHealth
    process_state: ProcessState
    models: tuple[RuntimeModelTelemetry, ...] = ()
    settings: tuple[RuntimeSetting, ...] = ()
    queue_depth: int | None = Field(default=None, ge=0)
    detail: str | None = None


class QueuedModelTelemetry(BaseModel):
    model: ModelId
    queued_at: datetime = Field(default_factory=utc_now)
    estimated_weight_bytes: int | None = Field(default=None, ge=0)
    requested_device: str


class InferenceTelemetry(BaseModel):
    operation_id: str
    model: ModelId
    captured_at: datetime = Field(default_factory=utc_now)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_duration_ns: int | None = Field(default=None, ge=0)
    load_duration_ns: int | None = Field(default=None, ge=0)
    prompt_duration_ns: int | None = Field(default=None, ge=0)
    generation_duration_ns: int | None = Field(default=None, ge=0)
    done_reason: str | None = None

    @property
    def output_tokens_per_second(self) -> float | None:
        if not self.output_tokens or not self.generation_duration_ns:
            return None
        return self.output_tokens / (self.generation_duration_ns / 1_000_000_000)


class TelemetryContribution(BaseModel):
    provider: str
    status: ProviderStatus = ProviderStatus.AVAILABLE
    system: SystemTelemetry | None = None
    gpus: tuple[GpuTelemetry, ...] = ()
    processes: tuple[ProcessTelemetry, ...] = ()
    allocations: tuple[MemoryAllocation, ...] = ()
    runtimes: tuple[RuntimeTelemetry, ...] = ()
    warnings: tuple[str, ...] = ()


class TelemetrySnapshot(BaseModel):
    captured_at: datetime = Field(default_factory=utc_now)
    enabled: bool = True
    provider_status: dict[str, ProviderStatus] = Field(default_factory=dict)
    system: SystemTelemetry | None = None
    gpus: tuple[GpuTelemetry, ...] = ()
    processes: tuple[ProcessTelemetry, ...] = ()
    allocations: tuple[MemoryAllocation, ...] = ()
    runtimes: tuple[RuntimeTelemetry, ...] = ()
    queued_models: tuple[QueuedModelTelemetry, ...] = ()
    last_inference: tuple[InferenceTelemetry, ...] = ()
    warnings: tuple[str, ...] = ()


class TelemetryProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def collect(self) -> TelemetryContribution: ...

    async def close(self) -> None: ...


class InferenceMetricsSink(Protocol):
    def record_inference(self, metrics: InferenceTelemetry) -> None: ...


class ResidencyPolicy(Protocol):
    async def before_load(
        self,
        model: ModelId,
        policy: LoadPolicy,
        estimated_weight_bytes: int | None = None,
    ) -> None: ...

    def model_loaded(self, state: ModelState, policy: LoadPolicy) -> None: ...

    def model_load_failed(self, model: ModelId) -> None: ...

    def model_used(self, model: ModelId) -> None: ...

    def model_unloaded(self, model: ModelId) -> None: ...


class ResourceMonitor(Protocol):
    async def snapshot(self) -> TelemetrySnapshot: ...

    def watch(self, interval_seconds: float = 1.0) -> AsyncIterator[TelemetrySnapshot]: ...

    async def set_enabled(self, enabled: bool) -> None: ...

    def history(self) -> Sequence[TelemetrySnapshot]: ...
