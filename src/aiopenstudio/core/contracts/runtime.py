"""Runtime lifecycle contract implemented by Ollama, Fooocus and Whisper."""

from collections.abc import AsyncIterator, Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .models import (
    ComputeDevice,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    RuntimeHealth,
)


class UnloadTarget(StrEnum):
    DEVICE = "device"
    RAM = "ram"
    ALL = "all"


class RuntimeEventKind(StrEnum):
    STARTED = "started"
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    METRICS = "metrics"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class LoadPolicy(BaseModel):
    device: ComputeDevice = ComputeDevice.AUTO
    pin_in_ram: bool = False
    pin_on_device: bool = False
    idle_timeout_seconds: float | None = Field(default=600.0, gt=0)


class RuntimeCapabilities(BaseModel):
    manages_process: bool = False
    supports_device_selection: bool = False
    supports_partial_unload: bool = False
    supports_streaming: bool = False
    supports_cancellation: bool = False


class InferenceRequest(BaseModel):
    operation_id: str = Field(min_length=1)
    model: ModelId
    inputs: dict[str, Any] = Field(default_factory=dict)


class RuntimeEvent(BaseModel):
    operation_id: str
    kind: RuntimeEventKind
    payload: dict[str, Any] = Field(default_factory=dict)


class ModelRuntime(Protocol):
    """Capabilities common to an external or in-process model runner."""

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> RuntimeCapabilities: ...

    async def health(self) -> RuntimeHealth: ...

    async def process_state(self) -> ProcessState: ...

    async def start(self) -> ProcessState: ...

    async def stop(self) -> ProcessState: ...

    async def list_models(self) -> Sequence[ModelDescriptor]: ...

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState: ...

    async def unload(
        self,
        model: ModelId,
        target: UnloadTarget = UnloadTarget.ALL,
    ) -> ModelState: ...

    async def state(self, model: ModelId) -> ModelState: ...

    def run(self, request: InferenceRequest) -> AsyncIterator[RuntimeEvent]: ...

    async def cancel(self, operation_id: str) -> None: ...


RuntimeInputs = Mapping[str, Any]
