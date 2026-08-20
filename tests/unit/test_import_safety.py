import os
import subprocess
import sys
from pathlib import Path


def test_imports_do_not_create_local_artifacts(tmp_path: Path) -> None:
    source_dir = Path(__file__).parents[2] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(source_dir), *sys.path))

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import socket; "
                "socket.socket.connect = lambda *args, **kwargs: "
                "(_ for _ in ()).throw(AssertionError('network access during import')); "
                "import aiopenstudio.core.config; "
                "import aiopenstudio.core.contracts; "
                "import aiopenstudio.infrastructure.database; "
                "import aiopenstudio.infrastructure.runtimes.ollama; "
                "import aiopenstudio.app"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
