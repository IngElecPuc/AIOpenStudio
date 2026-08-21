"""Backend-neutral contracts for local and secondary persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


def persistence_utc_now() -> datetime:
    return datetime.now(UTC)


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PersistenceConnectionStatus(StrEnum):
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    CHECKING = "checking"
    CONNECTED = "connected"
    ERROR = "error"


class PersistenceMode(StrEnum):
    SQLITE_ONLY = "sqlite_only"
    SQLITE_REPLICATED = "sqlite_replicated"
    POSTGRES_PRIMARY = "postgres_primary"


class PostgresSslMode(StrEnum):
    DISABLE = "disable"
    PREFER = "prefer"
    REQUIRE = "require"


class StoredConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    namespace: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    value: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=persistence_utc_now)


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    suite: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    operation_type: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    status: ExecutionStatus
    runtime: str | None = Field(default=None, max_length=100)
    model_key: str | None = Field(default=None, max_length=300)
    started_at: datetime = Field(default_factory=persistence_utc_now)
    finished_at: datetime | None = None
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    result_metadata: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = Field(default=None, max_length=4_000)


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    kind: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    path: str = Field(min_length=1, max_length=4_096)
    mime_type: str | None = Field(default=None, max_length=200)
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=persistence_utc_now)


class PostgresConnectionProfile(BaseModel):
    """Persistable connection details; passwords never belong in this model."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    mode: PersistenceMode = PersistenceMode.SQLITE_REPLICATED
    host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65_535)
    database: str = Field(default="aiopenstudio", min_length=1, max_length=63)
    username: str = Field(default="aiopenstudio", min_length=1, max_length=63)
    ssl_mode: PostgresSslMode = PostgresSslMode.PREFER
    connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    auto_create_tables: bool = True
    remember_password: bool = False
    synchronize_existing: bool = False

    @field_validator("host", "database", "username")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character in normalized for character in "\r\n\x00"):
            raise ValueError("connection fields cannot contain control characters")
        return normalized


class PostgresConnectionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    status: PersistenceConnectionStatus
    message: str
    latency_ms: float | None = Field(default=None, ge=0)
    server_version: str | None = None
    database: str | None = None
    username: str | None = None
    schema_revision: str | None = None


class PersistenceState(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: PostgresConnectionProfile
    status: PersistenceConnectionStatus
    message: str = ""
    pending_operations: int = Field(default=0, ge=0)
    fallback_active: bool = False


class PersistenceOutboxEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    entity_kind: str
    entity_key: str
    payload: dict[str, Any]
    attempts: int = Field(ge=0)


class PersistenceRepository(Protocol):
    def save_configuration(self, configuration: StoredConfiguration) -> None: ...

    def get_configuration(self, namespace: str, key: str) -> StoredConfiguration | None: ...

    def save_execution(
        self,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord] = (),
    ) -> None: ...

    def list_executions(self, limit: int = 100) -> Sequence[ExecutionRecord]: ...


class LocalPersistenceStore(PersistenceRepository, Protocol):
    def save_configuration(
        self,
        configuration: StoredConfiguration,
        *,
        enqueue_secondary: bool = False,
    ) -> None: ...

    def save_execution(
        self,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord] = (),
        *,
        enqueue_secondary: bool = False,
    ) -> None: ...

    def pending_outbox(self, limit: int = 100) -> Sequence[PersistenceOutboxEntry]: ...

    def pending_outbox_count(self) -> int: ...

    def mark_outbox_synced(self, event_id: str) -> None: ...

    def mark_outbox_failed(self, event_id: str, error: str) -> None: ...

    def queue_existing_for_secondary(self) -> int: ...


class ConnectionProfileStore(Protocol):
    def load(self) -> PostgresConnectionProfile: ...

    def save(self, profile: PostgresConnectionProfile) -> None: ...


class CredentialStore(Protocol):
    def load(self, profile: PostgresConnectionProfile) -> str | None: ...

    def save(self, profile: PostgresConnectionProfile, password: str) -> None: ...

    def delete(self, profile: PostgresConnectionProfile) -> None: ...


class SecondaryPersistenceRepository(PersistenceRepository, Protocol):
    def connect(self, *, create_tables: bool | None = None) -> PostgresConnectionResult: ...

    def dispose(self) -> None: ...


class ExecutionHistory(Protocol):
    async def save_execution(
        self,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord] = (),
    ) -> None: ...
