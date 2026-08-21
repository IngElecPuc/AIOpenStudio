import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from aiopenstudio.core.contracts import (
    ArtifactRecord,
    ExecutionRecord,
    ExecutionStatus,
    PersistenceConnectionStatus,
    PersistenceMode,
    PostgresConnectionProfile,
    PostgresConnectionResult,
    StoredConfiguration,
)
from aiopenstudio.infrastructure.database import PostgresProfileStore, SQLiteStore
from aiopenstudio.services import PersistenceService


class _MemoryProfileStore:
    def __init__(self, profile: PostgresConnectionProfile) -> None:
        self.profile = profile

    def load(self) -> PostgresConnectionProfile:
        return self.profile

    def save(self, profile: PostgresConnectionProfile) -> None:
        self.profile = profile


class _MemoryCredentials:
    password: str | None = None

    def load(self, _: PostgresConnectionProfile) -> str | None:
        return self.password

    def save(self, _: PostgresConnectionProfile, password: str) -> None:
        self.password = password

    def delete(self, _: PostgresConnectionProfile) -> None:
        self.password = None


class _SecondaryRepository:
    def __init__(self) -> None:
        self.executions: list[ExecutionRecord] = []
        self.configurations: list[StoredConfiguration] = []
        self.disposed = False

    def connect(self, *, create_tables: bool | None = None) -> PostgresConnectionResult:
        return PostgresConnectionResult(
            success=True,
            status=PersistenceConnectionStatus.CONNECTED,
            message="ready",
            schema_revision="test",
        )

    def dispose(self) -> None:
        self.disposed = True

    def save_configuration(self, configuration: StoredConfiguration) -> None:
        self.configurations.append(configuration)

    def get_configuration(self, namespace: str, key: str) -> StoredConfiguration | None:
        return next(
            (
                item
                for item in self.configurations
                if item.namespace == namespace and item.key == key
            ),
            None,
        )

    def save_execution(
        self,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord] = (),
    ) -> None:
        self.executions.append(execution)

    def list_executions(self, limit: int = 100) -> Sequence[ExecutionRecord]:
        return self.executions[:limit]


