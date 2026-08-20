"""Backend-neutral contracts for local speech transcription."""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ModelId
from .runtime import ModelLifecycleRuntime


class TranscriptionTask(StrEnum):
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"


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


class TranscriptionOptions(BaseModel):
    language: str | None = Field(default=None, min_length=2, max_length=16)
    task: TranscriptionTask = TranscriptionTask.TRANSCRIBE
    beam_size: int = Field(default=5, ge=1, le=10)
    vad_filter: bool = True
    word_timestamps: bool = False


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


class TranscriptionResult(BaseModel):
    operation_id: str
    model: ModelId
    source_path: Path
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    duration_seconds: float | None = Field(default=None, ge=0)
    elapsed_seconds: float = Field(ge=0)
    segments: tuple[TranscriptionSegment, ...] = ()
    cancelled: bool = False

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments).strip()


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
