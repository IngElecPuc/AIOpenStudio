"""Create optional secondary persistence tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260821_secondary_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_configurations",
        sa.Column("namespace", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("namespace", "key"),
    )
    op.create_table(
        "executions",
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("suite", sa.String(length=50), nullable=False),
        sa.Column("operation_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("runtime", sa.String(length=100)),
        sa.Column("model_key", sa.String(length=300)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("request_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index("idx_executions_started", "executions", [sa.text("started_at DESC")])
    op.create_index("idx_executions_suite_status", "executions", ["suite", "status"])
    op.create_table(
        "execution_artifacts",
        sa.Column("artifact_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=200)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.String(length=64)),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["executions.operation_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index(
        "idx_execution_artifacts_operation", "execution_artifacts", ["operation_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_execution_artifacts_operation", table_name="execution_artifacts")
    op.drop_table("execution_artifacts")
    op.drop_index("idx_executions_suite_status", table_name="executions")
    op.drop_index("idx_executions_started", table_name="executions")
    op.drop_table("executions")
    op.drop_table("stored_configurations")
