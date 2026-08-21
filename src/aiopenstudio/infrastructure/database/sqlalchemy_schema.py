"""SQLAlchemy metadata shared by PostgreSQL repositories and Alembic."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
json_type = JSON().with_variant(JSONB, "postgresql")

stored_configurations = Table(
    "stored_configurations",
    metadata,
    Column("namespace", String(100), primary_key=True),
    Column("key", String(100), primary_key=True),
    Column("value", json_type, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

executions = Table(
    "executions",
    metadata,
    Column("operation_id", String(128), primary_key=True),
    Column("suite", String(50), nullable=False),
    Column("operation_type", String(80), nullable=False),
    Column("status", String(20), nullable=False),
    Column("runtime", String(100)),
    Column("model_key", String(300)),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("request_metadata", json_type, nullable=False),
    Column("result_metadata", json_type, nullable=False),
    Column("error_message", Text),
)
Index("idx_executions_started", executions.c.started_at.desc())
Index("idx_executions_suite_status", executions.c.suite, executions.c.status)

execution_artifacts = Table(
    "execution_artifacts",
    metadata,
    Column("artifact_id", String(128), primary_key=True),
    Column(
        "operation_id",
        String(128),
        ForeignKey("executions.operation_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String(50), nullable=False),
    Column("path", Text, nullable=False),
    Column("mime_type", String(200)),
    Column("size_bytes", BigInteger),
    Column("sha256", String(64)),
    Column("metadata", json_type, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("idx_execution_artifacts_operation", execution_artifacts.c.operation_id)
