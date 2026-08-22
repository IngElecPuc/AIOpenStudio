from __future__ import annotations

import asyncio
import gc
import json
import time
import tkinter as tk
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aiopenstudio.core.contracts import (
    AudioInterval,
    ExperimentalDictationEvent,
    ExperimentalDictationEventKind,
    ExperimentalDictationOptions,
    ModelDescriptor,
    ModelId,
    TranscriptionDecodingOptions,
    TranscriptionEvent,
    TranscriptionEventKind,
    TranscriptionModelCapabilities,
    TranscriptionOptions,
    TranscriptionPromptOptions,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionTask,
    TranscriptionWord,
    VadMode,
    VadParameters,
)
from aiopenstudio.core.errors import RuntimeRequestError
from aiopenstudio.infrastructure.audio import PyAVAudioInspector
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.infrastructure.runtimes.whisper.runtime import (
    FasterWhisperRuntime,
    _result_payload,
    _runtime_transcribe_arguments,
)
from aiopenstudio.services import TranscriptionService
from aiopenstudio.services.transcription import (
    _dictation_intervals,
    _merge_dictation_text,
)
from aiopenstudio.ui.async_runner import AsyncLoopRunner
from aiopenstudio.ui.tabs.whisper import (
    WhisperTab,
    _format_seconds,
    _parse_intervals,
    _parse_temperatures,
)

from .test_transcription import FakeTranscriptionRuntime


def _write_model(root: Path, name: str, *, num_languages: int) -> Path:
    model_root = root / name
    model_root.mkdir(parents=True)
    (model_root / "config.json").write_text(
        json.dumps({"num_languages": num_languages}),
        encoding="utf-8",
    )
    (model_root / "model.bin").write_bytes(b"model")
    (model_root / "tokenizer.json").write_text("{}", encoding="utf-8")
    return model_root


def test_multilingual_model_publishes_source_languages_and_english_target(
    tmp_path: Path,
) -> None:
    _write_model(tmp_path, "faster-whisper-small", num_languages=100)

    descriptor = asyncio.run(FasterWhisperRuntime(tmp_path).list_models())[0]
    capabilities = TranscriptionModelCapabilities.model_validate(
        descriptor.metadata["transcription_capabilities"]
    )

    assert len(capabilities.source_language_codes) == 100
    assert {"en", "es", "zh", "yue", "haw"} <= set(
        capabilities.source_language_codes
    )
    assert capabilities.translation_target_codes == ("en",)
    assert capabilities.supports_translation is True
    assert "translation-to-english" in descriptor.capabilities


@pytest.mark.parametrize(
    ("name", "num_languages", "expected_sources", "expected_limitation"),
    [
        ("faster-whisper-small.en", 1, ("en",), "sólo para audio en inglés"),
        ("faster-whisper-turbo", 100, None, "no fue entrenado para traducción"),
    ],
)
def test_model_variant_restricts_translation(
    tmp_path: Path,
    name: str,
    num_languages: int,
    expected_sources: tuple[str, ...] | None,
    expected_limitation: str,
) -> None:
    _write_model(tmp_path, name, num_languages=num_languages)

    descriptor = asyncio.run(FasterWhisperRuntime(tmp_path).list_models())[0]
    capabilities = TranscriptionModelCapabilities.model_validate(
        descriptor.metadata["transcription_capabilities"]
    )

    if expected_sources is not None:
        assert capabilities.source_language_codes == expected_sources
    assert capabilities.translation_target_codes == ()
    assert capabilities.supports_translation is False
    assert expected_limitation in (capabilities.limitation or "")
    assert "translation-to-english" not in descriptor.capabilities


def test_options_keep_source_and_output_language_semantics_separate() -> None:
    transcribe = TranscriptionOptions(language="ES")
    translate = TranscriptionOptions(
        source_language="es",
        task=TranscriptionTask.TRANSLATE,
    )

    assert transcribe.source_language == "es"
    assert transcribe.expected_output_language == "es"
    assert translate.source_language == "es"
    assert translate.expected_output_language == "en"


