"""Process-isolated faster-whisper adapter with no implicit model downloads."""

from __future__ import annotations

import asyncio
import importlib.util
import multiprocessing
import queue
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, cast

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
    TranscriptionProgress,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionStage,
    UnloadTarget,
)
from aiopenstudio.core.errors import (
    ModelNotInstalledError,
    ResourceExhaustedError,
    RuntimeRequestError,
    RuntimeUnavailableError,
)

_RUNTIME_NAME = "faster-whisper"
_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")


class FasterWhisperRuntime:
    """Own one faster-whisper model in a disposable Windows worker process."""

    def __init__(self, models_root: Path, *, cancel_grace_seconds: float = 2.0) -> None:
        self._models_root = models_root.resolve()
        self._cancel_grace_seconds = cancel_grace_seconds
        self._context = multiprocessing.get_context("spawn")
        self._commands: Any | None = None
        self._responses: Any | None = None
        self._cancel_event: Any | None = None
        self._process: Any | None = None
        self._loaded_model: ModelId | None = None
        self._active_device: ComputeDevice | None = None
        self._active_operation: str | None = None
        self._forced_cancelled: set[str] = set()
        self._pinned_in_ram = False
        self._pinned_on_device = False

    @property
    def name(self) -> str:
        return _RUNTIME_NAME

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            manages_process=True,
            supports_device_selection=True,
            supports_partial_unload=True,
            supports_streaming=True,
            supports_cancellation=True,
        )

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None and self._process.is_alive() else None

    async def health(self) -> RuntimeHealth:
        package_available = importlib.util.find_spec("faster_whisper") is not None
        models = self._discover_models()
        if not package_available:
            return RuntimeHealth.UNAVAILABLE
        return RuntimeHealth.READY if models else RuntimeHealth.DEGRADED

    async def process_state(self) -> ProcessState:
        if self._process is None:
            return ProcessState.STOPPED
        return ProcessState.RUNNING if self._process.is_alive() else ProcessState.FAILED

    async def start(self) -> ProcessState:
        if importlib.util.find_spec("faster_whisper") is None:
            raise RuntimeUnavailableError(
                "faster-whisper no está instalado en el intérprete configurado."
            )
        self._ensure_process()
        return ProcessState.RUNNING

    async def stop(self) -> ProcessState:
        await asyncio.to_thread(self._stop_blocking)
        return ProcessState.STOPPED

    async def close(self) -> None:
        await self.stop()

    async def list_models(self) -> Sequence[ModelDescriptor]:
        return self._discover_models()

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        self._validate_model(model)
        descriptor = self._descriptor_for(model)
        if descriptor is None or descriptor.weights_path is None:
            raise ModelNotInstalledError(f"El modelo Whisper {model.name!r} no está instalado.")
        if self._active_operation is not None:
            raise RuntimeRequestError("No se puede cambiar el modelo durante una transcripción.")
        if self._loaded_model is not None and self._loaded_model.key != model.key:
            await self.stop()

        await self.start()
        response = await asyncio.to_thread(
            self._request_blocking,
            {
                "kind": "load",
                "model_path": str(descriptor.weights_path),
                "device": policy.device.value,
            },
            180.0,
        )
        self._raise_worker_error(response)
        actual_device = ComputeDevice(str(response["device"]))
        self._loaded_model = model
        self._active_device = actual_device
        self._pinned_in_ram = policy.pin_in_ram
        self._pinned_on_device = policy.pin_on_device
        return await self.state(model)

    async def unload(
        self,
        model: ModelId,
        target: UnloadTarget = UnloadTarget.ALL,
    ) -> ModelState:
        self._validate_model(model)
        if self._loaded_model is None:
            return self._unloaded_state(model)
        if self._loaded_model.key != model.key:
            raise RuntimeRequestError(f"El modelo activo es {self._loaded_model.name!r}.")
        if self._active_operation is not None:
            raise RuntimeRequestError("Cancela la transcripción antes de liberar el modelo.")

        if target is UnloadTarget.DEVICE and self._active_device is ComputeDevice.GPU:
            response = await asyncio.to_thread(
                self._request_blocking,
                {"kind": "offload_to_cpu"},
                60.0,
            )
            self._raise_worker_error(response)
            self._active_device = ComputeDevice.CPU
            self._pinned_on_device = False
            return await self.state(model)

        await self.stop()
        return self._unloaded_state(model)

    async def state(self, model: ModelId) -> ModelState:
        self._validate_model(model)
        health = await self.health()
        process = await self.process_state()
        if self._loaded_model is None or self._loaded_model.key != model.key:
            return ModelState(
                model=model,
                runtime_health=health,
                process_state=process,
                detail="Modelo local disponible, pero no residente.",
            )
        on_gpu = self._active_device is ComputeDevice.GPU
        return ModelState(
            model=model,
            runtime_health=health,
            process_state=process,
            ram_residency=ResidencyState.LOADED,
            gpu_residency=ResidencyState.LOADED if on_gpu else ResidencyState.UNLOADED,
            active_device=self._active_device,
            pinned_in_ram=self._pinned_in_ram,
            pinned_on_device=self._pinned_on_device and on_gpu,
            detail="Residencia administrada por el worker aislado de faster-whisper.",
        )

    async def transcribe(self, request: TranscriptionRequest) -> AsyncIterator[TranscriptionEvent]:
        self._validate_model(request.model)
        if self._loaded_model is None or self._loaded_model.key != request.model.key:
            raise RuntimeRequestError("Carga el modelo Whisper antes de transcribir.")
        if self._active_operation is not None:
            raise RuntimeRequestError("Ya existe una transcripción activa.")
        if not request.source_path.is_file():
            raise RuntimeRequestError("El archivo de audio seleccionado no existe.")
        if self._commands is None or self._responses is None or self._cancel_event is None:
            raise RuntimeUnavailableError("El worker Whisper no está disponible.")

        self._active_operation = request.operation_id
        self._cancel_event.clear()
        self._commands.put(
            {
                "kind": "transcribe",
                "operation_id": request.operation_id,
                "model": request.model.model_dump(mode="json"),
                "source_path": str(request.source_path.resolve()),
                "options": request.options.model_dump(mode="json"),
            }
        )
        try:
            while True:
                try:
                    message = await asyncio.to_thread(self._responses.get, True, 0.25)
                except queue.Empty:
                    process = self._process
                    if process is None or not process.is_alive():
                        if request.operation_id in self._forced_cancelled:
                            self._forced_cancelled.discard(request.operation_id)
                            yield TranscriptionEvent(
                                operation_id=request.operation_id,
                                kind=TranscriptionEventKind.CANCELLED,
                                message="Cancelación forzada; el worker fue detenido.",
                            )
                            return
                        raise RuntimeUnavailableError(
                            "El worker Whisper terminó inesperadamente."
                        ) from None
                    continue
                if message.get("operation_id") != request.operation_id:
                    continue
                event = self._event_from_message(request, message)
                yield event
                if event.kind in {
                    TranscriptionEventKind.COMPLETED,
                    TranscriptionEventKind.CANCELLED,
                    TranscriptionEventKind.ERROR,
                }:
                    return
        finally:
            self._active_operation = None

    async def cancel(self, operation_id: str) -> None:
        if self._active_operation != operation_id or self._cancel_event is None:
            return
        self._cancel_event.set()
        await asyncio.sleep(self._cancel_grace_seconds)
        if self._active_operation == operation_id:
            self._forced_cancelled.add(operation_id)
            await self.stop()

    def _event_from_message(
        self,
        request: TranscriptionRequest,
        message: dict[str, Any],
    ) -> TranscriptionEvent:
        kind = TranscriptionEventKind(str(message["kind"]))
        if kind is TranscriptionEventKind.PROGRESS:
            return TranscriptionEvent(
                operation_id=request.operation_id,
                kind=kind,
                progress=TranscriptionProgress.model_validate(message["progress"]),
            )
        if kind is TranscriptionEventKind.SEGMENT:
            return TranscriptionEvent(
                operation_id=request.operation_id,
                kind=kind,
                segment=TranscriptionSegment.model_validate(message["segment"]),
            )
        if kind in {TranscriptionEventKind.COMPLETED, TranscriptionEventKind.CANCELLED}:
            result_data = message.get("result")
            return TranscriptionEvent(
                operation_id=request.operation_id,
                kind=kind,
                result=TranscriptionResult.model_validate(result_data) if result_data else None,
                message=message.get("message"),
            )
        return TranscriptionEvent(
            operation_id=request.operation_id,
            kind=kind,
            message=message.get("message"),
        )

    def _discover_models(self) -> tuple[ModelDescriptor, ...]:
        if not self._models_root.is_dir():
            return ()
        descriptors: list[ModelDescriptor] = []
        for config_path in self._models_root.rglob("config.json"):
            model_path = config_path.parent.resolve()
            if not all((model_path / name).is_file() for name in _MODEL_FILES):
                continue
            try:
                model_path.relative_to(self._models_root)
            except ValueError:
                continue
            size_bytes = sum(
                candidate.stat().st_size
                for candidate in model_path.rglob("*")
                if candidate.is_file()
            )
            name = model_path.name
            variant = name.removeprefix("faster-whisper-") or name
            descriptors.append(
                ModelDescriptor(
                    id=ModelId(runtime=self.name, name=name, variant=variant),
                    display_name=f"Whisper {variant}",
                    capabilities=frozenset({"speech-to-text", "translation", "timestamps"}),
                    weights_path=model_path,
                    size_bytes=size_bytes,
                    installed=True,
                    metadata={"backend": "faster-whisper", "local_only": True},
                )
            )
        return tuple(sorted(descriptors, key=lambda item: item.size_bytes or 0))

    def _descriptor_for(self, model: ModelId) -> ModelDescriptor | None:
        return next((item for item in self._discover_models() if item.id.key == model.key), None)

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._commands = self._context.Queue()
        self._responses = self._context.Queue()
        self._cancel_event = self._context.Event()
        process = self._context.Process(
            target=_worker_main,
            args=(self._commands, self._responses, self._cancel_event),
            name="aiopenstudio-whisper",
            daemon=True,
        )
        process.start()
        self._process = process

    def _request_blocking(self, command: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self._commands is None or self._responses is None:
            raise RuntimeUnavailableError("El worker Whisper no está iniciado.")
        self._commands.put(command)
        try:
            return cast(dict[str, Any], self._responses.get(timeout=timeout))
        except queue.Empty as error:
            raise RuntimeUnavailableError("El worker Whisper no respondió a tiempo.") from error

    def _stop_blocking(self) -> None:
        process = self._process
        if process is not None and process.is_alive():
            if self._commands is not None and self._active_operation is None:
                self._commands.put({"kind": "stop"})
                process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        self._process = None
        self._commands = None
        self._responses = None
        self._cancel_event = None
        self._loaded_model = None
        self._active_device = None
        self._pinned_in_ram = False
        self._pinned_on_device = False

    @staticmethod
    def _raise_worker_error(response: dict[str, Any]) -> None:
        if response.get("kind") != "error":
            return
        message = str(response.get("message", "Error desconocido del worker Whisper."))
        if response.get("error_type") == "resource_exhausted":
            raise ResourceExhaustedError(message)
        raise RuntimeRequestError(message)

    def _unloaded_state(self, model: ModelId) -> ModelState:
        return ModelState(
            model=model,
            runtime_health=RuntimeHealth.READY,
            process_state=ProcessState.STOPPED,
            detail="El modelo fue liberado de RAM y VRAM.",
        )

    def _validate_model(self, model: ModelId) -> None:
        if model.runtime != self.name:
            raise RuntimeRequestError(
                f"El modelo {model.key!r} no pertenece al runtime {self.name!r}."
            )


def _worker_main(commands: Any, responses: Any, cancel_event: Any) -> None:
    model: Any | None = None
    model_path: str | None = None
    device = "cpu"
    while True:
        command = commands.get()
        kind = command.get("kind")
        if kind == "stop":
            return
        try:
            if kind == "load":
                ctranslate2 = importlib.import_module("ctranslate2")
                WhisperModel = importlib.import_module("faster_whisper").WhisperModel

                requested = str(command["device"])
                if requested == "gpu":
                    device = "cuda"
                elif requested == "cpu":
                    device = "cpu"
                else:
                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                compute_type = "int8_float16" if device == "cuda" else "int8"
                requested_path = str(command["model_path"])
                if model is None or model_path != requested_path:
                    model = WhisperModel(
                        requested_path,
                        device=device,
                        compute_type=compute_type,
                        local_files_only=True,
                    )
                    model_path = requested_path
                elif not model.model.model_is_loaded:
                    model.model.load_model()
                responses.put({"kind": "loaded", "device": "gpu" if device == "cuda" else "cpu"})
            elif kind == "offload_to_cpu":
                if model is not None:
                    model.model.unload_model(to_cpu=True)
                responses.put({"kind": "offloaded", "device": "cpu"})
            elif kind == "transcribe":
                if model is None:
                    raise RuntimeError("No hay un modelo cargado en el worker.")
                _worker_transcribe(model, command, responses, cancel_event)
        except Exception as error:
            message = str(error)
            lowered = message.casefold()
            exhausted = any(
                token in lowered for token in ("out of memory", "cuda_error_out_of_memory")
            )
            responses.put(
                {
                    "kind": "error",
                    "operation_id": command.get("operation_id"),
                    "error_type": "resource_exhausted" if exhausted else type(error).__name__,
                    "message": message,
                }
            )


def _worker_transcribe(
    model: Any,
    command: dict[str, Any],
    responses: Any,
    cancel_event: Any,
) -> None:
    operation_id = str(command["operation_id"])
    source_path = Path(str(command["source_path"]))
    options = dict(command["options"])
    model_id = ModelId.model_validate(command["model"])
    started = time.perf_counter()
    responses.put({"kind": TranscriptionEventKind.STARTED.value, "operation_id": operation_id})
    responses.put(
        {
            "kind": TranscriptionEventKind.PROGRESS.value,
            "operation_id": operation_id,
            "progress": {
                "stage": TranscriptionStage.DECODING.value,
                "detail": "Decodificando audio",
            },
        }
    )
    segments_iterator, info = model.transcribe(
        str(source_path),
        language=options.get("language"),
        task=options.get("task", "transcribe"),
        beam_size=int(options.get("beam_size", 5)),
        vad_filter=bool(options.get("vad_filter", True)),
        word_timestamps=bool(options.get("word_timestamps", False)),
    )
    total = float(info.duration)
    serialized_segments: list[dict[str, Any]] = []
    for index, segment in enumerate(segments_iterator):
        words = tuple(
            {
                "start_seconds": float(word.start),
                "end_seconds": float(word.end),
                "text": str(word.word),
                "probability": float(word.probability),
            }
            for word in (segment.words or ())
        )
        serialized = {
            "index": index,
            "start_seconds": float(segment.start),
            "end_seconds": float(segment.end),
            "text": str(segment.text),
            "words": words,
        }
        serialized_segments.append(serialized)
        responses.put(
            {
                "kind": TranscriptionEventKind.SEGMENT.value,
                "operation_id": operation_id,
                "segment": serialized,
            }
        )
        processed = min(float(segment.end), total)
        responses.put(
            {
                "kind": TranscriptionEventKind.PROGRESS.value,
                "operation_id": operation_id,
                "progress": {
                    "stage": TranscriptionStage.TRANSCRIBING.value,
                    "processed_seconds": processed,
                    "total_seconds": total,
                    "fraction": processed / total if total else None,
                },
            }
        )
        if cancel_event.is_set():
            result = _result_payload(
                operation_id, model_id, source_path, info, started, serialized_segments, True
            )
            responses.put(
                {
                    "kind": TranscriptionEventKind.CANCELLED.value,
                    "operation_id": operation_id,
                    "result": result,
                    "message": "Transcripción cancelada.",
                }
            )
            return
    result = _result_payload(
        operation_id, model_id, source_path, info, started, serialized_segments, False
    )
    responses.put(
        {
            "kind": TranscriptionEventKind.COMPLETED.value,
            "operation_id": operation_id,
            "result": result,
        }
    )


def _result_payload(
    operation_id: str,
    model: ModelId,
    source_path: Path,
    info: Any,
    started: float,
    segments: list[dict[str, Any]],
    cancelled: bool,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "model": model.model_dump(mode="json"),
        "source_path": str(source_path),
        "language": str(info.language),
        "language_probability": float(info.language_probability),
        "duration_seconds": float(info.duration),
        "elapsed_seconds": time.perf_counter() - started,
        "segments": segments,
        "cancelled": cancelled,
    }
