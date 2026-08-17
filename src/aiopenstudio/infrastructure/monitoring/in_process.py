"""Explicit registry for models hosted by this Python process."""

from __future__ import annotations

from threading import RLock

from aiopenstudio.core.contracts import (
    MemoryAllocation,
    ModelId,
    ProcessState,
    ProviderStatus,
    RuntimeHealth,
    RuntimeModelTelemetry,
    RuntimeTelemetry,
    TelemetryContribution,
)


class InProcessTelemetryRegistry:
    """Accept measurements from PyTorch/Hugging Face adapters without importing them."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._models: dict[str, RuntimeModelTelemetry] = {}
        self._allocations: dict[str, tuple[MemoryAllocation, ...]] = {}

    @property
    def name(self) -> str:
        return "in_process"

    def register(
        self,
        model: RuntimeModelTelemetry,
        allocations: tuple[MemoryAllocation, ...],
    ) -> None:
        with self._lock:
            self._models[model.model.key] = model
            self._allocations[model.model.key] = allocations

    def unregister(self, model: ModelId) -> None:
        with self._lock:
            self._models.pop(model.key, None)
            self._allocations.pop(model.key, None)

    async def collect(self) -> TelemetryContribution:
        with self._lock:
            models = tuple(self._models.values())
            allocations = tuple(
                allocation
                for model_allocations in self._allocations.values()
                for allocation in model_allocations
            )
        return TelemetryContribution(
            provider=self.name,
            status=ProviderStatus.AVAILABLE,
            allocations=allocations,
            runtimes=(
                RuntimeTelemetry(
                    name="pytorch",
                    health=RuntimeHealth.READY,
                    process_state=ProcessState.RUNNING if models else ProcessState.STOPPED,
                    models=models,
                    detail="Mediciones registradas explícitamente por adaptadores en proceso.",
                ),
            ),
        )

    async def close(self) -> None:
        return None
