"""Backend-neutral values exchanged across architectural boundaries."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ComputeDevice(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    GPU = "gpu"


class RuntimeHealth(StrEnum):
    UNAVAILABLE = "unavailable"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


class ProcessState(StrEnum):
    UNKNOWN = "unknown"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class ResidencyState(StrEnum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    UNLOADING = "unloading"
    FAILED = "failed"


class ModelId(BaseModel):
    model_config = ConfigDict(frozen=True)

    runtime: str = Field(min_length=1)
    name: str = Field(min_length=1)
    variant: str | None = None

    @property
    def key(self) -> str:
        """Return a stable key suitable for local persistence."""
        return ":".join((self.runtime, self.name, self.variant or ""))


class ModelDescriptor(BaseModel):
    id: ModelId
    display_name: str = Field(min_length=1)
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    weights_path: Path | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = None
    installed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelState(BaseModel):
    """Observable lifecycle with independent process, RAM and GPU states."""

    model: ModelId
    runtime_health: RuntimeHealth = RuntimeHealth.UNAVAILABLE
    process_state: ProcessState = ProcessState.UNKNOWN
    ram_residency: ResidencyState = ResidencyState.UNLOADED
    gpu_residency: ResidencyState = ResidencyState.UNLOADED
    active_device: ComputeDevice | None = None
    ram_bytes: int | None = Field(default=None, ge=0)
    vram_bytes: int | None = Field(default=None, ge=0)
    pinned_in_ram: bool = False
    pinned_on_device: bool = False
    last_used_at: datetime | None = None
    detail: str | None = None

    @property
    def loaded_in_ram(self) -> bool:
        return self.ram_residency is ResidencyState.LOADED

    @property
    def loaded_in_gpu(self) -> bool:
        return self.gpu_residency is ResidencyState.LOADED


class ResourceSnapshot(BaseModel):
    captured_at: datetime
    cpu_percent: float = Field(ge=0, le=100)
    ram_used_bytes: int = Field(ge=0)
    ram_total_bytes: int = Field(ge=0)
    gpu_name: str | None = None
    gpu_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    vram_used_bytes: int | None = Field(default=None, ge=0)
    vram_total_bytes: int | None = Field(default=None, ge=0)
