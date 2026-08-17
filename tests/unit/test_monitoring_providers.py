import asyncio

import pytest

from aiopenstudio.core.contracts import (
    MeasurementQuality,
    MemoryAllocation,
    MemoryCategory,
    MemoryLocation,
    ModelId,
    ProviderStatus,
    RuntimeModelTelemetry,
)
from aiopenstudio.infrastructure.monitoring import (
    InProcessTelemetryRegistry,
    NvidiaTelemetryProvider,
    OllamaTelemetryProvider,
    SystemTelemetryProvider,
)


class FakeOllamaClient:
    async def ps(self) -> dict[str, object]:
        return {
            "models": [
                {
                    "model": "test:latest",
                    "size": 1000,
                    "size_vram": 750,
                    "context_length": 4096,
                    "expires_at": "2026-08-17T20:00:00Z",
                }
            ]
        }

    async def close(self) -> None:
        return None


def test_ollama_provider_reports_visible_total_without_false_breakdown() -> None:
    async def scenario() -> None:
        provider = OllamaTelemetryProvider("http://unused", FakeOllamaClient())
        contribution = await provider.collect()

        assert contribution.status is ProviderStatus.AVAILABLE
        model = contribution.runtimes[0].models[0]
        assert (model.ram_bytes, model.vram_bytes) == (250, 750)
        assert {allocation.category for allocation in contribution.allocations} == {
            MemoryCategory.RUNTIME_OTHER
        }
        assert {allocation.quality for allocation in contribution.allocations} == {
            MeasurementQuality.RUNTIME_REPORTED,
            MeasurementQuality.DERIVED,
        }

    asyncio.run(scenario())


def test_in_process_registry_exposes_adapter_measurements() -> None:
    async def scenario() -> None:
        registry = InProcessTelemetryRegistry()
        model = ModelId(runtime="pytorch", name="fixture")
        allocation = MemoryAllocation(
            owner_id=model.key,
            label=model.name,
            location=MemoryLocation.VRAM,
            category=MemoryCategory.ACTIVATIONS,
            bytes=128,
            quality=MeasurementQuality.MEASURED,
            model=model,
        )
        registry.register(RuntimeModelTelemetry(model=model, vram_bytes=128), (allocation,))

        contribution = await registry.collect()
        assert contribution.runtimes[0].models[0].model == model
        assert contribution.allocations == (allocation,)
        registry.unregister(model)
        assert not (await registry.collect()).allocations

    asyncio.run(scenario())


def test_nvidia_provider_degrades_when_nvml_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = NvidiaTelemetryProvider()
    monkeypatch.setattr(
        provider,
        "_load_nvml",
        lambda: (_ for _ in ()).throw(ImportError("missing")),
    )

    contribution = asyncio.run(provider.collect())

    assert contribution.status is ProviderStatus.UNAVAILABLE


def test_system_provider_returns_cpu_and_ram() -> None:
    contribution = asyncio.run(SystemTelemetryProvider().collect())

    assert contribution.system is not None
    assert contribution.system.ram_total_bytes > 0
