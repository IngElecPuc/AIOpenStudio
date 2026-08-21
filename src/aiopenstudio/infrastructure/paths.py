"""Filesystem layout that keeps replaceable binaries separate from user data."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    runtime_root: Path
    config_root: Path
    packaged: bool

    @property
    def env_file(self) -> Path:
        return self.config_root / ".env"

    @classmethod
    def discover(cls) -> ApplicationPaths:
        packaged = bool(getattr(sys, "frozen", False))
        if not packaged:
            root = Path.cwd().resolve()
            return cls(runtime_root=root, config_root=root, packaged=False)
        return cls(
            runtime_root=Path(
                user_data_dir("AIOpenStudio", appauthor=False, roaming=False)
            ).resolve(),
            config_root=Path(
                user_config_dir("AIOpenStudio", appauthor=False, roaming=True)
            ).resolve(),
            packaged=True,
        )

    def resolve_runtime(self, path: Path) -> Path:
        expanded = path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return (self.runtime_root / expanded).resolve()
