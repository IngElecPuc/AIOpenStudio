from pathlib import Path

import pytest

from aiopenstudio.core.errors import RuntimeUnavailableError
from aiopenstudio.infrastructure.runtimes.fooocus import (
    FooocusProcessSettings,
    FooocusProcessSupervisor,
)
from aiopenstudio.infrastructure.runtimes.whisper import FasterWhisperRuntime


def test_whisper_restart_budget_stops_a_crash_loop(tmp_path: Path) -> None:
    runtime = FasterWhisperRuntime(
        tmp_path,
        restart_limit=1,
        restart_window_seconds=300,
    )

    runtime._register_restart()  # noqa: SLF001

    with pytest.raises(RuntimeUnavailableError, match="límite de reinicios"):
        runtime._register_restart()  # noqa: SLF001


def test_fooocus_restart_budget_stops_a_crash_loop(tmp_path: Path) -> None:
    settings = FooocusProcessSettings(
        home=tmp_path / "app",
        python_executable=tmp_path / "python.exe",
        models_root=tmp_path / "models",
        staging_root=tmp_path / "staging",
        runtime_root=tmp_path / "runtime",
        restart_limit=1,
        restart_window_seconds=300,
    )
    supervisor = FooocusProcessSupervisor(settings)

    supervisor._register_restart(10)  # noqa: SLF001

    with pytest.raises(RuntimeUnavailableError, match="límite de reinicios"):
        supervisor._register_restart(11)  # noqa: SLF001
