"""Low-overhead CPU, RAM and related-process telemetry using psutil."""

from __future__ import annotations

import asyncio
import os

import psutil  # type: ignore[import-untyped]

from aiopenstudio.core.contracts import (
    ProcessTelemetry,
    ProviderStatus,
    SystemTelemetry,
    TelemetryContribution,
)


class SystemTelemetryProvider:
    def __init__(self, process_name_fragments: tuple[str, ...] = ("ollama",)) -> None:
        self._fragments = tuple(value.casefold() for value in process_name_fragments)
        self._own_pid = os.getpid()

    @property
    def name(self) -> str:
        return "system"

    async def collect(self) -> TelemetryContribution:
        return await asyncio.to_thread(self._collect_sync)

    async def close(self) -> None:
        return None

    def _collect_sync(self) -> TelemetryContribution:
        memory = psutil.virtual_memory()
        processes: list[ProcessTelemetry] = []
        for process in psutil.process_iter(("pid", "name")):
            try:
                pid = int(process.info["pid"])
                name = str(process.info.get("name") or f"PID {pid}")
                owned = pid == self._own_pid
                if not owned and not any(part in name.casefold() for part in self._fragments):
                    continue
                processes.append(
                    ProcessTelemetry(
                        pid=pid,
                        name=name,
                        runtime="ollama" if "ollama" in name.casefold() else None,
                        cpu_percent=float(process.cpu_percent(interval=None)),
                        ram_bytes=int(process.memory_info().rss),
                        owned_by_app=owned,
                    )
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return TelemetryContribution(
            provider=self.name,
            status=ProviderStatus.AVAILABLE,
            system=SystemTelemetry(
                cpu_percent=float(psutil.cpu_percent(interval=None)),
                ram_total_bytes=int(memory.total),
                ram_used_bytes=int(memory.used),
                ram_available_bytes=int(memory.available),
                process_count=len(psutil.pids()),
            ),
            processes=tuple(processes),
        )
