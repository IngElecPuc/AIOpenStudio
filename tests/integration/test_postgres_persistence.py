import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aiopenstudio.core.contracts import (
    ExecutionRecord,
    ExecutionStatus,
    PostgresConnectionProfile,
    PostgresSslMode,
    StoredConfiguration,
)
from aiopenstudio.infrastructure.database import PostgresMigrationManager, PostgresRepository


@pytest.mark.postgres_integration
def test_postgres_migrations_and_repository_are_opt_in() -> None:
    if os.getenv("AIOPENSTUDIO_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL integration is opt-in")
    password = os.environ["AIOPENSTUDIO_DATABASE_PASSWORD"]
    profile = PostgresConnectionProfile(
        enabled=True,
        host=os.getenv("AIOPENSTUDIO_POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("AIOPENSTUDIO_POSTGRES_PORT", "5432")),
        database=os.environ["AIOPENSTUDIO_POSTGRES_DATABASE"],
        username=os.environ["AIOPENSTUDIO_POSTGRES_USERNAME"],
        ssl_mode=PostgresSslMode(os.getenv("AIOPENSTUDIO_POSTGRES_SSL_MODE", "prefer")),
        auto_create_tables=True,
    )
    migrations = PostgresMigrationManager(
        Path(__file__).parents[2]
        / "src/aiopenstudio/infrastructure/database/migrations"
    )
    repository = PostgresRepository(profile, password, migrations=migrations)
    result = repository.connect()
    assert result.success, result.message
    assert result.schema_revision == "20260821_secondary_persistence"
    operation_id = str(uuid4())
    configuration = StoredConfiguration(
        namespace="integration",
        key=operation_id,
        value={"verified": True},
    )
    execution = ExecutionRecord(
        operation_id=operation_id,
        suite="integration",
        operation_type="postgres_validation",
        status=ExecutionStatus.COMPLETED,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    try:
        repository.save_configuration(configuration)
        repository.save_execution(execution)
        assert repository.get_configuration("integration", operation_id) == configuration
        assert any(item.operation_id == operation_id for item in repository.list_executions())
    finally:
        repository.dispose()
