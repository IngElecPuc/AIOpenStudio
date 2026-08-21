import asyncio
import json
import zipfile
from pathlib import Path

from aiopenstudio.core.contracts import (
    DiagnosticItem,
    DiagnosticStatus,
    PersistenceConnectionStatus,
    PersistenceState,
    PostgresConnectionProfile,
    ProcessState,
    RuntimeHealth,
)
from aiopenstudio.services.diagnostics import DiagnosticsService


class _Probe:
    def collect(self) -> tuple[DiagnosticItem, ...]:
        return (
            DiagnosticItem(
                name="system",
                status=DiagnosticStatus.OK,
                detail="ready",
            ),
        )


class _Runtime:
    name = "runtime"

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth.READY

    async def process_state(self) -> ProcessState:
        return ProcessState.RUNNING


class _Persistence:
    async def state(self) -> PersistenceState:
        return PersistenceState(
            profile=PostgresConnectionProfile(),
            status=PersistenceConnectionStatus.DISABLED,
            message="password=private",
        )


def test_diagnostics_export_is_redacted_and_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "aiopenstudio.jsonl").write_text(
            'token=private C:\\Users\\person\\data', encoding="utf-8"
        )
        service = DiagnosticsService(
            application_version="test",
            session_id="session-1",
            environment="test",
            probe=_Probe(),
            runtimes={"runtime": _Runtime()},  # type: ignore[dict-item]
            persistence=_Persistence(),  # type: ignore[arg-type]
            log_dir=log_dir,
        )
        destination = await service.export(tmp_path / "diagnostics.zip")

        with zipfile.ZipFile(destination) as archive:
            snapshot = json.loads(archive.read("diagnostics.json"))
            logs = archive.read("logs/aiopenstudio.jsonl.txt").decode("utf-8")
        assert snapshot["session_id"] == "session-1"
        assert any(item["name"] == "runtime.runtime" for item in snapshot["items"])
        assert "private" not in logs
        assert "person" not in logs

    asyncio.run(scenario())
