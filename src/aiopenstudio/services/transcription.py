"""Whisper use cases, export formats and local input validation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from uuid import uuid4

from aiopenstudio.core.contracts import (
    AudioRecorder,
    ComputeDevice,
    LoadPolicy,
    ModelCatalog,
    ModelDescriptor,
    ModelId,
    ModelState,
    ResidencyPolicy,
    ResourceMonitor,
    RuntimeHealth,
    TranscriptionEvent,
    TranscriptionEventKind,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionRuntime,
    UnloadTarget,
)
from aiopenstudio.core.errors import RuntimeRequestError


class TranscriptionService:
    """Coordinate local-only models, residency, transcription and export."""

    def __init__(
        self,
        runtime: TranscriptionRuntime,
        catalog: ModelCatalog,
        *,
        residency_policy: ResidencyPolicy | None = None,
        resource_monitor: ResourceMonitor | None = None,
        recorder: AudioRecorder | None = None,
        recordings_dir: Path | None = None,
        max_input_bytes: int = 4 * 1024 * 1024 * 1024,
    ) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._residency_policy = residency_policy
        self._resource_monitor = resource_monitor
        self._recorder = recorder
        self._recordings_dir = recordings_dir
        self._max_input_bytes = max_input_bytes
        self._operation_gate = asyncio.Lock()
        self._load_policies: dict[str, LoadPolicy] = {}

    @property
    def runtime(self) -> TranscriptionRuntime:
        return self._runtime

    async def health(self) -> RuntimeHealth:
        return await self._runtime.health()

    async def refresh_models(self) -> Sequence[ModelDescriptor]:
        models = tuple(await self._runtime.list_models())
        live_keys = {descriptor.id.key for descriptor in models}
        for stale in self._catalog.list(runtime=self._runtime.name):
            if stale.id.key not in live_keys:
                self._catalog.remove(stale.id)
        for descriptor in models:
            self._catalog.save(descriptor)
        return models

    async def load_model(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        active = await self.active_model_state()
        if active is not None and (
            active.model != model
            or (
                policy.device is not ComputeDevice.AUTO
                and active.active_device is not None
                and active.active_device is not policy.device
            )
        ):
            await self.unload_model(active.model)
        elif active is not None:
            if self._residency_policy is not None:
                self._residency_policy.model_used(model)
            return active

        descriptor = self._catalog.get(model)
        if self._resource_monitor is not None:
            await self._resource_monitor.snapshot()
        if self._residency_policy is not None:
            await self._residency_policy.before_load(
                model,
                policy,
                descriptor.size_bytes if descriptor else None,
            )
        try:
            state = await self._runtime.load(model, policy)
        except Exception:
            if self._residency_policy is not None:
                self._residency_policy.model_load_failed(model)
            raise
        if self._residency_policy is not None:
            self._residency_policy.model_loaded(state, policy)
        self._load_policies[model.key] = policy
        if self._resource_monitor is not None:
            await self._resource_monitor.snapshot()
        return state

    async def unload_model(self, model: ModelId) -> ModelState:
        state = await self._runtime.unload(model, UnloadTarget.ALL)
        if self._residency_policy is not None:
            self._residency_policy.model_unloaded(model)
        self._load_policies.pop(model.key, None)
        if self._resource_monitor is not None:
            await self._resource_monitor.snapshot()
        return state

    async def model_state(self, model: ModelId) -> ModelState:
        return await self._runtime.state(model)

    async def active_model_state(self) -> ModelState | None:
        """Return the model actually resident in this runtime, if any."""
        for descriptor in await self._runtime.list_models():
            state = await self._runtime.state(descriptor.id)
            if state.loaded_in_ram or state.loaded_in_gpu:
                return state
        return None

    async def reserve_runtime(self) -> None:
        """Wait for active transcription and prevent a new one from starting."""
        await self._operation_gate.acquire()

    def release_runtime_reservation(self) -> None:
        if self._operation_gate.locked():
            self._operation_gate.release()

    def load_policy(self, model: ModelId) -> LoadPolicy:
        return self._load_policies.get(model.key, LoadPolicy())

    @property
    def microphone_available(self) -> bool:
        return self._recorder is not None and self._recorder.available

    async def start_recording(self) -> None:
        if self._recorder is None:
            raise RuntimeRequestError("La captura por micrófono no está configurada.")
        await self._recorder.start()

    async def stop_recording(self, *, prefix: str = "whisper") -> Path:
        if self._recorder is None or self._recordings_dir is None:
            raise RuntimeRequestError("La captura por micrófono no está configurada.")
        destination = self._recordings_dir / f"{prefix}-{uuid4()}.wav"
        return await self._recorder.stop(destination)

    async def cancel_recording(self) -> None:
        if self._recorder is not None:
            await self._recorder.cancel()

    async def remove_temporary_recording(self, path: Path) -> None:
        if self._recordings_dir is None:
            return
        await asyncio.to_thread(self._remove_recording_blocking, path)

    def _remove_recording_blocking(self, path: Path) -> None:
        if self._recordings_dir is None:
            return
        recordings_root = self._recordings_dir.resolve()
        candidate = path.resolve()
        if candidate.parent == recordings_root:
            candidate.unlink(missing_ok=True)

    async def stream_transcription(
        self,
        request: TranscriptionRequest,
        *,
        load_policy: LoadPolicy | None = None,
    ) -> AsyncIterator[TranscriptionEvent]:
        async with self._operation_gate:
            self._validate_source(request.source_path)
            state = await self._runtime.state(request.model)
            implicit_load = not state.loaded_in_ram and not state.loaded_in_gpu
            if implicit_load:
                await self.load_model(request.model, load_policy or LoadPolicy())
            elif self._residency_policy is not None:
                self._residency_policy.model_used(request.model)

            try:
                async for event in self._runtime.transcribe(request):
                    if (
                        event.kind
                        in {
                            TranscriptionEventKind.COMPLETED,
                            TranscriptionEventKind.CANCELLED,
                        }
                        and self._residency_policy is not None
                    ):
                        self._residency_policy.model_used(request.model)
                    yield event
            finally:
                if self._resource_monitor is not None:
                    await self._resource_monitor.snapshot()

    async def cancel(self, operation_id: str) -> None:
        await self._runtime.cancel(operation_id)

    def descriptor(self, model: ModelId) -> ModelDescriptor | None:
        return self._catalog.get(model)

    @staticmethod
    def create_operation_id() -> str:
        return str(uuid4())

    @staticmethod
    def estimated_vram_bytes(descriptor: ModelDescriptor | None) -> int | None:
        if descriptor is None or descriptor.size_bytes is None:
            return None
        return max(descriptor.size_bytes * 2, descriptor.size_bytes + 512 * 1024 * 1024)

    def export(self, result: TranscriptionResult, destination: Path) -> Path:
        suffix = destination.suffix.casefold()
        if suffix not in {".txt", ".json", ".srt", ".vtt"}:
            raise ValueError("Formato de exportación no compatible: usa TXT, JSON, SRT o VTT.")
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        if suffix == ".txt":
            content = result.text + "\n"
        elif suffix == ".json":
            content = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
        elif suffix == ".srt":
            content = _subtitle(result, webvtt=False)
        else:
            content = "WEBVTT\n\n" + _subtitle(result, webvtt=True)
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(destination)
        return destination

    def _validate_source(self, source_path: Path) -> None:
        if not source_path.is_file():
            raise RuntimeRequestError("El archivo de audio seleccionado no existe.")
        if source_path.stat().st_size > self._max_input_bytes:
            raise RuntimeRequestError("El archivo supera el límite local configurado.")


def _subtitle(result: TranscriptionResult, *, webvtt: bool) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(result.segments, start=1):
        separator = "." if webvtt else ","
        timing = (
            f"{_timestamp(segment.start_seconds, separator)} --> "
            f"{_timestamp(segment.end_seconds, separator)}"
        )
        blocks.append(f"{index}\n{timing}\n{segment.text.strip()}\n")
    return "\n".join(blocks)


def _timestamp(seconds: float, separator: str) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{millis:03d}"
