"""Microphone dictation and temporary LLM/Whisper device orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from aiopenstudio.core.contracts import (
    ComputeDevice,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    TranscriptionEvent,
    TranscriptionOptions,
    TranscriptionRequest,
)
from aiopenstudio.core.errors import RuntimeRequestError
from aiopenstudio.services.llm import LLMService
from aiopenstudio.services.resource_monitor import ResourceMonitorService
from aiopenstudio.services.transcription import TranscriptionService


class LLMDictationService:
    """Transcribe microphone input and temporarily yield LLM VRAM when required."""

    def __init__(
        self,
        transcription: TranscriptionService,
        llm: LLMService,
        monitor: ResourceMonitorService,
    ) -> None:
        self._transcription = transcription
        self._llm = llm
        self._monitor = monitor

    @property
    def microphone_available(self) -> bool:
        return self._transcription.microphone_available

    async def start_recording(self) -> None:
        await self._transcription.start_recording()

    async def stop_recording(self) -> Path:
        return await self._transcription.stop_recording(prefix="dictation")

    async def cancel_recording(self) -> None:
        await self._transcription.cancel_recording()

    async def default_model(self) -> ModelDescriptor:
        models = tuple(await self._transcription.refresh_models())
        if not models:
            raise RuntimeRequestError("No hay modelos faster-whisper locales disponibles.")
        return next(
            (model for model in models if model.id.variant == "small"),
            models[0],
        )

    async def transcribe_for_llm(
        self,
        source_path: Path,
        llm_model: ModelId | None,
        *,
        language: str | None = None,
    ) -> AsyncIterator[TranscriptionEvent]:
        whisper_descriptor = await self.default_model()
        estimate = self._transcription.estimated_vram_bytes(whisper_descriptor)
        should_yield = False
        llm_policy: LoadPolicy | None = None
        llm_was_suspended = False
        llm_reserved = False

        if llm_model is not None:
            llm_state = await self._llm.model_state(llm_model)
            if llm_state.loaded_in_ram or llm_state.loaded_in_gpu:
                should_yield = await self._monitor.requires_device_yield(
                    whisper_descriptor.id,
                    estimate,
                )
            if should_yield:
                await self._llm.reserve_model(llm_model)
                llm_reserved = True
                try:
                    _, llm_policy = await self._llm.move_model_to_ram(llm_model)
                    llm_was_suspended = self._monitor.suspend_model(llm_model)
                    await self._monitor.snapshot()
                except Exception:
                    self._llm.release_model_reservation(llm_model)
                    await self._transcription.remove_temporary_recording(source_path)
                    raise

        request = TranscriptionRequest(
            operation_id=self._transcription.create_operation_id(),
            model=whisper_descriptor.id,
            source_path=source_path,
            options=TranscriptionOptions(language=language),
        )
        try:
            async for event in self._transcription.stream_transcription(
                request,
                load_policy=LoadPolicy(device=ComputeDevice.GPU),
            ):
                yield event
        finally:
            try:
                whisper_state = await self._transcription.model_state(whisper_descriptor.id)
                if whisper_state.loaded_in_ram or whisper_state.loaded_in_gpu:
                    await self._transcription.unload_model(whisper_descriptor.id)
            finally:
                try:
                    if llm_model is not None and llm_policy is not None:
                        restored = await self._llm.restore_model_to_device(
                            llm_model,
                            llm_policy,
                        )
                        if llm_was_suspended:
                            self._monitor.resume_model(restored)
                        await self._monitor.snapshot()
                finally:
                    try:
                        await self._transcription.remove_temporary_recording(source_path)
                    finally:
                        if llm_model is not None and llm_reserved:
                            self._llm.release_model_reservation(llm_model)
