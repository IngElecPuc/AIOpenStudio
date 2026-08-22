"""Whisper use cases, export formats and local input validation."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aiopenstudio.core.contracts import (
    AudioInspector,
    AudioInterval,
    AudioRecorder,
    ComputeDevice,
    ExecutionHistory,
    ExecutionRecord,
    ExecutionStatus,
    ExperimentalDictationChunk,
    ExperimentalDictationEvent,
    ExperimentalDictationEventKind,
    ExperimentalDictationOptions,
    LoadPolicy,
    ModelCatalog,
    ModelDescriptor,
    ModelId,
    ModelState,
    ResidencyPolicy,
    ResourceMonitor,
    RuntimeHealth,
    TranscriptionCorrection,
    TranscriptionDocument,
    TranscriptionEvent,
    TranscriptionEventKind,
    TranscriptionModelCapabilities,
    TranscriptionOptions,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionRuntime,
    TranscriptionSearchHit,
    UnloadTarget,
    VadMode,
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
        audio_inspector: AudioInspector | None = None,
        recordings_dir: Path | None = None,
        max_input_bytes: int = 4 * 1024 * 1024 * 1024,
        execution_history: ExecutionHistory | None = None,
    ) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._residency_policy = residency_policy
        self._resource_monitor = resource_monitor
        self._recorder = recorder
        self._audio_inspector = audio_inspector
        self._recordings_dir = recordings_dir
        self._max_input_bytes = max_input_bytes
        self._execution_history = execution_history
        self._operation_gate = asyncio.Lock()
        self._queue_gate = asyncio.Lock()
        self._queue_states: dict[str, str] = {}
        self._queue_cancellations: set[str] = set()
        self._experimental_children: dict[str, str] = {}
        self._experimental_cancellations: set[str] = set()
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

    @property
    def experimental_dictation_available(self) -> bool:
        return self._audio_inspector is not None

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
            started_at = datetime.now(UTC)
            terminal_recorded = False
            try:
                self._validate_source(request.source_path)
                self._validate_options(request)
                await self._save_execution(request, ExecutionStatus.RUNNING, started_at)
                state = await self._runtime.state(request.model)
                implicit_load = not state.loaded_in_ram and not state.loaded_in_gpu
                if implicit_load:
                    await self.load_model(request.model, load_policy or LoadPolicy())
                elif self._residency_policy is not None:
                    self._residency_policy.model_used(request.model)
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
                    if event.kind in {
                        TranscriptionEventKind.COMPLETED,
                        TranscriptionEventKind.CANCELLED,
                        TranscriptionEventKind.ERROR,
                    }:
                        status = {
                            TranscriptionEventKind.COMPLETED: ExecutionStatus.COMPLETED,
                            TranscriptionEventKind.CANCELLED: ExecutionStatus.CANCELLED,
                            TranscriptionEventKind.ERROR: ExecutionStatus.FAILED,
                        }[event.kind]
                        await self._save_execution(
                            request,
                            status,
                            started_at,
                            result=event.result,
                            error=event.message,
                        )
                        terminal_recorded = True
                    yield event
            except Exception as error:
                if not terminal_recorded:
                    await self._save_execution(
                        request,
                        ExecutionStatus.FAILED,
                        started_at,
                        error=str(error),
                    )
                raise
            finally:
                if self._resource_monitor is not None:
                    await self._resource_monitor.snapshot()

    async def stream_queue(
        self,
        requests: Sequence[TranscriptionRequest],
        *,
        load_policy: LoadPolicy | None = None,
    ) -> AsyncIterator[TranscriptionEvent]:
        """Process audio requests in a single explicit FIFO without batch inference."""
        queued = tuple(requests)
        operation_ids = [request.operation_id for request in queued]
        if len(operation_ids) != len(set(operation_ids)):
            raise RuntimeRequestError("La cola contiene identificadores de operación repetidos.")
        if not queued:
            return

        async with self._queue_gate:
            self._queue_states.update({operation_id: "queued" for operation_id in operation_ids})
            try:
                for request in queued:
                    operation_id = request.operation_id
                    if operation_id in self._queue_cancellations:
                        cancelled_at = datetime.now(UTC)
                        await self._save_execution(
                            request,
                            ExecutionStatus.CANCELLED,
                            cancelled_at,
                        )
                        yield self._queued_cancelled_event(operation_id)
                        continue
                    self._queue_states[operation_id] = "starting"
                    try:
                        async for event in self.stream_transcription(
                            request,
                            load_policy=load_policy,
                        ):
                            self._queue_states[operation_id] = "active"
                            if operation_id in self._queue_cancellations:
                                await self._runtime.cancel(operation_id)
                            yield event
                    except Exception as error:
                        yield TranscriptionEvent(
                            operation_id=operation_id,
                            kind=TranscriptionEventKind.ERROR,
                            message=str(error),
                        )
                    finally:
                        self._queue_states.pop(operation_id, None)
                        self._queue_cancellations.discard(operation_id)
            finally:
                for operation_id in operation_ids:
                    self._queue_states.pop(operation_id, None)
                    self._queue_cancellations.discard(operation_id)

    @staticmethod
    def _queued_cancelled_event(operation_id: str) -> TranscriptionEvent:
        return TranscriptionEvent(
            operation_id=operation_id,
            kind=TranscriptionEventKind.CANCELLED,
            message="La tarea fue retirada de la cola antes de comenzar.",
        )

    async def stream_experimental_dictation(
        self,
        request: TranscriptionRequest,
        options: ExperimentalDictationOptions,
        *,
        load_policy: LoadPolicy | None = None,
    ) -> AsyncIterator[ExperimentalDictationEvent]:
        """Transcribe overlapping windows sequentially; this is not native streaming."""
        if self._audio_inspector is None:
            raise RuntimeRequestError("La inspección de audio experimental no está configurada.")
        if request.options.intervals:
            raise RuntimeRequestError(
                "El dictado experimental no se combina con intervalos manuales."
            )
        duration = await self._audio_inspector.duration_seconds(request.source_path)
        intervals = _dictation_intervals(duration, options)
        cumulative = ""
        self._experimental_children[request.operation_id] = ""
        try:
            for index, interval in enumerate(intervals):
                if request.operation_id in self._experimental_cancellations:
                    yield _experimental_cancelled(request.operation_id, duration, cumulative)
                    return
                child_id = f"{request.operation_id}:chunk:{index}"
                self._experimental_children[request.operation_id] = child_id
                child_options_payload = request.options.model_dump(mode="python")
                child_options_payload.update(
                    {
                        "vad_mode": VadMode.DISABLED,
                        "vad_parameters": None,
                        "intervals": (interval,),
                    }
                )
                child = TranscriptionRequest(
                    operation_id=child_id,
                    model=request.model,
                    source_path=request.source_path,
                    options=TranscriptionOptions.model_validate(child_options_payload),
                )
                yield ExperimentalDictationEvent(
                    operation_id=request.operation_id,
                    kind=ExperimentalDictationEventKind.PROCESSING,
                    cumulative_text=cumulative,
                    duration_seconds=duration,
                    processed_seconds=interval.start_seconds,
                    message=(
                        f"Fragmento {index + 1}/{len(intervals)}: "
                        f"{interval.start_seconds:.1f}–{interval.end_seconds:.1f} s"
                    ),
                )
                result: TranscriptionResult | None = None
                async for event in self.stream_transcription(child, load_policy=load_policy):
                    if event.kind is TranscriptionEventKind.CANCELLED:
                        yield _experimental_cancelled(
                            request.operation_id,
                            duration,
                            cumulative,
                        )
                        return
                    if event.kind is TranscriptionEventKind.ERROR:
                        raise RuntimeRequestError(
                            event.message or "Falló un fragmento del dictado experimental."
                        )
                    if event.kind is TranscriptionEventKind.COMPLETED:
                        result = event.result
                if result is None:
                    raise RuntimeRequestError(
                        "El fragmento terminó sin un resultado de transcripción."
                    )
                cumulative, appended, removed = _merge_dictation_text(
                    cumulative,
                    result.text,
                    options.deduplication_words,
                )
                yield ExperimentalDictationEvent(
                    operation_id=request.operation_id,
                    kind=ExperimentalDictationEventKind.CHUNK,
                    chunk=ExperimentalDictationChunk(
                        index=index,
                        start_seconds=interval.start_seconds,
                        end_seconds=interval.end_seconds,
                        raw_text=result.text,
                        appended_text=appended,
                        cumulative_text=cumulative,
                        removed_prefix_words=removed,
                    ),
                    cumulative_text=cumulative,
                    duration_seconds=duration,
                    processed_seconds=interval.end_seconds,
                )
            yield ExperimentalDictationEvent(
                operation_id=request.operation_id,
                kind=ExperimentalDictationEventKind.COMPLETED,
                cumulative_text=cumulative,
                duration_seconds=duration,
                processed_seconds=duration,
                message="Vista experimental completada.",
            )
        finally:
            self._experimental_children.pop(request.operation_id, None)
            self._experimental_cancellations.discard(request.operation_id)

    async def _save_execution(
        self,
        request: TranscriptionRequest,
        status: ExecutionStatus,
        started_at: datetime,
        *,
        result: TranscriptionResult | None = None,
        error: str | None = None,
    ) -> None:
        if self._execution_history is None:
            return
        source = request.source_path
        request_metadata = {
            "source_name": source.name,
            "source_path_sha256": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
            "source_size_bytes": source.stat().st_size if source.is_file() else None,
            "options": request.options.model_dump(mode="json"),
        }
        result_metadata: dict[str, object] = {}
        if result is not None:
            result_metadata = {
                "source_language": result.source_language,
                "source_language_probability": result.source_language_probability,
                "output_language": result.output_language,
                "task": result.task.value,
                "duration_seconds": result.duration_seconds,
                "duration_after_vad_seconds": result.duration_after_vad_seconds,
                "vad_removed_seconds": result.vad_removed_seconds,
                "elapsed_seconds": result.elapsed_seconds,
                "device": result.device.value if result.device is not None else None,
                "compute_type": result.compute_type,
                "segment_count": len(result.segments),
                "cancelled": result.cancelled,
                "text_sha256": hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
            }
        await self._execution_history.save_execution(
            ExecutionRecord(
                operation_id=request.operation_id,
                suite="whisper",
                operation_type=request.options.task.value,
                status=status,
                runtime=request.model.runtime,
                model_key=request.model.key,
                started_at=started_at,
                finished_at=datetime.now(UTC) if status is not ExecutionStatus.RUNNING else None,
                request_metadata=request_metadata,
                result_metadata=result_metadata,
                error_message=error,
            )
        )

    async def cancel(self, operation_id: str) -> None:
        if operation_id in self._experimental_children:
            self._experimental_cancellations.add(operation_id)
            child = self._experimental_children[operation_id]
            if child:
                await self._runtime.cancel(child)
            return
        state = self._queue_states.get(operation_id)
        if state in {"queued", "starting"}:
            self._queue_cancellations.add(operation_id)
            return
        await self._runtime.cancel(operation_id)

    def descriptor(self, model: ModelId) -> ModelDescriptor | None:
        return self._catalog.get(model)

    def transcription_capabilities(
        self,
        model: ModelId,
    ) -> TranscriptionModelCapabilities:
        descriptor = self._catalog.get(model)
        if descriptor is None:
            raise RuntimeRequestError("El modelo Whisper no está en el catálogo local.")
        payload = descriptor.metadata.get("transcription_capabilities")
        if isinstance(payload, dict):
            return TranscriptionModelCapabilities.model_validate(payload)
        translation = bool(
            {"translation", "translation-to-english"} & descriptor.capabilities
        )
        return TranscriptionModelCapabilities(
            translation_target_codes=("en",) if translation else (),
            supports_translation=translation,
            capabilities_verified=False,
            limitation="El runtime no publicó capacidades detalladas de idioma.",
        )

    @staticmethod
    def create_operation_id() -> str:
        return str(uuid4())

    @staticmethod
    def estimated_vram_bytes(descriptor: ModelDescriptor | None) -> int | None:
        if descriptor is None or descriptor.size_bytes is None:
            return None
        return max(descriptor.size_bytes * 2, descriptor.size_bytes + 512 * 1024 * 1024)

    @staticmethod
    def create_document(result: TranscriptionResult) -> TranscriptionDocument:
        return TranscriptionDocument(original=result)

    @staticmethod
    def correct_segment(
        document: TranscriptionDocument,
        segment_index: int,
        text: str,
    ) -> TranscriptionDocument:
        replacement = TranscriptionCorrection(segment_index=segment_index, text=text)
        corrections = tuple(
            correction
            for correction in document.corrections
            if correction.segment_index != segment_index
        ) + (replacement,)
        return document.model_copy(update={"corrections": corrections})

    @staticmethod
    def discard_correction(
        document: TranscriptionDocument,
        segment_index: int,
    ) -> TranscriptionDocument:
        corrections = tuple(
            correction
            for correction in document.corrections
            if correction.segment_index != segment_index
        )
        return document.model_copy(update={"corrections": corrections})

    @staticmethod
    def search(
        document: TranscriptionDocument,
        query: str,
        *,
        include_words: bool = False,
    ) -> tuple[TranscriptionSearchHit, ...]:
        needle = query.strip().casefold()
        result = document.corrected_result
        corrected_indexes = {
            correction.segment_index for correction in document.corrections
        }
        hits: list[TranscriptionSearchHit] = []
        for segment in result.segments:
            if not needle or needle in segment.text.casefold():
                hits.append(
                    TranscriptionSearchHit(
                        segment_index=segment.index,
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        text=segment.text,
                        corrected=segment.index in corrected_indexes,
                    )
                )
            if include_words and segment.index not in corrected_indexes:
                hits.extend(
                    TranscriptionSearchHit(
                        segment_index=segment.index,
                        word_index=word_index,
                        start_seconds=word.start_seconds,
                        end_seconds=word.end_seconds,
                        text=word.text,
                        corrected=False,
                    )
                    for word_index, word in enumerate(segment.words)
                    if not needle or needle in word.text.casefold()
                )
        return tuple(hits)

    def export(
        self,
        result: TranscriptionResult | TranscriptionDocument,
        destination: Path,
    ) -> Path:
        suffix = destination.suffix.casefold()
        if suffix not in {".txt", ".json", ".srt", ".vtt", ".csv", ".tsv"}:
            raise ValueError(
                "Formato de exportación no compatible: usa TXT, JSON, SRT, VTT, CSV o TSV."
            )
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        document = (
            result
            if isinstance(result, TranscriptionDocument)
            else self.create_document(result)
        )
        rendered = document.corrected_result
        if suffix == ".txt":
            content = rendered.text + "\n"
        elif suffix == ".json":
            content = json.dumps(
                {
                    "schema_version": 1,
                    "original": document.original.model_dump(mode="json"),
                    "corrections": [
                        correction.model_dump(mode="json")
                        for correction in document.corrections
                    ],
                    "rendered": rendered.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        elif suffix == ".srt":
            content = _subtitle(rendered, webvtt=False)
        elif suffix == ".vtt":
            content = "WEBVTT\n\n" + _subtitle(rendered, webvtt=True)
        else:
            content = _tabular(document, delimiter="\t" if suffix == ".tsv" else ",")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(destination)
        return destination

    def _validate_source(self, source_path: Path) -> None:
        if not source_path.is_file():
            raise RuntimeRequestError("El archivo de audio seleccionado no existe.")
        if source_path.stat().st_size > self._max_input_bytes:
            raise RuntimeRequestError("El archivo supera el límite local configurado.")

    def _validate_options(self, request: TranscriptionRequest) -> None:
        capabilities = self.transcription_capabilities(request.model)
        options = request.options
        if (
            options.source_language is not None
            and capabilities.source_language_codes
            and options.source_language not in capabilities.source_language_codes
        ):
            raise RuntimeRequestError(
                f"El modelo no admite {options.source_language!r} como idioma de entrada."
            )
        if options.source_language is None and not capabilities.supports_language_detection:
            if capabilities.source_language_codes != ("en",):
                raise RuntimeRequestError("Este modelo requiere seleccionar el idioma de entrada.")
        if options.task.value == "translate" and not capabilities.supports_translation:
            detail = capabilities.limitation or "El modelo no admite traducción."
            raise RuntimeRequestError(detail)
        if options.word_timestamps and not capabilities.supports_word_timestamps:
            raise RuntimeRequestError("El modelo no admite timestamps por palabra.")
        if options.vad_mode.value != "disabled" and not capabilities.supports_vad:
            raise RuntimeRequestError("El runtime no admite VAD para este modelo.")
        if options.intervals and not capabilities.supports_intervals:
            raise RuntimeRequestError("El runtime no admite intervalos de audio.")
        if options.prompt.hotwords and not capabilities.supports_hotwords:
            raise RuntimeRequestError("El runtime no admite hotwords para este modelo.")


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


def _tabular(document: TranscriptionDocument, *, delimiter: str) -> str:
    stream = io.StringIO(newline="")
    fields = (
        "segment_index",
        "segment_start_seconds",
        "segment_end_seconds",
        "word_index",
        "word_start_seconds",
        "word_end_seconds",
        "text",
        "probability",
        "average_log_probability",
        "no_speech_probability",
        "compression_ratio",
        "temperature",
        "corrected",
    )
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    corrected_indexes = {
        correction.segment_index for correction in document.corrections
    }
    for segment in document.corrected_result.segments:
        base: dict[str, object] = {
            "segment_index": segment.index,
            "segment_start_seconds": segment.start_seconds,
            "segment_end_seconds": segment.end_seconds,
            "average_log_probability": segment.average_log_probability,
            "no_speech_probability": segment.no_speech_probability,
            "compression_ratio": segment.compression_ratio,
            "temperature": segment.temperature,
            "corrected": segment.index in corrected_indexes,
        }
        if segment.index in corrected_indexes or not segment.words:
            writer.writerow({**base, "text": segment.text.strip()})
            continue
        for word_index, word in enumerate(segment.words):
            writer.writerow(
                {
                    **base,
                    "word_index": word_index,
                    "word_start_seconds": word.start_seconds,
                    "word_end_seconds": word.end_seconds,
                    "text": word.text.strip(),
                    "probability": word.probability,
                }
            )
    return stream.getvalue()


def _timestamp(seconds: float, separator: str) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{millis:03d}"


def _dictation_intervals(
    duration_seconds: float,
    options: ExperimentalDictationOptions,
) -> tuple[AudioInterval, ...]:
    intervals: list[AudioInterval] = []
    step = options.chunk_seconds - options.overlap_seconds
    start = 0.0
    while start < duration_seconds:
        end = min(start + options.chunk_seconds, duration_seconds)
        intervals.append(AudioInterval(start_seconds=start, end_seconds=end))
        if end >= duration_seconds:
            break
        start += step
    return tuple(intervals)


def _merge_dictation_text(
    cumulative: str,
    fragment: str,
    maximum_overlap_words: int,
) -> tuple[str, str, int]:
    previous_words = cumulative.split()
    fragment_words = fragment.split()
    limit = min(maximum_overlap_words, len(previous_words), len(fragment_words))
    removed = 0
    for count in range(limit, 0, -1):
        previous = [_normalized_word(word) for word in previous_words[-count:]]
        incoming = [_normalized_word(word) for word in fragment_words[:count]]
        if all(previous) and previous == incoming:
            removed = count
            break
    appended = " ".join(fragment_words[removed:]).strip()
    merged = " ".join(part for part in (cumulative.strip(), appended) if part)
    return merged, appended, removed


def _normalized_word(word: str) -> str:
    return re.sub(r"[^\w]+", "", word.casefold(), flags=re.UNICODE)


def _experimental_cancelled(
    operation_id: str,
    duration_seconds: float,
    cumulative_text: str,
) -> ExperimentalDictationEvent:
    return ExperimentalDictationEvent(
        operation_id=operation_id,
        kind=ExperimentalDictationEventKind.CANCELLED,
        cumulative_text=cumulative_text,
        duration_seconds=duration_seconds,
        message="Vista experimental cancelada; el texto parcial no se guarda automáticamente.",
    )
