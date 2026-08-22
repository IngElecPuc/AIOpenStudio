"""Backend-neutral contracts for local speech transcription and translation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .models import ComputeDevice, ModelId
from .runtime import ModelLifecycleRuntime


class TranscriptionTask(StrEnum):
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"


class VadMode(StrEnum):
    """Visible speech filtering policy, independent of Silero implementation types."""

    DISABLED = "disabled"
    AUTOMATIC = "automatic"
    CUSTOM = "custom"


class TranscriptionStage(StrEnum):
    PREPARING = "preparing"
    LOADING = "loading"
    DECODING = "decoding"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TranscriptionEventKind(StrEnum):
    STARTED = "started"
    PROGRESS = "progress"
    SEGMENT = "segment"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class AudioInterval(BaseModel):
    """Half-open interval selected from an audio source."""

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> AudioInterval:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("El final del intervalo debe ser posterior al inicio.")
        return self


class ExperimentalDictationOptions(BaseModel):
    """Window policy for an explicitly non-streaming dictation preview."""

    chunk_seconds: float = Field(default=30, ge=5, le=300)
    overlap_seconds: float = Field(default=3, ge=0, le=60)
    deduplication_words: int = Field(default=12, ge=0, le=100)

    @model_validator(mode="after")
    def validate_overlap(self) -> ExperimentalDictationOptions:
        if self.overlap_seconds >= self.chunk_seconds:
            raise ValueError("El solapamiento debe ser menor que cada fragmento.")
        return self


class VadParameters(BaseModel):
    """Portable subset of the Silero VAD options fixed by faster-whisper 1.2.1."""

    threshold: float | None = Field(default=None, gt=0, lt=1)
    negative_threshold: float | None = Field(default=None, gt=0, lt=1)
    minimum_speech_ms: int | None = Field(default=None, ge=0, le=60_000)
    maximum_speech_seconds: float | None = Field(default=None, gt=0, le=86_400)
    minimum_silence_ms: int | None = Field(default=None, ge=0, le=60_000)
    speech_padding_ms: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_thresholds(self) -> VadParameters:
        if (
            self.threshold is not None
            and self.negative_threshold is not None
            and self.negative_threshold >= self.threshold
        ):
            raise ValueError("El umbral de silencio debe ser menor que el umbral de voz.")
        return self

    def runtime_options(self) -> dict[str, int | float]:
        mapping = {
            "threshold": self.threshold,
            "neg_threshold": self.negative_threshold,
            "min_speech_duration_ms": self.minimum_speech_ms,
            "max_speech_duration_s": self.maximum_speech_seconds,
            "min_silence_duration_ms": self.minimum_silence_ms,
            "speech_pad_ms": self.speech_padding_ms,
        }
        return {name: value for name, value in mapping.items() if value is not None}


class TranscriptionPromptOptions(BaseModel):
    initial_prompt: str | None = Field(default=None, max_length=20_000)
    prefix: str | None = Field(default=None, max_length=10_000)
    hotwords: str | None = Field(default=None, max_length=10_000)

    @field_validator("initial_prompt", "prefix", "hotwords")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_prompt_sources(self) -> TranscriptionPromptOptions:
        if self.prefix is not None and self.hotwords is not None:
            raise ValueError("prefix y hotwords son incompatibles en faster-whisper.")
        return self


class TranscriptionDecodingOptions(BaseModel):
    """Optional overrides; ``None`` means preserve the backend default."""

    beam_size: int | None = Field(default=None, ge=1, le=100)
    best_of: int | None = Field(default=None, ge=1, le=100)
    patience: float | None = Field(default=None, gt=0, le=10)
    length_penalty: float | None = Field(default=None, gt=0, le=10)
    repetition_penalty: float | None = Field(default=None, gt=0, le=10)
    no_repeat_ngram_size: int | None = Field(default=None, ge=0, le=100)
    temperatures: tuple[float, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    compression_ratio_threshold: float | None = Field(default=None, gt=0, le=100)
    log_probability_threshold: float | None = Field(default=None, ge=-100, le=0)
    no_speech_threshold: float | None = Field(default=None, ge=0, le=1)
    condition_on_previous_text: bool | None = None
    prompt_reset_temperature: float | None = Field(default=None, ge=0, le=2)
    suppress_blank: bool | None = None
    suppress_tokens: tuple[int, ...] | None = None
    max_new_tokens: int | None = Field(default=None, ge=1, le=448)
    hallucination_silence_seconds: float | None = Field(default=None, gt=0, le=3_600)
    prepend_punctuations: str | None = Field(default=None, max_length=256)
    append_punctuations: str | None = Field(default=None, max_length=256)
    language_detection_threshold: float | None = Field(default=None, ge=0, le=1)
    language_detection_segments: int | None = Field(default=None, ge=1, le=100)

    @field_validator("temperatures")
    @classmethod
    def validate_temperatures(
        cls,
        value: tuple[float, ...] | None,
    ) -> tuple[float, ...] | None:
        if value is None:
            return None
        if any(temperature < 0 or temperature > 2 for temperature in value):
            raise ValueError("Las temperaturas deben estar entre 0 y 2.")
        if tuple(sorted(value)) != value or len(set(value)) != len(value):
            raise ValueError("Las temperaturas deben ser únicas y estar en orden ascendente.")
        return value


class TranscriptionOptions(BaseModel):
    """Complete request while keeping source and output language semantics separate."""

    model_config = ConfigDict(populate_by_name=True)

    source_language: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source_language", "language"),
        min_length=2,
        max_length=16,
        pattern=r"^[a-z]{2,3}$",
    )
    task: TranscriptionTask = TranscriptionTask.TRANSCRIBE
    word_timestamps: bool = False
    vad_mode: VadMode = VadMode.AUTOMATIC
    vad_parameters: VadParameters | None = None
    intervals: tuple[AudioInterval, ...] = ()
    prompt: TranscriptionPromptOptions = Field(default_factory=TranscriptionPromptOptions)
    decoding: TranscriptionDecodingOptions = Field(
        default_factory=TranscriptionDecodingOptions
    )

    @field_validator("source_language", mode="before")
    @classmethod
    def normalize_language(cls, value: object) -> object:
        return value.casefold() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_combination(self) -> TranscriptionOptions:
        if self.vad_mode is VadMode.CUSTOM and self.vad_parameters is None:
            raise ValueError("El VAD personalizado requiere parámetros.")
        if self.vad_mode is not VadMode.CUSTOM and self.vad_parameters is not None:
            raise ValueError("Los parámetros VAD sólo se usan en modo personalizado.")
        if self.intervals and self.vad_mode is not VadMode.DISABLED:
            raise ValueError("Seleccionar intervalos requiere desactivar VAD.")
        previous_end = 0.0
        for interval in self.intervals:
            if interval.start_seconds < previous_end:
                raise ValueError("Los intervalos deben estar ordenados y no solaparse.")
            previous_end = interval.end_seconds
        if (
            self.decoding.hallucination_silence_seconds is not None
            and not self.word_timestamps
        ):
            raise ValueError(
                "La detección de silencios alucinados requiere timestamps por palabra."
            )
        return self

    @property
    def expected_output_language(self) -> str | None:
        if self.task is TranscriptionTask.TRANSLATE:
            return "en"
        return self.source_language


class TranscriptionModelCapabilities(BaseModel):
    source_language_codes: tuple[str, ...] = ()
    translation_target_codes: tuple[str, ...] = ()
    supports_language_detection: bool = True
    supports_translation: bool = False
    supports_word_timestamps: bool = True
    supports_vad: bool = True
    supports_intervals: bool = True
    supports_hotwords: bool = True
    supports_batch_inference: bool = False
    capabilities_verified: bool = False
    limitation: str | None = None


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str = Field(min_length=1)
    model: ModelId
    source_path: Path
    options: TranscriptionOptions = Field(default_factory=TranscriptionOptions)


class TranscriptionWord(BaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str
    probability: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_timing(self) -> TranscriptionWord:
        if self.end_seconds < self.start_seconds:
            raise ValueError("word end must be greater than or equal to its start")
        return self


class TranscriptionSegment(BaseModel):
    index: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str
    words: tuple[TranscriptionWord, ...] = ()
    average_log_probability: float | None = None
    no_speech_probability: float | None = Field(default=None, ge=0, le=1)
    compression_ratio: float | None = Field(default=None, ge=0)
    temperature: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_timing(self) -> TranscriptionSegment:
        if self.end_seconds < self.start_seconds:
            raise ValueError("segment end must be greater than or equal to its start")
        return self


class TranscriptionProgress(BaseModel):
    stage: TranscriptionStage
    processed_seconds: float = Field(default=0, ge=0)
    total_seconds: float | None = Field(default=None, ge=0)
    fraction: float | None = Field(default=None, ge=0, le=1)
    detail: str | None = None


class LanguageProbability(BaseModel):
    code: str = Field(min_length=2, max_length=16)
    probability: float = Field(ge=0, le=1)


class TranscriptionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation_id: str
    model: ModelId
    source_path: Path
    source_language: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source_language", "language"),
    )
    source_language_probability: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "source_language_probability",
            "language_probability",
        ),
        ge=0,
        le=1,
    )
    output_language: str | None = None
    task: TranscriptionTask = TranscriptionTask.TRANSCRIBE
    language_probabilities: tuple[LanguageProbability, ...] = ()
    duration_seconds: float | None = Field(default=None, ge=0)
    duration_after_vad_seconds: float | None = Field(default=None, ge=0)
    elapsed_seconds: float = Field(ge=0)
    device: ComputeDevice | None = None
    compute_type: str | None = None
    requested_options: TranscriptionOptions | None = None
    applied_options: TranscriptionOptions | None = None
    segments: tuple[TranscriptionSegment, ...] = ()
    cancelled: bool = False

    @property
    def language(self) -> str | None:
        """Compatibility alias for callers written before source/output separation."""
        return self.source_language

    @property
    def language_probability(self) -> float | None:
        return self.source_language_probability

    @property
    def vad_removed_seconds(self) -> float | None:
        options = self.applied_options
        if (
            self.duration_seconds is None
            or self.duration_after_vad_seconds is None
            or options is None
            or options.vad_mode is VadMode.DISABLED
            or options.intervals
        ):
            return None
        return max(self.duration_seconds - self.duration_after_vad_seconds, 0.0)

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments).strip()


class TranscriptionCorrection(BaseModel):
    """User-authored replacement that never mutates the backend result."""

    segment_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=100_000)

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("La corrección no puede quedar vacía.")
        return value


class TranscriptionDocument(BaseModel):
    """Original result plus an auditable, non-destructive correction layer."""

    original: TranscriptionResult
    corrections: tuple[TranscriptionCorrection, ...] = ()

    @model_validator(mode="after")
    def validate_corrections(self) -> TranscriptionDocument:
        available = {segment.index for segment in self.original.segments}
        indexes = [correction.segment_index for correction in self.corrections]
        if len(indexes) != len(set(indexes)):
            raise ValueError("Cada segmento admite una única corrección vigente.")
        missing = sorted(set(indexes) - available)
        if missing:
            raise ValueError(f"Las correcciones apuntan a segmentos inexistentes: {missing}.")
        return self

    @property
    def corrected_result(self) -> TranscriptionResult:
        replacements = {
            correction.segment_index: correction.text for correction in self.corrections
        }
        segments = tuple(
            segment.model_copy(update={"text": replacements.get(segment.index, segment.text)})
            for segment in self.original.segments
        )
        return self.original.model_copy(update={"segments": segments})

    def correction_for(self, segment_index: int) -> TranscriptionCorrection | None:
        return next(
            (
                correction
                for correction in self.corrections
                if correction.segment_index == segment_index
            ),
            None,
        )


class TranscriptionSearchHit(BaseModel):
    segment_index: int = Field(ge=0)
    word_index: int | None = Field(default=None, ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str
    corrected: bool = False


class ExperimentalDictationChunk(BaseModel):
    index: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    raw_text: str
    appended_text: str
    cumulative_text: str
    removed_prefix_words: int = Field(default=0, ge=0)


class ExperimentalDictationEventKind(StrEnum):
    PROCESSING = "processing"
    CHUNK = "chunk"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ExperimentalDictationEvent(BaseModel):
    operation_id: str
    kind: ExperimentalDictationEventKind
    chunk: ExperimentalDictationChunk | None = None
    cumulative_text: str = ""
    duration_seconds: float = Field(ge=0)
    processed_seconds: float = Field(default=0, ge=0)
    message: str | None = None


class TranscriptionEvent(BaseModel):
    operation_id: str
    kind: TranscriptionEventKind
    progress: TranscriptionProgress | None = None
    segment: TranscriptionSegment | None = None
    result: TranscriptionResult | None = None
    message: str | None = None


class TranscriptionRuntime(ModelLifecycleRuntime, Protocol):
    """Runtime capable of streaming timestamped transcription events."""

    def transcribe(self, request: TranscriptionRequest) -> AsyncIterator[TranscriptionEvent]: ...

    async def cancel(self, operation_id: str) -> None: ...


class AudioRecorder(Protocol):
    @property
    def available(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self, destination: Path) -> Path: ...

    async def cancel(self) -> None: ...


class AudioInspector(Protocol):
    """Read-only duration inspection for a local audio container."""

    async def duration_seconds(self, source: Path) -> float: ...
