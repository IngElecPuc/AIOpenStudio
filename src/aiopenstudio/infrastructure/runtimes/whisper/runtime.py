"""Process-isolated faster-whisper adapter with no implicit model downloads."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import multiprocessing
import queue
import time
from collections import deque
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
    TranscriptionModelCapabilities,
    TranscriptionOptions,
    TranscriptionProgress,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionStage,
    TranscriptionTask,
    UnloadTarget,
    VadMode,
)
from aiopenstudio.core.errors import (
    ModelNotInstalledError,
    ResourceExhaustedError,
    RuntimeRequestError,
    RuntimeUnavailableError,
)

_RUNTIME_NAME = "faster-whisper"
_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")
_MULTILINGUAL_SOURCE_LANGUAGE_CODES = (
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs",
    "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi",
    "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht", "hu", "hy",
    "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb",
    "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt",
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru",
    "sa", "sd", "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw",
    "ta", "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz", "vi",
    "yi", "yo", "zh", "yue",
)


class FasterWhisperRuntime:
    """Own one faster-whisper model in a disposable Windows worker process."""

    def __init__(
        self,
        models_root: Path,
        *,
        cancel_grace_seconds: float = 2.0,
        restart_limit: int = 3,
        restart_window_seconds: float = 300.0,
    ) -> None:
        self._models_root = models_root.resolve()
        self._cancel_grace_seconds = cancel_grace_seconds
        self._restart_limit = restart_limit
        self._restart_window_seconds = restart_window_seconds
        self._restart_times: deque[float] = deque()
        self._logger = logging.getLogger("aiopenstudio.runtime.whisper")
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
            transcription_capabilities = _model_transcription_capabilities(
                config_path,
                variant,
            )
            capabilities = {
                "speech-to-text",
                "timestamps",
                "word-timestamps",
                "vad",
                "intervals",
                "hotwords",
            }
            if transcription_capabilities.supports_translation:
                capabilities.add("translation-to-english")
            descriptors.append(
                ModelDescriptor(
                    id=ModelId(runtime=self.name, name=name, variant=variant),
                    display_name=f"Whisper {variant}",
                    capabilities=frozenset(capabilities),
                    weights_path=model_path,
                    size_bytes=size_bytes,
                    installed=True,
                    metadata={
                        "backend": "faster-whisper",
                        "local_only": True,
                        "transcription_capabilities": (
                            transcription_capabilities.model_dump(mode="json")
                        ),
                    },
                )
            )
        return tuple(sorted(descriptors, key=lambda item: item.size_bytes or 0))

    def _descriptor_for(self, model: ModelId) -> ModelDescriptor | None:
        return next((item for item in self._discover_models() if item.id.key == model.key), None)

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        if self._process is not None:
            exit_code = self._process.exitcode
            self._register_restart()
            self._logger.warning(
                "runtime.worker_restarting",
                extra={
                    "component": "whisper",
                    "runtime": self.name,
                    "previous_exit_code": exit_code,
                    "restart_count": len(self._restart_times),
                },
            )
            self._stop_blocking()
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

    def _register_restart(self) -> None:
        now = time.monotonic()
        while self._restart_times and now - self._restart_times[0] > self._restart_window_seconds:
            self._restart_times.popleft()
        if len(self._restart_times) >= self._restart_limit:
            raise RuntimeUnavailableError(
                "El worker Whisper superó el límite de reinicios; revisa Diagnósticos."
            )
        self._restart_times.append(now)

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
        if process is not None and not process.is_alive():
            process.join(timeout=0)
            try:
                process.close()
            except (OSError, ValueError):
                pass
        for channel in (self._commands, self._responses):
            if channel is not None:
                try:
                    channel.close()
                except (OSError, ValueError):
                    pass
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


def _model_transcription_capabilities(
    config_path: Path,
    variant: str,
) -> TranscriptionModelCapabilities:
    """Derive language semantics from the installed CTranslate2 model config."""
    normalized_variant = variant.casefold()
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raw_config = {}

    raw_count = raw_config.get("num_languages")
    language_count = raw_count if isinstance(raw_count, int) else None
    english_only = language_count == 1 or normalized_variant.endswith(".en")
    turbo = "turbo" in normalized_variant
    config_verified = language_count is not None

    if english_only:
        return TranscriptionModelCapabilities(
            source_language_codes=("en",),
            translation_target_codes=(),
            supports_language_detection=False,
            supports_translation=False,
            capabilities_verified=config_verified,
            limitation=(
                "El modelo instalado es sólo para audio en inglés; la traducción no aplica."
            ),
        )

    limitation = None
    if turbo:
        limitation = (
            "Whisper turbo no fue entrenado para traducción; sólo se habilita transcripción."
        )
    elif not config_verified:
        limitation = (
            "No fue posible verificar num_languages en config.json; se usa el catálogo "
            "multilingüe oficial de Whisper."
        )
    return TranscriptionModelCapabilities(
        source_language_codes=_MULTILINGUAL_SOURCE_LANGUAGE_CODES,
        translation_target_codes=() if turbo else ("en",),
        supports_language_detection=True,
        supports_translation=not turbo,
        capabilities_verified=config_verified,
        limitation=limitation,
    )


def _worker_main(commands: Any, responses: Any, cancel_event: Any) -> None:
    model: Any | None = None
    model_path: str | None = None
    device = "cpu"
    compute_type = "int8"
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
                _worker_transcribe(
                    model,
                    command,
                    responses,
                    cancel_event,
                    device=device,
                    compute_type=compute_type,
                )
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
    *,
    device: str,
    compute_type: str,
) -> None:
    operation_id = str(command["operation_id"])
    source_path = Path(str(command["source_path"]))
    options = TranscriptionOptions.model_validate(command["options"])
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
        **_runtime_transcribe_arguments(options),
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
            "average_log_probability": _optional_float(segment, "avg_logprob"),
            "no_speech_probability": _optional_float(segment, "no_speech_prob"),
            "compression_ratio": _optional_float(segment, "compression_ratio"),
            "temperature": _optional_float(segment, "temperature"),
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
                operation_id,
                model_id,
                source_path,
                info,
                started,
                serialized_segments,
                True,
                options,
                device,
                compute_type,
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
        operation_id,
        model_id,
        source_path,
        info,
        started,
        serialized_segments,
        False,
        options,
        device,
        compute_type,
    )
    responses.put(
        {
            "kind": TranscriptionEventKind.COMPLETED.value,
            "operation_id": operation_id,
            "result": result,
        }
    )


def _runtime_transcribe_arguments(options: TranscriptionOptions) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "language": options.source_language,
        "task": options.task.value,
        "word_timestamps": options.word_timestamps,
        "vad_filter": options.vad_mode is not VadMode.DISABLED,
    }
    if options.vad_mode is VadMode.CUSTOM and options.vad_parameters is not None:
        arguments["vad_parameters"] = options.vad_parameters.runtime_options()
    if options.intervals:
        arguments["clip_timestamps"] = [
            value
            for interval in options.intervals
            for value in (interval.start_seconds, interval.end_seconds)
        ]

    for name in ("initial_prompt", "prefix", "hotwords"):
        value = getattr(options.prompt, name)
        if value is not None:
            arguments[name] = value

    decoding_mapping = {
        "beam_size": "beam_size",
        "best_of": "best_of",
        "patience": "patience",
        "length_penalty": "length_penalty",
        "repetition_penalty": "repetition_penalty",
        "no_repeat_ngram_size": "no_repeat_ngram_size",
        "temperatures": "temperature",
        "compression_ratio_threshold": "compression_ratio_threshold",
        "log_probability_threshold": "log_prob_threshold",
        "no_speech_threshold": "no_speech_threshold",
        "condition_on_previous_text": "condition_on_previous_text",
        "prompt_reset_temperature": "prompt_reset_on_temperature",
        "suppress_blank": "suppress_blank",
        "suppress_tokens": "suppress_tokens",
        "max_new_tokens": "max_new_tokens",
        "hallucination_silence_seconds": "hallucination_silence_threshold",
        "prepend_punctuations": "prepend_punctuations",
        "append_punctuations": "append_punctuations",
        "language_detection_threshold": "language_detection_threshold",
        "language_detection_segments": "language_detection_segments",
    }
    for contract_name, runtime_name in decoding_mapping.items():
        value = getattr(options.decoding, contract_name)
        if value is not None:
            arguments[runtime_name] = list(value) if isinstance(value, tuple) else value
    return arguments


def _applied_options(info: Any, requested: TranscriptionOptions) -> TranscriptionOptions:
    backend_options = getattr(info, "transcription_options", None)
    if backend_options is None:
        return requested.model_copy(
            update={"source_language": str(getattr(info, "language", "")) or None}
        )

    def option(name: str, fallback: Any = None) -> Any:
        return getattr(backend_options, name, fallback)

    applied = {
        "source_language": str(getattr(info, "language", "")) or None,
        "task": option("task", requested.task.value),
        "word_timestamps": bool(option("word_timestamps", requested.word_timestamps)),
        "vad_mode": requested.vad_mode,
        "vad_parameters": requested.vad_parameters,
        "intervals": requested.intervals,
        "prompt": {
            "initial_prompt": option("initial_prompt"),
            "prefix": option("prefix"),
            "hotwords": option("hotwords"),
        },
        "decoding": {
            "beam_size": option("beam_size"),
            "best_of": option("best_of"),
            "patience": option("patience"),
            "length_penalty": option("length_penalty"),
            "repetition_penalty": option("repetition_penalty"),
            "no_repeat_ngram_size": option("no_repeat_ngram_size"),
            "temperatures": option("temperatures"),
            "compression_ratio_threshold": option("compression_ratio_threshold"),
            "log_probability_threshold": option("log_prob_threshold"),
            "no_speech_threshold": option("no_speech_threshold"),
            "condition_on_previous_text": option("condition_on_previous_text"),
            "prompt_reset_temperature": option("prompt_reset_on_temperature"),
            "suppress_blank": option("suppress_blank"),
            "suppress_tokens": option("suppress_tokens"),
            "max_new_tokens": option("max_new_tokens"),
            "hallucination_silence_seconds": option("hallucination_silence_threshold"),
            "prepend_punctuations": option("prepend_punctuations"),
            "append_punctuations": option("append_punctuations"),
            "language_detection_threshold": option("language_detection_threshold"),
            "language_detection_segments": option("language_detection_segments"),
        },
    }
    return TranscriptionOptions.model_validate(applied)


def _optional_float(value: Any, attribute: str) -> float | None:
    raw = getattr(value, attribute, None)
    return float(raw) if raw is not None else None


def _result_payload(
    operation_id: str,
    model: ModelId,
    source_path: Path,
    info: Any,
    started: float,
    segments: list[dict[str, Any]],
    cancelled: bool,
    requested_options: TranscriptionOptions,
    device: str,
    compute_type: str,
) -> dict[str, Any]:
    source_language = str(info.language)
    task = requested_options.task
    probabilities = tuple(
        {"code": str(code), "probability": float(probability)}
        for code, probability in (getattr(info, "all_language_probs", None) or ())
    )
    return {
        "operation_id": operation_id,
        "model": model.model_dump(mode="json"),
        "source_path": str(source_path),
        "source_language": source_language,
        "source_language_probability": float(info.language_probability),
        "output_language": "en" if task is TranscriptionTask.TRANSLATE else source_language,
        "task": task.value,
        "language_probabilities": probabilities,
        "duration_seconds": float(info.duration),
        "duration_after_vad_seconds": _optional_float(info, "duration_after_vad"),
        "elapsed_seconds": time.perf_counter() - started,
        "device": "gpu" if device == "cuda" else "cpu",
        "compute_type": compute_type,
        "requested_options": requested_options.model_dump(mode="json"),
        "applied_options": _applied_options(info, requested_options).model_dump(mode="json"),
        "segments": segments,
        "cancelled": cancelled,
    }
