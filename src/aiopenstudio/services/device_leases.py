"""Exclusive GPU leases with reversible suspension of resident suites."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import TracebackType

from aiopenstudio.core.contracts import ComputeDevice, LoadPolicy, ModelId, ModelState
from aiopenstudio.services.llm import LLMService
from aiopenstudio.services.resource_monitor import ResourceMonitorService
from aiopenstudio.services.transcription import TranscriptionService


@dataclass(slots=True)
class _LLMSuspension:
    state: ModelState
    policy: LoadPolicy
    reserved: bool
    monitor_suspended: bool


@dataclass(slots=True)
class _WhisperSuspension:
    state: ModelState
    policy: LoadPolicy
    reserved: bool


class DeviceLease:
    def __init__(self, coordinator: DeviceLeaseCoordinator, requester: ModelId) -> None:
        self._coordinator = coordinator
        self.requester = requester
        self._llm: _LLMSuspension | None = None
        self._whisper: _WhisperSuspension | None = None
        self._entered = False

    async def __aenter__(self) -> DeviceLease:
        await self._coordinator._lock.acquire()
        self._entered = True
        try:
            await self._suspend_llm()
            await self._suspend_whisper()
            await self._coordinator.monitor.snapshot()
            return self
        except Exception:
            await self._restore()
            self._release_lock()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        try:
            await self._restore()
        finally:
            self._release_lock()

    async def _suspend_llm(self) -> None:
        service = self._coordinator.llm
        if service is None:
            return
        state = await service.active_model_state()
        if state is None:
            return
        await service.reserve_model(state.model)
        suspension = _LLMSuspension(
            state=state,
            policy=service.load_policy(state.model),
            reserved=True,
            monitor_suspended=False,
        )
        self._llm = suspension
        if state.active_device is ComputeDevice.GPU:
            await service.move_model_to_ram(state.model)
        suspension.monitor_suspended = self._coordinator.monitor.suspend_model(state.model)

    async def _suspend_whisper(self) -> None:
        service = self._coordinator.transcription
        if service is None:
            return
        await service.reserve_runtime()
        state = await service.active_model_state()
        if state is None:
            service.release_runtime_reservation()
            return
        suspension = _WhisperSuspension(
            state=state,
            policy=service.load_policy(state.model),
            reserved=True,
        )
        self._whisper = suspension
        await service.unload_model(state.model)

    async def _restore(self) -> None:
        errors: list[Exception] = []
        if self._whisper is not None:
            whisper_suspension = self._whisper
            try:
                transcription = self._coordinator.transcription
                if transcription is not None:
                    await transcription.load_model(
                        whisper_suspension.state.model, whisper_suspension.policy
                    )
            except Exception as error:
                errors.append(error)
            finally:
                if (
                    whisper_suspension.reserved
                    and self._coordinator.transcription is not None
                ):
                    self._coordinator.transcription.release_runtime_reservation()
                self._whisper = None
        if self._llm is not None:
            llm_suspension = self._llm
            try:
                if (
                    llm_suspension.state.active_device is ComputeDevice.GPU
                    and self._coordinator.llm is not None
                ):
                    restored = await self._coordinator.llm.restore_model_to_device(
                        llm_suspension.state.model, llm_suspension.policy
                    )
                    if llm_suspension.monitor_suspended:
                        self._coordinator.monitor.resume_model(restored)
                elif llm_suspension.monitor_suspended:
                    self._coordinator.monitor.resume_model(llm_suspension.state)
            except Exception as error:
                errors.append(error)
            finally:
                if llm_suspension.reserved and self._coordinator.llm is not None:
                    self._coordinator.llm.release_model_reservation(
                        llm_suspension.state.model
                    )
                self._llm = None
        await self._coordinator.monitor.snapshot()
        if errors:
            raise ExceptionGroup("No fue posible restaurar todas las suites", errors)

    def _release_lock(self) -> None:
        if self._entered and self._coordinator._lock.locked():
            self._coordinator._lock.release()
        self._entered = False


class DeviceLeaseCoordinator:
    def __init__(
        self,
        monitor: ResourceMonitorService,
        *,
        llm: LLMService | None = None,
        transcription: TranscriptionService | None = None,
    ) -> None:
        self.monitor = monitor
        self.llm = llm
        self.transcription = transcription
        self._lock = asyncio.Lock()

    def lease(self, requester: ModelId) -> DeviceLease:
        return DeviceLease(self, requester)
