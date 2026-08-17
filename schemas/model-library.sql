PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS library_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO library_metadata(key, value, updated_at)
VALUES ('schema_version', '1', datetime('now'))
ON CONFLICT(key) DO UPDATE SET
    value = excluded.value,
    updated_at = excluded.updated_at;

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('llm', 'speech', 'image', 'embedding')),
    provider TEXT NOT NULL CHECK (provider IN ('ollama', 'huggingface', 'http_file')),
    family TEXT NOT NULL,
    variant TEXT,
    quantization TEXT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    revision TEXT,
    runtime_reference TEXT,
    relative_path TEXT NOT NULL CHECK (relative_path NOT LIKE '/%'),
    license_name TEXT NOT NULL,
    license_url TEXT,
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    checksum_sha256 TEXT,
    capabilities_json TEXT NOT NULL,
    installed_at TEXT NOT NULL,
    verified_at TEXT,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind);
CREATE INDEX IF NOT EXISTS idx_artifacts_provider ON artifacts(provider);
CREATE INDEX IF NOT EXISTS idx_artifacts_family ON artifacts(family);

CREATE TABLE IF NOT EXISTS download_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started', 'installed', 'failed', 'skipped')),
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_download_events_run ON download_events(run_id, event_id);
CREATE INDEX IF NOT EXISTS idx_download_events_artifact
ON download_events(artifact_id, event_id);

PRAGMA user_version = 1;
