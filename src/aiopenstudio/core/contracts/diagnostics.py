"""Backend-neutral diagnostic snapshots and probes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class DiagnosticStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    status: DiagnosticStatus
    detail: str = Field(default="", max_length=4_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class DiagnosticSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    application_version: str
    session_id: str
    environment: str
    items: tuple[DiagnosticItem, ...]


class DiagnosticProbe(Protocol):
    def collect(self) -> Sequence[DiagnosticItem]: ...