def test_options_reject_incompatible_combinations() -> None:
    with pytest.raises(ValidationError, match="prefix y hotwords"):
        TranscriptionPromptOptions(prefix="respuesta", hotwords="AIOpenStudio")
    with pytest.raises(ValidationError, match="requiere desactivar VAD"):
        TranscriptionOptions(intervals=(AudioInterval(start_seconds=0, end_seconds=1),))
    with pytest.raises(ValidationError, match="requiere timestamps por palabra"):
        TranscriptionOptions(
            decoding=TranscriptionDecodingOptions(hallucination_silence_seconds=2)
        )


def test_runtime_arguments_only_apply_explicit_advanced_overrides() -> None:
    options = TranscriptionOptions(
        source_language="es",
        task=TranscriptionTask.TRANSLATE,
        word_timestamps=True,
        vad_mode=VadMode.CUSTOM,
        vad_parameters=VadParameters(
            threshold=0.6,
            minimum_silence_ms=750,
        ),
        prompt=TranscriptionPromptOptions(
            initial_prompt="Charla sobre AIOpenStudio.",
            hotwords="AIOpenStudio, CTranslate2",
        ),
        decoding=TranscriptionDecodingOptions(
            beam_size=7,
            temperatures=(0.0, 0.4),
            condition_on_previous_text=False,
        ),
    )

    arguments = _runtime_transcribe_arguments(options)

    assert arguments == {
        "language": "es",
        "task": "translate",
        "word_timestamps": True,
        "vad_filter": True,
        "vad_parameters": {"threshold": 0.6, "min_silence_duration_ms": 750},
        "initial_prompt": "Charla sobre AIOpenStudio.",
        "hotwords": "AIOpenStudio, CTranslate2",
        "beam_size": 7,
        "temperature": [0.0, 0.4],
        "condition_on_previous_text": False,
    }


def test_result_distinguishes_detected_source_from_translated_output(tmp_path: Path) -> None:
    requested = TranscriptionOptions(task=TranscriptionTask.TRANSLATE)
    backend_options = SimpleNamespace(
        beam_size=5,
        best_of=5,
        patience=1.0,
        length_penalty=1.0,
        repetition_penalty=1.0,
        no_repeat_ngram_size=0,
        temperatures=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        condition_on_previous_text=True,
        prompt_reset_on_temperature=0.5,
        suppress_blank=True,
        suppress_tokens=[-1],
        max_new_tokens=None,
        hallucination_silence_threshold=None,
        prepend_punctuations="'\"¿([{-",
        append_punctuations="'\".,!?)]}",
        word_timestamps=False,
        initial_prompt=None,
        prefix=None,
        hotwords=None,
    )
    info = SimpleNamespace(
        language="es",
        language_probability=0.97,
        all_language_probs=[("es", 0.97), ("en", 0.02)],
        duration=12.0,
        duration_after_vad=10.5,
        transcription_options=backend_options,
    )

    payload = _result_payload(
        "operation",
        ModelId(runtime="faster-whisper", name="small"),
        tmp_path / "audio.wav",
        info,
        0.0,
        [],
        False,
        requested,
        "cuda",
        "int8_float16",
    )
    result = TranscriptionResult.model_validate(payload)

    assert result.source_language == "es"
    assert result.output_language == "en"
    assert result.task is TranscriptionTask.TRANSLATE
    assert result.device is not None and result.device.value == "gpu"
    assert result.compute_type == "int8_float16"
    assert result.vad_removed_seconds == pytest.approx(1.5)
    assert result.requested_options == requested
    assert result.applied_options is not None
    assert result.applied_options.decoding.beam_size == 5


