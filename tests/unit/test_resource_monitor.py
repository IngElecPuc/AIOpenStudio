import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from aiopenstudio.core.contracts import (
    ComputeDevice,
    GpuTelemetry,
    InferenceRequest,
    InferenceTelemetry,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ProviderStatus,
    ResidencyState,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeHealth,
    RuntimeModelTelemetry,
    RuntimeTelemetry,
    SystemTelemetry,
    TelemetryContribution,
    UnloadTarget,
)
from aiopenstudio.core.errors import ResourceLimitError
from aiopenstudio.services import ResourceMonitorService


class FakeProvider:
    name = "fake"

    def __init__(self, contribution: TelemetryContribution) -> None:
        self.contribution = contribution
        self.calls = 0

    async def collect(self) -> TelemetryContribution:
        self.calls += 1
        return self.contribution

    async def close(self) -> None:
        return None


class FakeRuntime:
    name = "fake"
    capabilities = RuntimeCapabilities(supports_partial_unload=True)

    def __init__(self) -> None:
        self.unloads: list[tuple[ModelId, UnloadTarget]] = []

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth.READY

    async def process_state(self) -> ProcessState:
        return ProcessState.RUNNING

    async def start(self) -> ProcessState:
        return ProcessState.RUNNING

    async def stop(self) -> ProcessState:
        return ProcessState.STOPPED

    async def list_models(self) -> Sequence[ModelDescriptor]:
        return ()

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        return ModelState(model=model)

    async def unload(self, model: ModelId, target: UnloadTarget = UnloadTarget.ALL) -> ModelState:
        self.unloads.append((model, target))
        return ModelState(model=model, runtime_health=RuntimeHealth.READY)

    async def state(self, model: ModelId) -> ModelState:
        return ModelState(model=model)

    def run(self, request: InferenceRequest) -> AsyncIterator[RuntimeEvent]:
        raise NotImplementedError

    async def cancel(self, operation_id: str) -> None:
        return None


def _contribution(model: ModelId) -> TelemetryContribution:
    return TelemetryContribution(
        provider="fake",
        system=SystemTelemetry(
            cpu_percent=25,
            ram_total_bytes=1000,
            ram_used_bytes=500,
            ram_available_bytes=500,
            process_count=10,
        ),
        gpus=(
            GpuTelemetry(
                index=0,
                name="Test GPU",
                vram_total_bytes=1000,
                vram_used_bytes=400,
                vram_free_bytes=600,
            ),
        ),
        runtimes=(
            RuntimeTelemetry(
                name="fake",
                health=RuntimeHealth.READY,
                process_state=ProcessState.RUNNING,
                models=(RuntimeModelTelemetry(model=model, total_bytes=300, vram_bytes=300),),
            ),
        ),
    )


def test_monitor_tracks_queue_residency_metrics_and_release() -> None:
    async def scenario() -> None:
        model = ModelId(runtime="fake", name="model")
        provider = FakeProvider(_contribution(model))
        runtime = FakeRuntime()
        service = ResourceMonitorService((provider,), {"fake": runtime}, max_managed_models=2)
        policy = LoadPolicy(device=ComputeDevice.AUTO)

        await service.snapshot()
        await service.before_load(model, policy, 300)
        queued = await service.snapshot()
        assert queued.queued_models[0].estimated_weight_bytes == 300

        service.model_loaded(
            ModelState(
                model=model,
                ram_residency=ResidencyState.LOADED,
                gpu_residency=ResidencyState.LOADED,
            ),
            policy,
        )
        service.record_inference(
            InferenceTelemetry(
                operation_id="op-1",
                model=model,
                input_tokens=10,
                output_tokens=5,
                generation_duration_ns=1_000_000_000,
            )
        )
        current = await service.snapshot()

        assert current.runtimes[0].models[0].owned_by_app is True
        assert current.last_inference[0].output_tokens_per_second == 5
        assert not current.queued_models
        await service.release_model(model)
        assert runtime.unloads == [(model, UnloadTarget.ALL)]

    asyncio.run(scenario())


def test_monitor_disables_collection_and_enforces_limits() -> None:
    async def scenario() -> None:
        first = ModelId(runtime="fake", name="first")
        provider = FakeProvider(_contribution(first))
        service = ResourceMonitorService((provider,), {"fake": FakeRuntime()}, max_managed_models=1)
        policy = LoadPolicy()
        service.model_loaded(ModelState(model=first), policy)

        with pytest.raises(ResourceLimitError, match="permite 1"):
            await service.before_load(ModelId(runtime="fake", name="second"), policy)

        await service.set_enabled(False)
        disabled = await service.snapshot()
        assert disabled.enabled is False
        assert disabled.provider_status == {"fake": ProviderStatus.DISABLED}
        assert provider.calls == 0

    asyncio.run(scenario())
