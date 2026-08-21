"""Collect and export redacted support diagnostics."""

from __future__ import annotations

import asyncio
import logging
import zipfile
from collections.abc import Mapping
from pathlib import Path

from aiopenstudio.core.contracts import (
    DiagnosticItem,
    DiagnosticProbe,
    DiagnosticSnapshot,
    DiagnosticStatus,
    ModelLifecycleRuntime,
)

from .logging import SensitiveDataFilter
from .persistence import PersistenceService


class DiagnosticsService:
    def __init__(
        self,
        *,
        application_version: str,
        session_id: str,
        environment: str,
        probe: DiagnosticProbe,
        runtimes: Mapping[str, ModelLifecycleRuntime],
        persistence: PersistenceService,
        log_dir: Path,
    ) -> None:
        self._application_version = application_version
        self._session_id = session_id
        self._environment = environment
        self._probe = probe
        self._runtimes = dict(runtimes)
        self._persistence = persistence
        self._log_dir = log_dir.resolve()
        self._logger = logging.getLogger("aiopenstudio.diagnostics")

    async def collect(self) -> DiagnosticSnapshot:
        items = list(await asyncio.to_thread(self._probe.collect))
        for name, runtime in self._runtimes.items():
            try:
                health, process = await asyncio.gather(
                    runtime.health(), runtime.process_state()
                )
                status = (
                    DiagnosticStatus.OK
                    if health.value in {"ready", "starting"}
                    else DiagnosticStatus.WARNING
                )
                items.append(
                    DiagnosticItem(
                        name=f"runtime.{name}",
                        status=status,
                        detail=f"health={health.value}; process={process.value}",
                    )
                )
            except Exception as error:
                items.append(
                    DiagnosticItem(
                        name=f"runtime.{name}",
                        status=DiagnosticStatus.ERROR,
                        detail=SensitiveDataFilter.redact(str(error)),
                    )
                )
        persistence = await self._persistence.state()
        items.append(
            DiagnosticItem(
                name="persistence",
                status=(
                    DiagnosticStatus.WARNING
                    if persistence.fallback_active
                    else DiagnosticStatus.OK
                ),
                detail=SensitiveDataFilter.redact(persistence.message),
                metadata={
                    "mode": persistence.profile.mode.value,
                    "connection_status": persistence.status.value,
                    "pending_operations": persistence.pending_operations,
                    "fallback_active": persistence.fallback_active,
                },
            )
        )
        return DiagnosticSnapshot(
            application_version=self._application_version,
            session_id=self._session_id,
            environment=self._environment,
            items=tuple(items),
        )

    async def export(self, destination: Path) -> Path:
        snapshot = await self.collect()
        result = await asyncio.to_thread(self._export_blocking, destination, snapshot)
        self._logger.info(
            "diagnostics.exported",
            extra={"component": "diagnostics", "destination_name": result.name},
        )
        return result

    def _export_blocking(
        self, destination: Path, snapshot: DiagnosticSnapshot
    ) -> Path:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "diagnostics.json",
                snapshot.model_dump_json(indent=2),
            )
            for log_path in sorted(self._log_dir.glob("*.jsonl*")):
                if not log_path.is_file():
                    continue
                content = self._read_tail(log_path, 256 * 1024)
                archive.writestr(
                    f"logs/{log_path.name}.txt",
                    SensitiveDataFilter.redact(content),
                )
        temporary.replace(destination)
        return destination

    @staticmethod
    def _read_tail(path: Path, limit: int) -> str:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace")