def test_service_rejects_language_or_translation_not_supported_by_model(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = FakeTranscriptionRuntime()
        descriptor = ModelDescriptor(
            id=runtime.descriptor.id,
            display_name=runtime.descriptor.display_name,
            installed=True,
            metadata={
                "transcription_capabilities": TranscriptionModelCapabilities(
                    source_language_codes=("en",),
                    translation_target_codes=(),
                    supports_language_detection=False,
                    limitation="Modelo sólo inglés.",
                ).model_dump(mode="json")
            },
        )
        runtime.descriptors = (descriptor,)
        runtime.descriptor = descriptor
        catalog = SQLiteStore(tmp_path / "catalog.sqlite3")
        catalog.initialize()
        service = TranscriptionService(runtime, catalog)
        await service.refresh_models()
        source = tmp_path / "audio.wav"
        source.write_bytes(b"RIFF")

        for options in (
            TranscriptionOptions(source_language="es"),
            TranscriptionOptions(task=TranscriptionTask.TRANSLATE),
        ):
            request = TranscriptionRequest(
                operation_id="unsupported",
                model=descriptor.id,
                source_path=source,
                options=options,
            )
            with pytest.raises(RuntimeRequestError):
                _ = [event async for event in service.stream_transcription(request)]

    asyncio.run(scenario())


def test_document_keeps_original_and_exports_corrected_detail(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "catalog.sqlite3")
    store.initialize()
    service = TranscriptionService(FakeTranscriptionRuntime(), store)
    segment = TranscriptionSegment(
        index=0,
        start_seconds=1.25,
        end_seconds=2.75,
        text=" texto orijinal",
        words=(
            TranscriptionWord(
                start_seconds=1.25,
                end_seconds=1.8,
                text=" texto",
                probability=0.91,
            ),
            TranscriptionWord(
                start_seconds=1.8,
                end_seconds=2.75,
                text=" orijinal",
                probability=0.62,
            ),
        ),
        average_log_probability=-0.4,
        no_speech_probability=0.05,
        compression_ratio=1.2,
        temperature=0.0,
    )
    result = TranscriptionResult(
        operation_id="edit",
        model=ModelId(runtime="faster-whisper", name="small"),
        source_path=tmp_path / "audio.wav",
        elapsed_seconds=1,
        segments=(segment,),
    )

    document = service.correct_segment(
        service.create_document(result),
        0,
        " texto original",
    )

    assert document.original.text == "texto orijinal"
    assert document.corrected_result.text == "texto original"
    assert service.search(document, "original")[0].corrected is True
    assert service.search(document, "orijinal") == ()

    for suffix in (".txt", ".srt", ".vtt", ".csv", ".tsv", ".json"):
        destination = service.export(document, tmp_path / f"corrected{suffix}")
        content = destination.read_text(encoding="utf-8")
        assert "original" in content
    detailed = json.loads((tmp_path / "corrected.json").read_text(encoding="utf-8"))
    assert detailed["original"]["segments"][0]["text"] == " texto orijinal"
    assert detailed["rendered"]["segments"][0]["text"] == " texto original"
    assert detailed["corrections"] == [
        {"segment_index": 0, "text": " texto original"}
    ]


def test_audio_queue_is_fifo_and_localizes_invalid_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "queue.sqlite3")
        store.initialize()
        runtime = FakeTranscriptionRuntime()
        service = TranscriptionService(runtime, store)
        await service.refresh_models()
        valid = tmp_path / "valid.wav"
        valid.write_bytes(b"RIFF")
        requests = (
            TranscriptionRequest(
                operation_id="missing",
                model=runtime.descriptor.id,
                source_path=tmp_path / "missing.wav",
            ),
            TranscriptionRequest(
                operation_id="valid",
                model=runtime.descriptor.id,
                source_path=valid,
            ),
        )

        events = [event async for event in service.stream_queue(requests)]

        terminal = [
            (event.operation_id, event.kind)
            for event in events
            if event.kind
            in {
                TranscriptionEventKind.COMPLETED,
                TranscriptionEventKind.CANCELLED,
                TranscriptionEventKind.ERROR,
            }
        ]
        assert terminal == [
            ("missing", TranscriptionEventKind.ERROR),
            ("valid", TranscriptionEventKind.COMPLETED),
        ]

    asyncio.run(scenario())