def test_sqlite_persists_execution_metadata_and_deduplicates_outbox(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.sqlite3")
    store.initialize()
    started_at = datetime.now(UTC)
    running = ExecutionRecord(
        operation_id="operation-1",
        suite="fooocus",
        operation_type="image_generation",
        status=ExecutionStatus.RUNNING,
        runtime="fooocus",
        model_key="fooocus:model",
        started_at=started_at,
        request_metadata={"prompt_sha256": "a" * 64},
    )
    completed = running.model_copy(
        update={
            "status": ExecutionStatus.COMPLETED,
            "finished_at": datetime.now(UTC),
            "result_metadata": {"image_count": 1},
        }
    )
    artifact = ArtifactRecord(
        artifact_id="operation-1-image-1",
        operation_id="operation-1",
        kind="image",
        path=str(tmp_path / "image.png"),
        mime_type="image/png",
        size_bytes=100,
        sha256="b" * 64,
    )

    store.save_execution(running, enqueue_secondary=True)
    store.save_execution(completed, (artifact,), enqueue_secondary=True)

    assert store.list_executions() == [completed]
    assert store.pending_outbox_count() == 1
    pending = store.pending_outbox()[0]
    assert pending.entity_kind == "execution"
    assert pending.payload["execution"]["status"] == "completed"  # type: ignore[index]
    assert len(pending.payload["artifacts"]) == 1  # type: ignore[arg-type]


def test_sqlite_configuration_is_local_and_can_be_queued(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.sqlite3")
    store.initialize()
    configuration = StoredConfiguration(
        namespace="fooocus",
        key="defaults",
        value={"performance": "speed"},
    )

    store.save_configuration(configuration, enqueue_secondary=True)

    assert store.get_configuration("fooocus", "defaults") == configuration
    assert store.pending_outbox_count() == 1


def test_postgres_profile_file_never_contains_password(tmp_path: Path) -> None:
    profile_path = tmp_path / "runtime" / "postgres-profile.json"
    store = PostgresProfileStore(profile_path)
    profile = PostgresConnectionProfile(
        enabled=True,
        host="localhost",
        database="aiopenstudio_test",
        username="desktop_user",
        remember_password=True,
    )

    store.save(profile)

    assert store.load() == profile
    content = profile_path.read_text(encoding="utf-8")
    assert "password" not in content.casefold().replace("remember_password", "")
    assert "postgresql://" not in content


def test_persistence_service_replicates_outbox_and_can_be_disabled(tmp_path: Path) -> None:
    async def scenario() -> None:
        local = SQLiteStore(tmp_path / "memory.sqlite3")
        local.initialize()
        profiles = _MemoryProfileStore(PostgresConnectionProfile())
        credentials = _MemoryCredentials()
        secondary = _SecondaryRepository()
        service = PersistenceService(
            local,
            profiles,
            credentials,
            lambda _profile, _password: secondary,
        )
        profile = PostgresConnectionProfile(
            enabled=True,
            database="empty_database",
            username="desktop_user",
        )
        result = await service.connect(profile, "manual-secret")
        assert result.success
        execution = ExecutionRecord(
            operation_id="replicated-operation",
            suite="llm",
            operation_type="chat",
            status=ExecutionStatus.COMPLETED,
        )
        await service.save_execution(execution)
        assert secondary.executions == [execution]
        assert local.pending_outbox_count() == 0

        await service.disconnect(disable=True)
        state = await service.state()
        assert state.status is PersistenceConnectionStatus.DISABLED
        assert secondary.disposed

    asyncio.run(scenario())


def test_enabled_profile_without_password_starts_disconnected(tmp_path: Path) -> None:
    async def scenario() -> None:
        local = SQLiteStore(tmp_path / "memory.sqlite3")
        local.initialize()
        profiles = _MemoryProfileStore(PostgresConnectionProfile(enabled=True))
        credentials = _MemoryCredentials()
        service = PersistenceService(
            local,
            profiles,
            credentials,
            lambda _profile, _password: _SecondaryRepository(),
        )

        result = await service.reconnect()

        assert result is not None and not result.success
        assert (await service.state()).status is PersistenceConnectionStatus.DISCONNECTED

    asyncio.run(scenario())


def test_postgres_primary_writes_directly_without_duplicating_in_sqlite(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        local = SQLiteStore(tmp_path / "memory.sqlite3")
        local.initialize()
        profile = PostgresConnectionProfile(
            enabled=True,
            mode=PersistenceMode.POSTGRES_PRIMARY,
        )
        profiles = _MemoryProfileStore(profile)
        secondary = _SecondaryRepository()
        service = PersistenceService(
            local,
            profiles,
            _MemoryCredentials(),
            lambda _profile, _password: secondary,
        )
        assert (await service.connect(profile, "manual-secret")).success
        execution = ExecutionRecord(
            operation_id="postgres-primary-operation",
            suite="fooocus",
            operation_type="image_generation",
            status=ExecutionStatus.COMPLETED,
        )

        await service.save_execution(execution)

        assert secondary.executions == [execution]
        assert local.list_executions() == []
        assert await service.list_executions() == [execution]
        assert not (await service.state()).fallback_active

    asyncio.run(scenario())


def test_disabled_postgres_primary_warns_and_uses_durable_sqlite_fallback(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        local = SQLiteStore(tmp_path / "memory.sqlite3")
        local.initialize()
        profile = PostgresConnectionProfile(
            enabled=False,
            mode=PersistenceMode.POSTGRES_PRIMARY,
        )
        profiles = _MemoryProfileStore(profile)
        service = PersistenceService(
            local,
            profiles,
            _MemoryCredentials(),
            lambda _profile, _password: _SecondaryRepository(),
        )

        result = await service.reconnect()
        assert result is not None and not result.success
        assert "fallback" in result.message.casefold()
        execution = ExecutionRecord(
            operation_id="fallback-operation",
            suite="whisper",
            operation_type="transcribe",
            status=ExecutionStatus.COMPLETED,
        )
        await service.save_execution(execution)

        state = await service.state()
        assert state.fallback_active
        assert profiles.profile.mode is PersistenceMode.POSTGRES_PRIMARY
        assert not profiles.profile.enabled
        assert local.list_executions() == [execution]
        assert local.pending_outbox_count() == 1

    asyncio.run(scenario())


def test_persistence_mode_can_be_selected_from_configuration(tmp_path: Path) -> None:
    async def scenario() -> None:
        local = SQLiteStore(tmp_path / "memory.sqlite3")
        local.initialize()
        profiles = _MemoryProfileStore(PostgresConnectionProfile())
        service = PersistenceService(
            local,
            profiles,
            _MemoryCredentials(),
            lambda _profile, _password: _SecondaryRepository(),
        )

        primary = await service.set_mode(PersistenceMode.POSTGRES_PRIMARY)
        assert primary.profile.mode is PersistenceMode.POSTGRES_PRIMARY
        assert primary.fallback_active

        sqlite = await service.set_mode(PersistenceMode.SQLITE_ONLY)
        assert sqlite.profile.mode is PersistenceMode.SQLITE_ONLY
        assert not sqlite.profile.enabled
        assert not sqlite.fallback_active
        assert sqlite.message == "Modo Solo SQLite activo."

    asyncio.run(scenario())
