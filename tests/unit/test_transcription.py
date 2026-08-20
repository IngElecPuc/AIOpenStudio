import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from aiopenstudio.core.contracts import (
    ComputeDevice,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    RuntimeCapabilities,
    RuntimeHealth,
    TranscriptionEvent,
    TranscriptionEventKind,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    UnloadTarget,
)
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.infrastructure.runtimes.whisper import FasterWhisperRuntime
from aiopenstudio.services import TranscriptionService


class FakeTranscriptionRuntime:
    name = "fake-whisper"
    capabilities = RuntimeCapabilities(
        supports_device_selection=True,
        supports_streaming=True,
        supports_cancellation=True,
    )

    def __init__(self) -> None:
        self.descriptors = (
            ModelDescriptor(
                id=ModelId(runtime=self.name, name="small", variant="small"),
                display_name="Whisper small",
                size_bytes=100,
                installed=True,
            ),
            ModelDescriptor(
                id=ModelId(runtime=self.name, name="medium", variant="medium"),
                display_name="Whisper medium",
                size_bytes=200,
                installed=True,
            ),
        )
        self.descriptor = self.descriptors[0]
        self.loaded_model: ModelId | None = None
        self.calls: list[str] = []
        self.cancelled: list[str] = []

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth.READY

    async def process_state(self) -> ProcessState:
        return ProcessState.RUNNING if self.loaded_model else ProcessState.STOPPED

    async def start(self) -> ProcessState:
        return ProcessState.RUNNING

    async def stop(self) -> ProcessState:
        self.loaded_model = None
        return ProcessState.STOPPED

    async def list_models(self) -> Sequence[ModelDescriptor]:
        return self.descriptors

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        self.loaded_model = model
        self.calls.append(f"load:{model.name}")
        return ModelState(
            model=model,
            runtime_health=RuntimeHealth.READY,
            process_state=ProcessState.RUNNING,
            ram_residency=ResidencyState.LOADED,
            gpu_residency=(
                ResidencyState.LOADED
                if policy.device is ComputeDevice.GPU
                else ResidencyState.UNLOADED
            ),
            active_device=policy.device,
        )

    async def unload(self, model: ModelId, target: UnloadTarget = UnloadTarget.ALL) -> ModelState:
        self.calls.append(f"unload:{model.name}")
        if self.loaded_model == model:
            self.loaded_model = None
        return ModelState(model=model, runtime_health=RuntimeHealth.READY)

    async def state(self, model: ModelId) -> ModelState:
        return ModelState(
            model=model,
            runtime_health=RuntimeHealth.READY,
            ram_residency=(
                ResidencyState.LOADED
                if self.loaded_model == model
                else ResidencyState.UNLOADED
            ),
            active_device=(ComputeDevice.CPU if self.loaded_model == model else None),
        )

    async def transcribe(self, request: TranscriptionRequest) -> AsyncIterator[TranscriptionEvent]:
        segment = TranscriptionSegment(
            index=0,
            start_seconds=0,
            end_seconds=1,
            text=" Hola mundo",
        )
        yield TranscriptionEvent(
            operation_id=request.operation_id,
            kind=TranscriptionEventKind.SEGMENT,
            segment=segment,
        )
        yield TranscriptionEvent(
            operation_id=request.operation_id,
            kind=TranscriptionEventKind.COMPLETED,
            result=TranscriptionResult(
                operation_id=request.operation_id,
                model=request.model,
                source_path=request.source_path,
                duration_seconds=1,
                elapsed_seconds=0.5,
                segments=(segment,),
            ),
        )

    async def cancel(self, operation_id: str) -> None:
        self.cancelled.append(operation_id)


class SingleResidentPolicy:
    def __init__(self) -> None:
        self.resident: ModelId | None = None

    async def before_load(
        self,
        model: ModelId,
        policy: LoadPolicy,
        estimated_weight_bytes: int | None = None,
    ) -> None:
        del model, policy, estimated_weight_bytes
        if self.resident is not None:
            raise RuntimeError("Only one managed model is allowed")

    def model_loaded(self, state: ModelState, policy: LoadPolicy) -> None:
        del policy
        self.resident = state.model

    def model_load_failed(self, model: ModelId) -> None:
        del model

    def model_used(self, model: ModelId) -> None:
        del model

    def model_unloaded(self, model: ModelId) -> None:
        if self.resident == model:
            self.resident = None


def test_service_implicitly_loads_and_exports_all_formats(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeTranscriptionRuntime()
        policy = SingleResidentPolicy()
        service = TranscriptionService(runtime, store, residency_policy=policy)
        await service.refresh_models()
        source = tmp_path / "audio.wav"
        source.write_bytes(b"RIFF-test")
        request = TranscriptionRequest(
            operation_id="transcription-1",
            model=runtime.descriptor.id,
            source_path=source,
        )

        events = [
            event
            async for event in service.stream_transcription(
                request,
                load_policy=LoadPolicy(device=ComputeDevice.CPU),
            )
        ]
        result = events[-1].result

        assert runtime.loaded_model == runtime.descriptor.id
        assert result is not None
        assert result.text == "Hola mundo"
        for suffix in (".txt", ".json", ".srt", ".vtt"):
            output = service.export(result, tmp_path / f"result{suffix}")
            assert output.is_file()
            assert "Hola mundo" in output.read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_loading_a_different_model_replaces_the_resident_model(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeTranscriptionRuntime()
        policy = SingleResidentPolicy()
        service = TranscriptionService(runtime, store, residency_policy=policy)
        await service.refresh_models()
        small, medium = (descriptor.id for descriptor in runtime.descriptors)

        await service.load_model(small, LoadPolicy(device=ComputeDevice.CPU))
        state = await service.load_model(medium, LoadPolicy(device=ComputeDevice.CPU))

        assert state.model == medium
        assert runtime.loaded_model == medium
        assert policy.resident == medium
        assert runtime.calls == ["load:small", "unload:small", "load:medium"]
        active = await service.active_model_state()
        assert active is not None
        assert active.model == state.model

    asyncio.run(scenario())


def test_runtime_discovers_only_complete_local_models(tmp_path: Path) -> None:
    complete = tmp_path / "Systran" / "faster-whisper-small"
    complete.mkdir(parents=True)
    for filename in ("config.json", "model.bin", "tokenizer.json"):
        (complete / filename).write_bytes(b"x")
    incomplete = tmp_path / "faster-whisper-incomplete"
    incomplete.mkdir()
    (incomplete / "config.json").write_text("{}", encoding="utf-8")

    models = asyncio.run(FasterWhisperRuntime(tmp_path).list_models())

    assert [model.id.variant for model in models] == ["small"]
    assert models[0].weights_path == complete.resolve()


def test_transcription_contract_rejects_reversed_timestamps() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TranscriptionSegment(
            index=0,
            start_seconds=2,
            end_seconds=1,
            text="invalid",
        )
