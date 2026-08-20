"""Aggregation, history and safe residency policies for resource telemetry."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiopenstudio.core.contracts import (
    InferenceTelemetry,
    LoadPolicy,
    ModelId,
    ModelLifecycleRuntime,
    ModelState,
    ProcessTelemetry,
    ProviderStatus,
    QueuedModelTelemetry,
    RuntimeModelTelemetry,
    RuntimeTelemetry,
    SystemTelemetry,
    TelemetryContribution,
    TelemetryProvider,
    TelemetrySnapshot,
    UnloadTarget,
)
from aiopenstudio.core.errors import ResourceLimitError


@dataclass
class _ManagedModel:
    model: ModelId
    policy: LoadPolicy
    loaded_at: datetime
    last_used_at: datetime

    @property
    def pinned(self) -> bool:
        return self.policy.pin_in_ram or self.policy.pin_on_device


class ResourceMonitorService:
    """Merge provider samples without making the UI depend on hardware SDKs."""

    def __init__(
        self,
        providers: Sequence[TelemetryProvider],
        runtimes: Mapping[str, ModelLifecycleRuntime],
        *,
        enabled: bool = True,
        interval_seconds: float = 1.0,
        history_samples: int = 120,
        auto_release_enabled: bool = False,
        idle_timeout_seconds: float = 600.0,
        max_managed_models: int = 1,
        ram_soft_limit: float = 0.85,
        ram_hard_limit: float = 0.92,
        vram_soft_limit: float = 0.80,
        vram_hard_limit: float = 0.90,
    ) -> None:
        self._providers = tuple(providers)
        self._runtimes = dict(runtimes)
        self._enabled = enabled
        self._interval_seconds = interval_seconds
        self._history: deque[TelemetrySnapshot] = deque(maxlen=history_samples)
        self._auto_release_enabled = auto_release_enabled
        self._idle_timeout = timedelta(seconds=idle_timeout_seconds)
        self._max_managed_models = max_managed_models
        self._ram_soft_limit = ram_soft_limit
        self._ram_hard_limit = ram_hard_limit
        self._vram_soft_limit = vram_soft_limit
        self._vram_hard_limit = vram_hard_limit
        self._managed: dict[str, _ManagedModel] = {}
        self._suspended: dict[str, _ManagedModel] = {}
        self._queued: dict[str, QueuedModelTelemetry] = {}
        self._inference: dict[str, InferenceTelemetry] = {}
        self._active_snapshot: asyncio.Task[TelemetrySnapshot] | None = None
        self._closed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def auto_release_enabled(self) -> bool:
        return self._auto_release_enabled

    async def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def set_auto_release(self, enabled: bool) -> None:
        self._auto_release_enabled = enabled

    async def snapshot(self) -> TelemetrySnapshot:
        current = self._active_snapshot
        if current is not None and not current.done():
            return await asyncio.shield(current)
        task = asyncio.create_task(self._collect_snapshot())
        self._active_snapshot = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._active_snapshot is task:
                self._active_snapshot = None

    async def _collect_snapshot(self) -> TelemetrySnapshot:
        if not self._enabled:
            return TelemetrySnapshot(
                enabled=False,
                provider_status={
                    provider.name: ProviderStatus.DISABLED for provider in self._providers
                },
                queued_models=tuple(self._queued.values()),
                last_inference=self._recent_inference(),
            )
        results = await asyncio.gather(
            *(provider.collect() for provider in self._providers),
            return_exceptions=True,
        )
        contributions: list[TelemetryContribution] = []
        warnings: list[str] = []
        provider_status: dict[str, ProviderStatus] = {}
        for provider, result in zip(self._providers, results, strict=True):
            if isinstance(result, BaseException):
                provider_status[provider.name] = ProviderStatus.DEGRADED
                warnings.append(f"{provider.name}: {result}")
                continue
            contributions.append(result)
            provider_status[result.provider] = result.status
            warnings.extend(result.warnings)
        system = next((item.system for item in contributions if item.system is not None), None)
        processes = self._merge_processes(contributions)
        runtimes = tuple(
            self._annotate_runtime(runtime)
            for contribution in contributions
            for runtime in contribution.runtimes
        )
        self._append_pressure_warnings(system, contributions, warnings)
        snapshot = TelemetrySnapshot(
            provider_status=provider_status,
            system=system,
            gpus=tuple(gpu for item in contributions for gpu in item.gpus),
            processes=processes,
            allocations=tuple(
                allocation for item in contributions for allocation in item.allocations
            ),
            runtimes=runtimes,
            queued_models=tuple(self._queued.values()),
            last_inference=self._recent_inference(),
            warnings=tuple(dict.fromkeys(warnings)),
        )
        self._history.append(snapshot)
        return snapshot

    async def watch(
        self, interval_seconds: float | None = None
    ) -> AsyncIterator[TelemetrySnapshot]:
        interval = interval_seconds or self._interval_seconds
        while not self._closed:
            if self._enabled:
                yield await self.snapshot()
                if self._auto_release_enabled:
                    await self.release_inactive()
                await asyncio.sleep(interval)
            else:
                await asyncio.sleep(min(interval, 0.25))

    def history(self) -> Sequence[TelemetrySnapshot]:
        return tuple(self._history)

    def record_inference(self, metrics: InferenceTelemetry) -> None:
        self._inference[metrics.model.key] = metrics
        self.model_used(metrics.model)

    async def before_load(
        self,
        model: ModelId,
        policy: LoadPolicy,
        estimated_weight_bytes: int | None = None,
    ) -> None:
        occupied = set(self._managed) | set(self._queued)
        if model.key not in occupied and len(occupied) >= self._max_managed_models:
            raise ResourceLimitError(
                f"La política permite {self._max_managed_models} modelo(s) administrado(s) "
                "simultáneamente. Libera uno antes de continuar."
            )
        if self._history:
            latest = self._history[-1]
            if latest.system and latest.system.ram_total_bytes:
                ratio = latest.system.ram_used_bytes / latest.system.ram_total_bytes
                if ratio >= self._ram_hard_limit:
                    raise ResourceLimitError("La RAM alcanzó el límite duro configurado.")
            if latest.gpus and any(
                gpu.vram_total_bytes
                and gpu.vram_used_bytes / gpu.vram_total_bytes >= self._vram_hard_limit
                for gpu in latest.gpus
            ):
                raise ResourceLimitError("La VRAM alcanzó el límite duro configurado.")
        self._queued[model.key] = QueuedModelTelemetry(
            model=model,
            estimated_weight_bytes=estimated_weight_bytes,
            requested_device=policy.device.value,
        )

    def model_loaded(self, state: ModelState, policy: LoadPolicy) -> None:
        now = datetime.now(UTC)
        self._queued.pop(state.model.key, None)
        self._managed[state.model.key] = _ManagedModel(state.model, policy, now, now)
        self._suspended.pop(state.model.key, None)

    def model_load_failed(self, model: ModelId) -> None:
        self._queued.pop(model.key, None)

    def model_used(self, model: ModelId) -> None:
        managed = self._managed.get(model.key)
        if managed is not None:
            managed.last_used_at = datetime.now(UTC)

    def model_unloaded(self, model: ModelId) -> None:
        self._queued.pop(model.key, None)
        self._managed.pop(model.key, None)
        self._suspended.pop(model.key, None)

    def suspend_model(self, model: ModelId) -> bool:
        managed = self._managed.pop(model.key, None)
        if managed is None:
            return False
        self._suspended[model.key] = managed
        self._queued.pop(model.key, None)
        return True

    def resume_model(self, state: ModelState) -> None:
        suspended = self._suspended.pop(state.model.key, None)
        if suspended is None:
            return
        suspended.last_used_at = datetime.now(UTC)
        self._managed[state.model.key] = suspended

    async def requires_device_yield(
        self,
        requester: ModelId,
        estimated_vram_bytes: int | None,
    ) -> bool:
        await self.snapshot()
        other_managed = [key for key in self._managed if key != requester.key]
        if len(other_managed) >= self._max_managed_models:
            return True
        if not self._history or estimated_vram_bytes is None:
            return bool(other_managed)
        latest = self._history[-1]
        if not latest.gpus:
            return False
        gpu = latest.gpus[0]
        hard_available = int(gpu.vram_total_bytes * self._vram_hard_limit) - gpu.vram_used_bytes
        return estimated_vram_bytes > max(hard_available, 0)

    async def release_model(self, model: ModelId) -> ModelState:
        runtime = self._runtimes.get(model.runtime)
        if runtime is None:
            raise ValueError(f"No existe runtime registrado para {model.runtime!r}.")
        state = await runtime.unload(model, UnloadTarget.ALL)
        self.model_unloaded(model)
        return state

    async def release_inactive(self, *, force: bool = False) -> tuple[ModelState, ...]:
        now = datetime.now(UTC)
        candidates = tuple(
            managed
            for managed in self._managed.values()
            if not managed.pinned
            and (
                force
                or now - managed.last_used_at
                >= (
                    timedelta(seconds=managed.policy.idle_timeout_seconds)
                    if managed.policy.idle_timeout_seconds is not None
                    else self._idle_timeout
                )
            )
        )
        released: list[ModelState] = []
        for managed in candidates:
            released.append(await self.release_model(managed.model))
        return tuple(released)

    async def close(self) -> None:
        self._closed = True
        self._enabled = False
        if self._active_snapshot is not None:
            await asyncio.shield(self._active_snapshot)
        await asyncio.gather(*(provider.close() for provider in self._providers))

    def policy_summary(self) -> dict[str, str]:
        return {
            "auto_release": "activo" if self._auto_release_enabled else "inactivo",
            "idle_timeout_seconds": str(int(self._idle_timeout.total_seconds())),
            "max_managed_models": str(self._max_managed_models),
            "ram_limits": f"{self._ram_soft_limit:.0%} / {self._ram_hard_limit:.0%}",
            "vram_limits": f"{self._vram_soft_limit:.0%} / {self._vram_hard_limit:.0%}",
        }

    def _recent_inference(self) -> tuple[InferenceTelemetry, ...]:
        return tuple(
            sorted(self._inference.values(), key=lambda item: item.captured_at, reverse=True)
        )

    def _annotate_runtime(self, runtime: RuntimeTelemetry) -> RuntimeTelemetry:
        models: list[RuntimeModelTelemetry] = []
        for model in runtime.models:
            managed = self._managed.get(model.model.key) or self._suspended.get(model.model.key)
            models.append(
                model
                if managed is None
                else model.model_copy(
                    update={
                        "owned_by_app": True,
                        "pinned": managed.pinned,
                        "last_used_at": managed.last_used_at,
                    }
                )
            )
        return runtime.model_copy(
            update={"models": tuple(models), "queue_depth": len(self._queued)}
        )

    @staticmethod
    def _merge_processes(
        contributions: Sequence[TelemetryContribution],
    ) -> tuple[ProcessTelemetry, ...]:
        merged: dict[int, ProcessTelemetry] = {}
        for contribution in contributions:
            for process in contribution.processes:
                current = merged.get(process.pid)
                if current is None:
                    merged[process.pid] = process
                    continue
                merged[process.pid] = current.model_copy(
                    update={
                        "name": current.name
                        if not current.name.startswith("PID ")
                        else process.name,
                        "runtime": current.runtime or process.runtime,
                        "cpu_percent": current.cpu_percent
                        if current.cpu_percent is not None
                        else process.cpu_percent,
                        "ram_bytes": current.ram_bytes
                        if current.ram_bytes is not None
                        else process.ram_bytes,
                        "vram_bytes": current.vram_bytes
                        if current.vram_bytes is not None
                        else process.vram_bytes,
                        "owned_by_app": current.owned_by_app or process.owned_by_app,
                    }
                )
        return tuple(sorted(merged.values(), key=lambda item: item.pid))

    def _append_pressure_warnings(
        self,
        system: SystemTelemetry | None,
        contributions: Sequence[TelemetryContribution],
        warnings: list[str],
    ) -> None:
        if system is not None and system.ram_total_bytes:
            if system.ram_used_bytes / system.ram_total_bytes >= self._ram_soft_limit:
                warnings.append("RAM sobre el límite blando configurado.")
        for contribution in contributions:
            for gpu in contribution.gpus:
                if (
                    gpu.vram_total_bytes
                    and gpu.vram_used_bytes / gpu.vram_total_bytes >= self._vram_soft_limit
                ):
                    warnings.append(f"VRAM de GPU {gpu.index} sobre el límite blando configurado.")
