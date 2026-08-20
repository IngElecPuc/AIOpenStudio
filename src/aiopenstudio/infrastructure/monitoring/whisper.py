"""Telemetry provider for the managed faster-whisper worker."""

from __future__ import annotations

import psutil  # type: ignore[import-untyped]

from aiopenstudio.core.contracts import (
    ProcessTelemetry,
    ProviderStatus,
    RuntimeModelTelemetry,
    RuntimeTelemetry,
    TelemetryContribution,
)
from aiopenstudio.infrastructure.runtimes.whisper import FasterWhisperRuntime


class WhisperTelemetryProvider:
    def __init__(self, runtime: FasterWhisperRuntime) -> None:
        self._runtime = runtime

    @property
    def name(self) -> str:
        return "whisper"

    async def collect(self) -> TelemetryContribution:
        health = await self._runtime.health()
        process_state = await self._runtime.process_state()
        pid = self._runtime.process_id
        models: list[RuntimeModelTelemetry] = []
        for descriptor in await self._runtime.list_models():
            state = await self._runtime.state(descriptor.id)
            if state.loaded_in_ram or state.loaded_in_gpu:
                models.append(
                    RuntimeModelTelemetry(
                        model=descriptor.id,
                        total_bytes=descriptor.size_bytes,
                        ram_bytes=state.ram_bytes,
                        vram_bytes=state.vram_bytes,
                        process_id=pid,
                    )
                )
        processes: tuple[ProcessTelemetry, ...] = ()
        if pid is not None:
            try:
                process = psutil.Process(pid)
                processes = (
                    ProcessTelemetry(
                        pid=pid,
                        name=process.name(),
                        runtime=self._runtime.name,
                        ram_bytes=process.memory_info().rss,
                        owned_by_app=True,
                    ),
                )
            except (psutil.Error, OSError):
                pass
        return TelemetryContribution(
            provider=self.name,
            status=ProviderStatus.AVAILABLE,
            processes=processes,
            runtimes=(
                RuntimeTelemetry(
                    name=self._runtime.name,
                    health=health,
                    process_state=process_state,
                    models=tuple(models),
                    detail="Worker local aislado; VRAM física medida por NVML.",
                ),
            ),
        )

    async def close(self) -> None:
        return None
