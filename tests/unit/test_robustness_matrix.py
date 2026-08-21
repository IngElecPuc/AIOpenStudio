import asyncio

import pytest

from aiopenstudio.core.contracts import (
    GpuTelemetry,
    LoadPolicy,
    ModelId,
    SystemTelemetry,
    TelemetryContribution,
)
from aiopenstudio.core.errors import ResourceLimitError
from aiopenstudio.services import DeviceLeaseCoordinator, ResourceMonitorService


class _Monitor:
    async def snapshot(self) -> object:
        return object()


class _PressureProvider:
    name = "synthetic-pressure"

    def __init__(self, *, ram_ratio: float, vram_ratio: float) -> None:
        self._ram_ratio = ram_ratio
        self._vram_ratio = vram_ratio

    async def collect(self) -> TelemetryContribution:
        total = 1_000
        return TelemetryContribution(
            provider=self.name,
            system=SystemTelemetry(
                cpu_percent=10,
                ram_total_bytes=total,
                ram_used_bytes=int(total * self._ram_ratio),
                ram_available_bytes=int(total * (1 - self._ram_ratio)),
                process_count=1,
            ),
            gpus=(
                GpuTelemetry(
                    index=0,
                    name="Synthetic GPU",
                    vram_total_bytes=total,
                    vram_used_bytes=int(total * self._vram_ratio),
                    vram_free_bytes=int(total * (1 - self._vram_ratio)),
                ),
            ),
        )

    async def close(self) -> None:
        return None


def test_concurrent_device_leases_never_overlap() -> None:
    async def scenario() -> None:
        coordinator = DeviceLeaseCoordinator(_Monitor())  # type: ignore[arg-type]
        active = 0
        maximum = 0

        async def worker(index: int) -> None:
            nonlocal active, maximum
            async with coordinator.lease(ModelId(runtime="test", name=str(index))):
                active += 1
                maximum = max(maximum, active)
                await asyncio.sleep(0)
                active -= 1

        await asyncio.gather(*(worker(index) for index in range(12)))

        assert maximum == 1
        assert active == 0

    asyncio.run(scenario())


def test_cancelled_lease_waiter_does_not_poison_the_queue() -> None:
    async def scenario() -> None:
        coordinator = DeviceLeaseCoordinator(_Monitor())  # type: ignore[arg-type]
        release = asyncio.Event()
        entered = asyncio.Event()

        async def holder() -> None:
            async with coordinator.lease(ModelId(runtime="test", name="holder")):
                entered.set()
                await release.wait()

        holder_task = asyncio.create_task(holder())
        await entered.wait()
        waiter = asyncio.create_task(
            coordinator.lease(ModelId(runtime="test", name="cancelled")).__aenter__()
        )
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await holder_task

        async with coordinator.lease(ModelId(runtime="test", name="after-cancel")):
            pass

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("ram_ratio", "vram_ratio", "expected"),
    ((0.95, 0.10, "RAM"), (0.10, 0.95, "VRAM")),
)
def test_synthetic_memory_pressure_is_rejected_before_load(
    ram_ratio: float,
    vram_ratio: float,
    expected: str,
) -> None:
    async def scenario() -> None:
        provider = _PressureProvider(ram_ratio=ram_ratio, vram_ratio=vram_ratio)
        service = ResourceMonitorService(
            (provider,),
            {},
            ram_hard_limit=0.92,
            vram_hard_limit=0.90,
        )
        await service.snapshot()
        with pytest.raises(ResourceLimitError, match=expected):
            await service.before_load(
                ModelId(runtime="test", name="pressure"),
                LoadPolicy(),
                100,
            )
        await service.close()

    asyncio.run(scenario())
