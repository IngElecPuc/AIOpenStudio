"""Alembic environment driven by an application-owned SQLAlchemy connection."""

from alembic import context

from aiopenstudio.infrastructure.database.sqlalchemy_schema import metadata


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("AIOpenStudio migrations require an application-owned connection.")
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