def test_ui_parses_intervals_and_fallback_temperatures() -> None:
    intervals = _parse_intervals("0-30, 01:15-02:00.5")

    assert [(item.start_seconds, item.end_seconds) for item in intervals] == [
        (0.0, 30.0),
        (75.0, 120.5),
    ]
    assert _parse_temperatures("0, 0.2, 0.6") == (0.0, 0.2, 0.6)
    assert _format_seconds(3723.25) == "01:02:03.250"


@pytest.mark.parametrize("value", ["10", "10-", "1:75-2:00", "30-20"])
def test_ui_rejects_invalid_intervals(value: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        _parse_intervals(value)


def test_queue_can_cancel_a_waiting_audio_without_stopping_active_audio(
    tmp_path: Path,
) -> None:
    class SlowRuntime(FakeTranscriptionRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def transcribe(
            self,
            request: TranscriptionRequest,
        ) -> AsyncIterator[TranscriptionEvent]:
            self.started.set()
            yield TranscriptionEvent(
                operation_id=request.operation_id,
                kind=TranscriptionEventKind.STARTED,
            )
            await self.release.wait()
            result = TranscriptionResult(
                operation_id=request.operation_id,
                model=request.model,
                source_path=request.source_path,
                elapsed_seconds=0.1,
            )
            yield TranscriptionEvent(
                operation_id=request.operation_id,
                kind=TranscriptionEventKind.COMPLETED,
                result=result,
            )

    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "cancel-queue.sqlite3")
        store.initialize()
        runtime = SlowRuntime()
        service = TranscriptionService(runtime, store)
        await service.refresh_models()
        sources = (tmp_path / "first.wav", tmp_path / "second.wav")
        for source in sources:
            source.write_bytes(b"RIFF")
        requests = tuple(
            TranscriptionRequest(
                operation_id=operation_id,
                model=runtime.descriptor.id,
                source_path=source,
            )
            for operation_id, source in zip(("first", "second"), sources, strict=True)
        )

        async def collect() -> list[TranscriptionEvent]:
            return [event async for event in service.stream_queue(requests)]

        collection = asyncio.create_task(collect())
        await runtime.started.wait()
        await service.cancel("second")
        runtime.release.set()
        events = await collection

        assert [
            (event.operation_id, event.kind)
            for event in events
            if event.kind
            in {
                TranscriptionEventKind.COMPLETED,
                TranscriptionEventKind.CANCELLED,
            }
        ] == [
            ("first", TranscriptionEventKind.COMPLETED),
            ("second", TranscriptionEventKind.CANCELLED),
        ]
        assert runtime.cancelled == []

    asyncio.run(scenario())


def test_whisper_tab_builds_with_progressive_detail_hidden_by_default(
    tmp_path: Path,
) -> None:
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk no está disponible: {error}")
    root.withdraw()
    runner = AsyncLoopRunner()
    runner.start()
    store = SQLiteStore(tmp_path / "ui.sqlite3")
    store.initialize()
    service = TranscriptionService(FakeTranscriptionRuntime(), store)
    tab: WhisperTab | None = None
    try:
        tab = WhisperTab(root, service, runner)
        tab.pack(fill=tk.BOTH, expand=True)
        time.sleep(0.05)
        root.update()

        assert tab._word_timestamps.get() is False
        assert tab._vad_mode.get() == VadMode.AUTOMATIC.value
        assert tab._transcript.cget("state") == tk.DISABLED
        assert tab._task.get() == TranscriptionTask.TRANSCRIBE.value
    finally:
        for _ in range(3):
            time.sleep(0.05)
            root.update()
        runner.stop()
        if tab is not None:
            tab.destroy()
            tab = None
        gc.collect()
        root.destroy()


def test_experimental_dictation_builds_overlapping_windows_and_deduplicates() -> None:
    options = ExperimentalDictationOptions(
        chunk_seconds=30,
        overlap_seconds=5,
        deduplication_words=4,
    )

    intervals = _dictation_intervals(70, options)
    merged, appended, removed = _merge_dictation_text(
        "Una prueba de AIOpenStudio continúa",
        "AIOpenStudio continúa con otra frase.",
        4,
    )

    assert [(item.start_seconds, item.end_seconds) for item in intervals] == [
        (0.0, 30.0),
        (25.0, 55.0),
        (50.0, 70.0),
    ]
    assert merged == "Una prueba de AIOpenStudio continúa con otra frase."
    assert appended == "con otra frase."
    assert removed == 2


def test_experimental_dictation_stream_is_sequential_and_marks_completion(
    tmp_path: Path,
) -> None:
    class FixedInspector:
        async def duration_seconds(self, source: Path) -> float:
            del source
            return 55.0

    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "experimental.sqlite3")
        store.initialize()
        runtime = FakeTranscriptionRuntime()
        service = TranscriptionService(
            runtime,
            store,
            audio_inspector=FixedInspector(),
        )
        await service.refresh_models()
        source = tmp_path / "audio.wav"
        source.write_bytes(b"RIFF")
        request = TranscriptionRequest(
            operation_id="preview",
            model=runtime.descriptor.id,
            source_path=source,
        )

        events = [
            event
            async for event in service.stream_experimental_dictation(
                request,
                ExperimentalDictationOptions(chunk_seconds=30, overlap_seconds=5),
            )
        ]

        chunks = [event.chunk for event in events if event.chunk is not None]
        assert [(chunk.start_seconds, chunk.end_seconds) for chunk in chunks] == [
            (0.0, 30.0),
            (25.0, 55.0),
        ]
        assert chunks[1].removed_prefix_words == 2
        assert events[-1].kind is ExperimentalDictationEventKind.COMPLETED
        assert events[-1].cumulative_text == "Hola mundo"

    asyncio.run(scenario())


