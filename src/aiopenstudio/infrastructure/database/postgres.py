"""Optional PostgreSQL repository and explicit Alembic schema management."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import URL, Engine, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError

from aiopenstudio.core.contracts import (
    ArtifactRecord,
    ExecutionRecord,
    PersistenceConnectionStatus,
    PostgresConnectionProfile,
    PostgresConnectionResult,
    StoredConfiguration,
)

from .sqlalchemy_schema import execution_artifacts, executions, stored_configurations

POSTGRES_SCHEMA_REVISION = "20260821_secondary_persistence"


class PostgresMigrationManager:
    """Run packaged Alembic migrations without persisting a credential-bearing URL."""

    def __init__(self, script_location: Path) -> None:
        self._script_location = script_location

    def upgrade(self, engine: Engine) -> None:
        try:
            from alembic import command
            from alembic.config import Config
        except ImportError as error:
            raise RuntimeError(
                "Alembic no está instalado; instala el extra opcional 'postgres'."
            ) from error
        configuration = Config()
        configuration.set_main_option("script_location", str(self._script_location))
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")


class PostgresRepository:
    """SQLAlchemy 2.x adapter for the optional secondary PostgreSQL database."""

    def __init__(
        self,
        profile: PostgresConnectionProfile,
        password: str,
        *,
        migrations: PostgresMigrationManager,
    ) -> None:
        self.profile = profile
        self._password = password
        self._migrations = migrations
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise RuntimeError("La conexión PostgreSQL no está activa.")
        return self._engine

    def connect(self, *, create_tables: bool | None = None) -> PostgresConnectionResult:
        self.dispose()
        started = time.perf_counter()
        engine: Engine | None = None
        try:
            engine = create_engine(
                self._url(),
                pool_pre_ping=True,
                pool_size=2,
                max_overflow=2,
                connect_args={"connect_timeout": self.profile.connect_timeout_seconds},
            )
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT version(), current_database(), current_user"
                    )
                ).one()
            should_create = (
                self.profile.auto_create_tables if create_tables is None else create_tables
            )
            if should_create:
                self._migrations.upgrade(engine)
            revision = self._schema_revision(engine)
            if revision != POSTGRES_SCHEMA_REVISION:
                engine.dispose()
                return PostgresConnectionResult(
                    success=False,
                    status=PersistenceConnectionStatus.ERROR,
                    message=(
                        "La conexión funciona, pero el esquema de AIOpenStudio no está listo. "
                        "Habilita la autocreación de tablas o aplica las migraciones Alembic."
                    ),
                    latency_ms=(time.perf_counter() - started) * 1_000,
                    server_version=str(row[0]),
                    database=str(row[1]),
                    username=str(row[2]),
                    schema_revision=revision,
                )
            self._engine = engine
            return PostgresConnectionResult(
                success=True,
                status=PersistenceConnectionStatus.CONNECTED,
                message="Conexión PostgreSQL verificada.",
                latency_ms=(time.perf_counter() - started) * 1_000,
                server_version=str(row[0]),
                database=str(row[1]),
                username=str(row[2]),
                schema_revision=revision,
            )
        except Exception as error:
            try:
                if engine is not None:
                    engine.dispose()
            except Exception:
                pass
            return PostgresConnectionResult(
                success=False,
                status=PersistenceConnectionStatus.ERROR,
                message=f"No se pudo conectar a PostgreSQL: {self._safe_error(error)}",
                latency_ms=(time.perf_counter() - started) * 1_000,
            )

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def save_configuration(self, configuration: StoredConfiguration) -> None:
        statement = postgres_insert(stored_configurations).values(
            namespace=configuration.namespace,
            key=configuration.key,
            value=configuration.value,
            schema_version=configuration.schema_version,
            updated_at=configuration.updated_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[stored_configurations.c.namespace, stored_configurations.c.key],
            set_={
                "value": statement.excluded.value,
                "schema_version": statement.excluded.schema_version,
                "updated_at": statement.excluded.updated_at,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def get_configuration(self, namespace: str, key: str) -> StoredConfiguration | None:
        statement = select(stored_configurations).where(
            stored_configurations.c.namespace == namespace,
            stored_configurations.c.key == key,
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return self._configuration_from_mapping(row) if row is not None else None

    def save_execution(
        self,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord] = (),
    ) -> None:
        statement = postgres_insert(executions).values(
            operation_id=execution.operation_id,
            suite=execution.suite,
            operation_type=execution.operation_type,
            status=execution.status.value,
            runtime=execution.runtime,
            model_key=execution.model_key,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            request_metadata=execution.request_metadata,
            result_metadata=execution.result_metadata,
            error_message=execution.error_message,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[executions.c.operation_id],
            set_={
                "status": statement.excluded.status,
                "runtime": statement.excluded.runtime,
                "model_key": statement.excluded.model_key,
                "finished_at": statement.excluded.finished_at,
                "request_metadata": statement.excluded.request_metadata,
                "result_metadata": statement.excluded.result_metadata,
                "error_message": statement.excluded.error_message,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)
            for artifact in artifacts:
                artifact_statement = postgres_insert(execution_artifacts).values(
                    artifact_id=artifact.artifact_id,
                    operation_id=artifact.operation_id,
                    kind=artifact.kind,
                    path=artifact.path,
                    mime_type=artifact.mime_type,
                    size_bytes=artifact.size_bytes,
                    sha256=artifact.sha256,
                    metadata=artifact.metadata,
                    created_at=artifact.created_at,
                )
                artifact_statement = artifact_statement.on_conflict_do_update(
                    index_elements=[execution_artifacts.c.artifact_id],
                    set_={
                        "kind": artifact_statement.excluded.kind,
                        "path": artifact_statement.excluded.path,
                        "mime_type": artifact_statement.excluded.mime_type,
                        "size_bytes": artifact_statement.excluded.size_bytes,
                        "sha256": artifact_statement.excluded.sha256,
                        "metadata": artifact_statement.excluded.metadata,
                    },
                )
                connection.execute(artifact_statement)

    def list_executions(self, limit: int = 100) -> Sequence[ExecutionRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("Execution limit must be between 1 and 500")
        statement = select(executions).order_by(executions.c.started_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._execution_from_mapping(row) for row in rows]

    def _url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.profile.username,
            password=self._password,
            host=self.profile.host,
            port=self.profile.port,
            database=self.profile.database,
            query={"sslmode": self.profile.ssl_mode.value},
        )

    @staticmethod
    def _schema_revision(engine: Engine) -> str | None:
        try:
            with engine.connect() as connection:
                row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
        except SQLAlchemyError:
            return None
        return str(row[0]) if row else None

    def _safe_error(self, error: BaseException) -> str:
        message = str(error).replace(self._password, "***") if self._password else str(error)
        return " ".join(message.split())[:600]

    @staticmethod
    def _configuration_from_mapping(row: Any) -> StoredConfiguration:
        return StoredConfiguration(
            namespace=row["namespace"],
            key=row["key"],
            value=row["value"],
            schema_version=row["schema_version"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _execution_from_mapping(row: Any) -> ExecutionRecord:
        return ExecutionRecord(
            operation_id=row["operation_id"],
            suite=row["suite"],
            operation_type=row["operation_type"],
            status=row["status"],
            runtime=row["runtime"],
            model_key=row["model_key"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            request_metadata=row["request_metadata"],
            result_metadata=row["result_metadata"],
            error_message=row["error_message"],
        )
