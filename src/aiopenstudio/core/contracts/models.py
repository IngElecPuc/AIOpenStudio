"""Backend-neutral values exchanged across architectural boundaries."""

from datetime import datetime
from enum import StrEnum

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


class ModelId(BaseModel):
    model_config = ConfigDict(frozen=True)

    runtime: str = Field(min_length=1)
    name: str = Field(min_length=1)
    variant: str | None = None


class ModelDescriptor(BaseModel):
    id: ModelId
    display_name: str
    capabilities: frozenset[str] = frozenset()
    size_bytes: int | None = Field(default=None, ge=0)
    installed: bool = False


class ModelState(BaseModel):
    """Observable residency; a model may occupy RAM without using a GPU."""

    model: ModelId
    runtime_health: RuntimeHealth
    loaded_in_ram: bool = False
    compute_device: ComputeDevice | None = None
    memory_bytes: int | None = Field(default=None, ge=0)
    last_used_at: datetime | None = None
    detail: str | None = None


class ResourceSnapshot(BaseModel):
    captured_at: datetime
    cpu_percent: float = Field(ge=0, le=100)
    ram_used_bytes: int = Field(ge=0)
    ram_total_bytes: int = Field(ge=0)
    gpu_name: str | None = None
    gpu_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    vram_used_bytes: int | None = Field(default=None, ge=0)
    vram_total_bytes: int | None = Field(default=None, ge=0)
