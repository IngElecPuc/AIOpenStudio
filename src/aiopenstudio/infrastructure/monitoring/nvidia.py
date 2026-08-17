"""Optional NVIDIA telemetry with graceful fallback when NVML is unavailable."""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any, cast

from aiopenstudio.core.contracts import (
    GpuTelemetry,
    MeasurementQuality,
    MemoryAllocation,
    MemoryCategory,
    MemoryLocation,
    ProcessTelemetry,
    ProviderStatus,
    TelemetryContribution,
)


class NvidiaTelemetryProvider:
    def __init__(self) -> None:
        self._nvml: Any | None = None
        self._initialized = False

    @property
    def name(self) -> str:
        return "nvidia_nvml"

    async def collect(self) -> TelemetryContribution:
        return await asyncio.to_thread(self._collect_sync)

    async def close(self) -> None:
        if self._initialized and self._nvml is not None:
            await asyncio.to_thread(self._nvml.nvmlShutdown)
            self._initialized = False

    def _collect_sync(self) -> TelemetryContribution:
        try:
            nvml = self._load_nvml()
            gpus: list[GpuTelemetry] = []
            processes: dict[int, ProcessTelemetry] = {}
            allocations: list[MemoryAllocation] = []
            for index in range(int(nvml.nvmlDeviceGetCount())):
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                utilization = nvml.nvmlDeviceGetUtilizationRates(handle)
                name = nvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode(errors="replace")
                temperature = self._optional_call(
                    nvml.nvmlDeviceGetTemperature,
                    handle,
                    nvml.NVML_TEMPERATURE_GPU,
                )
                power_mw = self._optional_call(nvml.nvmlDeviceGetPowerUsage, handle)
                gpus.append(
                    GpuTelemetry(
                        index=index,
                        name=str(name),
                        utilization_percent=float(utilization.gpu),
                        memory_utilization_percent=float(utilization.memory),
                        vram_total_bytes=int(memory.total),
                        vram_used_bytes=int(memory.used),
                        vram_free_bytes=int(memory.free),
                        temperature_celsius=_number(temperature),
                        power_watts=_milliwatts(power_mw),
                    )
                )
                for process in self._device_processes(nvml, handle):
                    pid = int(process.pid)
                    used = self._valid_used_memory(nvml, process.usedGpuMemory)
                    previous = processes.get(pid)
                    processes[pid] = ProcessTelemetry(
                        pid=pid,
                        name=previous.name if previous else f"PID {pid}",
                        vram_bytes=(previous.vram_bytes or 0) + used if previous else used,
                    )
                    allocations.append(
                        MemoryAllocation(
                            owner_id=f"gpu:{index}:pid:{pid}",
                            label=f"PID {pid}",
                            location=MemoryLocation.VRAM,
                            category=MemoryCategory.PROCESS,
                            bytes=used,
                            quality=MeasurementQuality.MEASURED,
                            process_id=pid,
                            detail=f"Memoria de proceso medida por NVML en GPU {index}.",
                        )
                    )
            return TelemetryContribution(
                provider=self.name,
                status=ProviderStatus.AVAILABLE,
                gpus=tuple(gpus),
                processes=tuple(processes.values()),
                allocations=tuple(allocations),
            )
        except (ImportError, OSError, RuntimeError) as error:
            return TelemetryContribution(
                provider=self.name,
                status=ProviderStatus.UNAVAILABLE,
                warnings=(f"NVML no disponible: {error}",),
            )
        except Exception as error:  # NVML exposes version-specific exception classes.
            return TelemetryContribution(
                provider=self.name,
                status=ProviderStatus.DEGRADED,
                warnings=(f"NVML no pudo completar la muestra: {error}",),
            )

    def _load_nvml(self) -> Any:
        if self._nvml is None:
            self._nvml = import_module("pynvml")
        if not self._initialized:
            self._nvml.nvmlInit()
            self._initialized = True
        return self._nvml

    @staticmethod
    def _device_processes(nvml: Any, handle: Any) -> tuple[Any, ...]:
        found: dict[int, Any] = {}
        for function_name in (
            "nvmlDeviceGetComputeRunningProcesses",
            "nvmlDeviceGetGraphicsRunningProcesses",
        ):
            function = getattr(nvml, function_name, None)
            if function is None:
                continue
            try:
                for process in function(handle):
                    found[int(process.pid)] = process
            except Exception:
                continue
        return tuple(found.values())

    @staticmethod
    def _valid_used_memory(nvml: Any, value: object) -> int:
        unavailable = getattr(nvml, "NVML_VALUE_NOT_AVAILABLE", None)
        if value is None or value == unavailable:
            return 0
        if isinstance(value, (int, float)):
            return max(int(value), 0)
        return 0

    @staticmethod
    def _optional_call(function: Any, *args: Any) -> object | None:
        try:
            return cast(object, function(*args))
        except Exception:
            return None


def _number(value: object | None) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _milliwatts(value: object | None) -> float | None:
    number = _number(value)
    return number / 1000 if number is not None else None
