import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from aiopenstudio.core.contracts import (
    ComputeDevice,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    ResidencyState,
    TranscriptionEvent,
    TranscriptionEventKind,
    TranscriptionResult,
    TranscriptionSegment,
)
from aiopenstudio.services.dictation import LLMDictationService


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def model_state(self, model: ModelId) -> ModelState:
        return ModelState(
            model=model,
            ram_residency=ResidencyState.LOADED,
            gpu_residency=ResidencyState.LOADED,
            active_device=ComputeDevice.GPU,
        )

    async def reserve_model(self, model: ModelId) -> None:
        self.calls.append("reserve")

    def release_model_reservation(self, model: ModelId) -> None:
        self.calls.append("release")

    async def move_model_to_ram(self, model: ModelId) -> tuple[ModelState, LoadPolicy]:
        self.calls.append("move_to_ram")
        return ModelState(model=model, ram_residency=ResidencyState.LOADED), LoadPolicy()

    async def restore_model_to_device(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        self.calls.append("restore_gpu")
        return ModelState(
            model=model,
            ram_residency=ResidencyState.LOADED,
            gpu_residency=ResidencyState.LOADED,
        )


class FakeMonitor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def requires_device_yield(self, requester: ModelId, estimate: int | None) -> bool:
        self.calls.append("requires_yield")
        return True

    def suspend_model(self, model: ModelId) -> bool:
        self.calls.append("suspend")
        return True

    def resume_model(self, state: ModelState) -> None:
        self.calls.append("resume")

    async def snapshot(self) -> object:
        self.calls.append("snapshot")
        return object()


class FakeTranscription:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.model = ModelDescriptor(
            id=ModelId(runtime="faster-whisper", name="small", variant="small"),
            display_name="Whisper small",
            size_bytes=100,
            installed=True,
        )
        self.loaded = False

    @property
    def microphone_available(self) -> bool:
        return True

    async def start_recording(self) -> None:
        return None

    async def stop_recording(self, *, prefix: str = "whisper") -> Path:
        destination = self.source.parent / f"{prefix}.wav"
        destination.write_bytes(b"audio")
        return destination

    async def cancel_recording(self) -> None:
        return None

    async def remove_temporary_recording(self, path: Path) -> None:
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def refresh_models(self) -> tuple[ModelDescriptor, ...]:
        return (self.model,)

    @staticmethod
    def estimated_vram_bytes(descriptor: ModelDescriptor) -> int:
        return 200

    @staticmethod
    def create_operation_id() -> str:
        return "dictation"

    async def stream_transcription(
        self, request: object, *, load_policy: LoadPolicy
    ) -> AsyncIterator[TranscriptionEvent]:
        self.loaded = True
        segment = TranscriptionSegment(
            index=0,
            start_seconds=0,
            end_seconds=1,
            text=" voz",
        )
        yield TranscriptionEvent(
            operation_id="dictation",
            kind=TranscriptionEventKind.SEGMENT,
            segment=segment,
        )
        yield TranscriptionEvent(
            operation_id="dictation",
            kind=TranscriptionEventKind.COMPLETED,
            result=TranscriptionResult(
                operation_id="dictation",
                model=self.model.id,
                source_path=self.source,
                elapsed_seconds=1,
                segments=(segment,),
            ),
        )

    async def model_state(self, model: ModelId) -> ModelState:
        return ModelState(
            model=model,
            ram_residency=(ResidencyState.LOADED if self.loaded else ResidencyState.UNLOADED),
        )

    async def unload_model(self, model: ModelId) -> ModelState:
        self.loaded = False
        return ModelState(model=model)


def test_dictation_yields_and_restores_llm_device(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "recording.wav"
        source.write_bytes(b"audio")
        llm = FakeLLM()
        monitor = FakeMonitor()
        transcription = FakeTranscription(source)
        service = LLMDictationService(
            transcription,  # type: ignore[arg-type]
            llm,  # type: ignore[arg-type]
            monitor,  # type: ignore[arg-type]
        )
        llm_model = ModelId(runtime="ollama", name="llm")

        events = [event async for event in service.transcribe_for_llm(source, llm_model)]

        assert events[-1].kind is TranscriptionEventKind.COMPLETED
        assert llm.calls == ["reserve", "move_to_ram", "restore_gpu", "release"]
        assert monitor.calls == [
            "requires_yield",
            "suspend",
            "snapshot",
            "resume",
            "snapshot",
        ]
        assert not source.exists()

    asyncio.run(scenario())
