"""Read-only Ollama runtime telemetry based on its public process API."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from ollama import AsyncClient, ResponseError

from aiopenstudio.core.contracts import (
    MeasurementQuality,
    MemoryAllocation,
    MemoryCategory,
    MemoryLocation,
    ModelId,
    ProcessState,
    ProviderStatus,
    RuntimeHealth,
    RuntimeModelTelemetry,
    RuntimeSetting,
    RuntimeTelemetry,
    TelemetryContribution,
)


class OllamaProcessClient(Protocol):
    async def ps(self) -> Any: ...

    async def close(self) -> None: ...


_SETTINGS: tuple[tuple[str, str, bool], ...] = (
    ("OLLAMA_CONTEXT_LENGTH", "4096", True),
    ("OLLAMA_MAX_LOADED_MODELS", "automático (según memoria/GPU)", True),
    ("OLLAMA_NUM_PARALLEL", "1", True),
    ("OLLAMA_MAX_QUEUE", "512", True),
    ("OLLAMA_MODELS", "directorio predeterminado de Ollama", True),
    ("OLLAMA_HOST", "127.0.0.1:11434", True),
    ("OLLAMA_NO_CLOUD", "false", True),
)


class OllamaTelemetryProvider:
    def __init__(self, base_url: str, client: OllamaProcessClient | None = None) -> None:
        self._client = client or cast(OllamaProcessClient, AsyncClient(host=base_url))

    @property
    def name(self) -> str:
        return "ollama"

    async def collect(self) -> TelemetryContribution:
        try:
            response = await self._client.ps()
        except (ConnectionError, OSError) as error:
            return self._unavailable(str(error))
        except ResponseError as error:
            return TelemetryContribution(
                provider=self.name,
                status=ProviderStatus.DEGRADED,
                runtimes=(self._runtime(RuntimeHealth.DEGRADED, ProcessState.FAILED, str(error)),),
                warnings=(f"Ollama respondió con error: {error}",),
            )

        models: list[RuntimeModelTelemetry] = []
        allocations: list[MemoryAllocation] = []
        for candidate in _sequence_value(response, "models"):
            name = _string_value(candidate, "model") or _string_value(candidate, "name")
            if not name:
                continue
            model = ModelId(runtime="ollama", name=name)
            total = _int_value(candidate, "size") or 0
            vram = _int_value(candidate, "size_vram") or 0
            ram = max(total - vram, 0)
            models.append(
                RuntimeModelTelemetry(
                    model=model,
                    total_bytes=total,
                    ram_bytes=ram,
                    vram_bytes=vram,
                    context_length=_int_value(candidate, "context_length"),
                    expires_at=_datetime_value(candidate, "expires_at"),
                )
            )
            if ram:
                allocations.append(self._allocation(model, ram, MemoryLocation.RAM))
            if vram:
                allocations.append(self._allocation(model, vram, MemoryLocation.VRAM))

        return TelemetryContribution(
            provider=self.name,
            status=ProviderStatus.AVAILABLE,
            allocations=tuple(allocations),
            runtimes=(
                RuntimeTelemetry(
                    name="ollama",
                    health=RuntimeHealth.READY,
                    process_state=ProcessState.RUNNING,
                    models=tuple(models),
                    settings=self._settings(),
                    detail=(
                        "Residencia reportada por /api/ps; el desglose interno no es observable."
                    ),
                ),
            ),
        )

    async def close(self) -> None:
        await self._client.close()

    def _unavailable(self, detail: str) -> TelemetryContribution:
        return TelemetryContribution(
            provider=self.name,
            status=ProviderStatus.UNAVAILABLE,
            runtimes=(self._runtime(RuntimeHealth.UNAVAILABLE, ProcessState.STOPPED, detail),),
            warnings=("Ollama no está disponible para telemetría.",),
        )

    def _runtime(
        self,
        health: RuntimeHealth,
        process_state: ProcessState,
        detail: str,
    ) -> RuntimeTelemetry:
        return RuntimeTelemetry(
            name="ollama",
            health=health,
            process_state=process_state,
            settings=self._settings(),
            detail=detail,
        )

    @staticmethod
    def _settings() -> tuple[RuntimeSetting, ...]:
        return tuple(
            RuntimeSetting(
                name=name,
                value=os.environ.get(name, default),
                source="environment" if name in os.environ else "documented_default",
                restart_required=restart_required,
            )
            for name, default, restart_required in _SETTINGS
        )

    @staticmethod
    def _allocation(
        model: ModelId,
        size: int,
        location: MemoryLocation,
    ) -> MemoryAllocation:
        quality = (
            MeasurementQuality.RUNTIME_REPORTED
            if location is MemoryLocation.VRAM
            else MeasurementQuality.DERIVED
        )
        return MemoryAllocation(
            owner_id=model.key,
            label=model.name,
            location=location,
            category=MemoryCategory.RUNTIME_OTHER,
            bytes=size,
            quality=quality,
            runtime="ollama",
            model=model,
            detail="Total del runtime; incluye pesos, KV cache, activaciones y overhead.",
        )


def _value(source: object, key: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _sequence_value(source: object, key: str) -> Sequence[object]:
    value = _value(source, key)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return cast(Sequence[object], value)
    return ()


def _string_value(source: object, key: str) -> str | None:
    value = _value(source, key)
    return value if isinstance(value, str) else None


def _int_value(source: object, key: str) -> int | None:
    value = _value(source, key)
    return int(value) if isinstance(value, (int, float)) else None


def _datetime_value(source: object, key: str) -> datetime | None:
    value = _value(source, key)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