def test_pyav_inspector_reads_wav_duration_without_decoding_a_model(tmp_path: Path) -> None:
    source = tmp_path / "duration.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * 24_000)

    duration = asyncio.run(PyAVAudioInspector().duration_seconds(source))

    assert duration == pytest.approx(1.5, abs=0.01)


def test_experimental_dictation_cancels_the_active_child_operation(tmp_path: Path) -> None:
    class FixedInspector:
        async def duration_seconds(self, source: Path) -> float:
            del source
            return 20.0

    class CancellableRuntime(FakeTranscriptionRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancel_requested = asyncio.Event()

        async def transcribe(
            self,
            request: TranscriptionRequest,
        ) -> AsyncIterator[TranscriptionEvent]:
            self.started.set()
            yield TranscriptionEvent(
                operation_id=request.operation_id,
                kind=TranscriptionEventKind.STARTED,
            )
            await self.cancel_requested.wait()
            yield TranscriptionEvent(
                operation_id=request.operation_id,
                kind=TranscriptionEventKind.CANCELLED,
            )

        async def cancel(self, operation_id: str) -> None:
            self.cancelled.append(operation_id)
            self.cancel_requested.set()

    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "experimental-cancel.sqlite3")
        store.initialize()
        runtime = CancellableRuntime()
        service = TranscriptionService(runtime, store, audio_inspector=FixedInspector())
        await service.refresh_models()
        source = tmp_path / "audio.wav"
        source.write_bytes(b"RIFF")
        request = TranscriptionRequest(
            operation_id="preview",
            model=runtime.descriptor.id,
            source_path=source,
        )

        async def collect() -> list[ExperimentalDictationEvent]:
            return [
                event
                async for event in service.stream_experimental_dictation(
                    request,
                    ExperimentalDictationOptions(),
                )
            ]

        collection = asyncio.create_task(collect())
        await runtime.started.wait()
        await service.cancel("preview")
        events = await collection

        assert runtime.cancelled == ["preview:chunk:0"]
        assert events[-1].kind is ExperimentalDictationEventKind.CANCELLED

    asyncio.run(scenario())
