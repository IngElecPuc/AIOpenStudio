"""SQLite inventory for the shared model library."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from aiopenstudio.core.model_library import (
    ArtifactKind,
    DownloadProvider,
    InstalledArtifact,
)


class ModelLibraryCatalog:
    """Store successful installations and an append-only download event log."""

    def __init__(self, database_path: Path, schema_path: Path) -> None:
        self.database_path = database_path
        self.schema_path = schema_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        with self._connection() as connection:
            connection.executescript(schema)

    def save(self, artifact: InstalledArtifact) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, display_name, kind, provider, family, variant,
                    quantization, source, source_url, revision, runtime_reference,
                    relative_path, license_name, license_url, size_bytes,
                    checksum_sha256, capabilities_json, installed_at, verified_at,
                    metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          datetime('now'))
                ON CONFLICT(artifact_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    kind=excluded.kind,
                    provider=excluded.provider,
                    family=excluded.family,
                    variant=excluded.variant,
                    quantization=excluded.quantization,
                    source=excluded.source,
                    source_url=excluded.source_url,
                    revision=excluded.revision,
                    runtime_reference=excluded.runtime_reference,
                    relative_path=excluded.relative_path,
                    license_name=excluded.license_name,
                    license_url=excluded.license_url,
                    size_bytes=excluded.size_bytes,
                    checksum_sha256=excluded.checksum_sha256,
                    capabilities_json=excluded.capabilities_json,
                    installed_at=excluded.installed_at,
                    verified_at=excluded.verified_at,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    artifact.artifact_id,
                    artifact.display_name,
                    artifact.kind.value,
                    artifact.provider.value,
                    artifact.family,
                    artifact.variant,
                    artifact.quantization,
                    artifact.source,
                    artifact.source_url,
                    artifact.revision,
                    artifact.runtime_reference,
                    artifact.relative_path.as_posix(),
                    artifact.license_name,
                    artifact.license_url,
                    artifact.size_bytes,
                    artifact.checksum_sha256,
                    json.dumps(artifact.capabilities, ensure_ascii=False),
                    artifact.installed_at.isoformat(),
                    artifact.verified_at.isoformat() if artifact.verified_at else None,
                    json.dumps(artifact.metadata, ensure_ascii=False, sort_keys=True),
                ),
            )

    def get(self, artifact_id: str) -> InstalledArtifact | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(self) -> tuple[InstalledArtifact, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts ORDER BY kind, display_name COLLATE NOCASE"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def record_event(
        self,
        run_id: str,
        artifact_id: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        if status not in {"started", "installed", "failed", "skipped"}:
            raise ValueError(f"Unsupported event status: {status}")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO download_events(run_id, artifact_id, status, detail, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (run_id, artifact_id, status, detail),
            )

    def latest_events(self) -> dict[str, tuple[str, str | None]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT artifact_id, status, detail
                FROM download_events AS event
                WHERE event_id = (
                    SELECT MAX(candidate.event_id)
                    FROM download_events AS candidate
                    WHERE candidate.artifact_id = event.artifact_id
                )
                """
            ).fetchall()
        return {row["artifact_id"]: (row["status"], row["detail"]) for row in rows}

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> InstalledArtifact:
        return InstalledArtifact(
            artifact_id=row["artifact_id"],
            display_name=row["display_name"],
            kind=ArtifactKind(row["kind"]),
            provider=DownloadProvider(row["provider"]),
            family=row["family"],
            variant=row["variant"],
            quantization=row["quantization"],
            source=row["source"],
            source_url=row["source_url"],
            revision=row["revision"],
            runtime_reference=row["runtime_reference"],
            relative_path=PurePosixPath(row["relative_path"]),
            license_name=row["license_name"],
            license_url=row["license_url"],
            size_bytes=row["size_bytes"],
            checksum_sha256=row["checksum_sha256"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            installed_at=row["installed_at"],
            verified_at=row["verified_at"],
            metadata=json.loads(row["metadata_json"]),
        )
