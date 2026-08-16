"""Runtime lifecycle contract implemented by Ollama, Fooocus and Whisper."""

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol

from .models import ComputeDevice, ModelDescriptor, ModelId, ModelState, RuntimeHealth


class ModelRuntime(Protocol):
    """Capabilities common to an external or in-process model runner."""

    @property
    def name(self) -> str: ...

    async def health(self) -> RuntimeHealth: ...

    async def list_models(self) -> Sequence[ModelDescriptor]: ...

    async def load(self, model: ModelId, device: ComputeDevice) -> ModelState: ...

    async def unload(self, model: ModelId) -> ModelState: ...

    async def state(self, model: ModelId) -> ModelState: ...

    def run(
        self,
        model: ModelId,
        inputs: Mapping[str, Any],
    ) -> AsyncIterator[Mapping[str, Any]]: ...

    async def cancel(self, operation_id: str) -> None: ...
