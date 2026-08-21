"""Read-only local system diagnostics."""

from __future__ import annotations

import platform
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import psutil  # type: ignore[import-untyped]

from aiopenstudio.core.contracts import DiagnosticItem, DiagnosticStatus


class SystemDiagnosticProbe:
    def __init__(self, paths: Mapping[str, Path]) -> None:
        self._paths = {name: path.resolve() for name, path in paths.items()}

    def collect(self) -> Sequence[DiagnosticItem]:
        memory = psutil.virtual_memory()
        items = [
            DiagnosticItem(
                name="system",
                status=DiagnosticStatus.OK,
                detail=f"{platform.system()} {platform.release()} ({platform.machine()})",
                metadata={
                    "python": sys.version.split()[0],
                    "cpu_logical": psutil.cpu_count(),
                    "ram_total_bytes": int(memory.total),
                    "ram_available_bytes": int(memory.available),
                },
            )
        ]
        for name, path in self._paths.items():
            anchor = path if path.exists() else path.parent
            try:
                usage = shutil.disk_usage(anchor)
                metadata: dict[str, object] = {
                    "path": self._display_path(path),
                    "exists": path.exists(),
                    "free_bytes": usage.free,
                }
                status = DiagnosticStatus.OK if path.exists() else DiagnosticStatus.WARNING
                detail = "Ruta disponible." if path.exists() else "La ruta todavía no existe."
            except OSError as error:
                metadata = {"path": self._display_path(path)}
                status = DiagnosticStatus.ERROR
                detail = str(error)
            items.append(
                DiagnosticItem(
                    name=f"path.{name}",
                    status=status,
                    detail=detail,
                    metadata=metadata,
                )
            )
        return tuple(items)

    @staticmethod
    def _display_path(path: Path) -> str:
        try:
            relative = path.relative_to(Path.home())
        except ValueError:
            return str(path)
        return str(Path("%USERPROFILE%") / relative)
