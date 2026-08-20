"""Supervise an isolated Fooocus process without invoking an updater."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import psutil  # type: ignore[import-untyped]

from aiopenstudio.core.errors import RuntimeUnavailableError


@dataclass(frozen=True, slots=True)
class FooocusProcessSettings:
    home: Path
    python_executable: Path
    models_root: Path
    staging_root: Path
    runtime_root: Path
    host: str = "127.0.0.1"
    port: int = 7865
    startup_timeout_seconds: float = 180.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class FooocusProcessSupervisor:
    _REQUIRED_DISTRIBUTIONS = {
        "gradio": "3.41.2",
        "fastapi": "0.101.0",
        "starlette": "0.27.0",
    }
    _PROMPT_EXPANSION_SUPPORT_FILES = (
        "config.json",
        "merges.txt",
        "positive.txt",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.json",
    )

    def __init__(self, settings: FooocusProcessSettings) -> None:
        self.settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._output_task: asyncio.Task[None] | None = None
        self._logs: deque[str] = deque(maxlen=200)
        self._selected_checkpoint: str | None = None

    @property
    def process_id(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.returncode is None else None

    @property
    def running(self) -> bool:
        return self.process_id is not None

    @property
    def recent_logs(self) -> tuple[str, ...]:
        return tuple(self._logs)

    @property
    def log_path(self) -> Path:
        return self.settings.runtime_root / "fooocus-process.log"

    def preflight(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.settings.home.is_dir():
            issues.append(f"No existe el directorio Fooocus: {self.settings.home}")
        if not self.settings.python_executable.is_file():
            issues.append(
                f"No existe el intérprete aislado de Fooocus: {self.settings.python_executable}"
            )
        if not (self.settings.home / "launch.py").is_file():
            issues.append("La instalación configurada no contiene launch.py.")
        checkpoints = self.settings.models_root / "checkpoints"
        if not checkpoints.is_dir() or not any(
            path.suffix.casefold() in {".safetensors", ".ckpt"}
            for path in checkpoints.iterdir()
            if path.is_file()
        ):
            issues.append("No hay checkpoints Fooocus locales en la biblioteca compartida.")
        if self.settings.host not in {"127.0.0.1", "localhost", "::1"}:
            issues.append("Fooocus debe enlazarse exclusivamente a loopback.")
        required_assets = (
            self.settings.models_root / "vae_approx/xlvaeapp.pth",
            self.settings.models_root / "vae_approx/vaeapp_sd15.pth",
            self.settings.models_root
            / "vae_approx/xl-to-v1_interposer-v4.0.safetensors",
            self.settings.models_root
            / "prompt_expansion/fooocus_expansion/pytorch_model.bin",
        )
        for asset in required_assets:
            if not asset.is_file():
                issues.append(f"Falta el activo auxiliar Fooocus local: {asset}")
        bundled_expansion = (
            self.settings.home / "models/prompt_expansion/fooocus_expansion"
        )
        for filename in self._PROMPT_EXPANSION_SUPPORT_FILES:
            source = bundled_expansion / filename
            if not source.is_file():
                issues.append(
                    "La fuente Fooocus no contiene el archivo de expansión incluido "
                    f"{source}."
                )
        issues.extend(self._dependency_issues())
        return tuple(issues)

    def _dependency_issues(self) -> tuple[str, ...]:
        environment_root = self.settings.python_executable.parent.parent
        candidates = [environment_root / "Lib/site-packages"]
        candidates.extend(environment_root.glob("lib/python*/site-packages"))
        site_packages = next((path for path in candidates if path.is_dir()), None)
        if site_packages is None:
            return ()
        issues: list[str] = []
        for package, expected in self._REQUIRED_DISTRIBUTIONS.items():
            versions = self._distribution_versions(site_packages, package)
            if versions != {expected}:
                detected = ", ".join(sorted(versions)) if versions else "ausente"
                issues.append(
                    f"El entorno Fooocus requiere {package}=={expected}; se detectó {detected}."
                )
        return tuple(issues)

    @staticmethod
    def _distribution_versions(site_packages: Path, package: str) -> set[str]:
        versions: set[str] = set()
        normalized = package.replace("-", "_")
        for metadata in site_packages.glob(f"{normalized}-*.dist-info/METADATA"):
            try:
                for line in metadata.read_text(encoding="utf-8").splitlines():
                    if line.startswith("Version: "):
                        versions.add(line.removeprefix("Version: ").strip())
                        break
            except OSError:
                continue
        return versions

    def select_checkpoint(self, name: str) -> None:
        if Path(name).name != name:
            raise RuntimeUnavailableError("El nombre del checkpoint Fooocus no es seguro.")
        candidate = self.settings.models_root / "checkpoints" / name
        if not candidate.is_file():
            raise RuntimeUnavailableError(f"No existe el checkpoint Fooocus local {name!r}.")
        self._selected_checkpoint = name

    async def start(self) -> None:
        if self.running:
            return
        issues = self.preflight()
        if issues:
            raise RuntimeUnavailableError(" ".join(issues))
        await asyncio.to_thread(self._write_runtime_config)
        self._logs.clear()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "config_path": str(self.settings.runtime_root / "config.json"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "GRADIO_ANALYTICS_ENABLED": "False",
                "GRADIO_TEMP_DIR": str(self.settings.runtime_root / "gradio"),
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "PIP_NO_INDEX": "1",
                "PYTHONUNBUFFERED": "1",
                "REQS_FILE": str(self.settings.runtime_root / "requirements-offline.txt"),
                "TORCH_COMMAND": "pip --version",
            }
        )
        self._process = await asyncio.create_subprocess_exec(
            str(self.settings.python_executable),
            *self._launch_arguments(),
            cwd=self.settings.home,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._output_task = asyncio.create_task(self._capture_output())

    def _launch_arguments(self) -> tuple[str, ...]:
        return (
            "launch.py",
            "--listen",
            self.settings.host,
            "--port",
            str(self.settings.port),
            "--temp-path",
            str(self.settings.runtime_root / "gradio"),
            "--disable-in-browser",
            "--disable-preset-selection",
            "--disable-preset-download",
        )

    async def stop(self) -> None:
        process = self._process
        if process is None:
            return
        pid = process.pid
        await asyncio.to_thread(self._terminate_tree, pid)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        if self._output_task is not None:
            await asyncio.gather(self._output_task, return_exceptions=True)
        self._output_task = None
        self._process = None

    async def _capture_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        with self.log_path.open("a", encoding="utf-8") as log_stream:
            while True:
                line = await process.stdout.readline()
                if not line:
                    return
                decoded = line.decode(errors="replace").rstrip()
                self._logs.append(decoded)
                with suppress(OSError):
                    log_stream.write(decoded + "\n")
                    log_stream.flush()

    def _write_runtime_config(self) -> None:
        self.settings.runtime_root.mkdir(parents=True, exist_ok=True)
        self.settings.staging_root.mkdir(parents=True, exist_ok=True)
        self._stage_prompt_expansion_support_files()
        (self.settings.runtime_root / "requirements-offline.txt").write_text(
            "# Dependencies are provisioned explicitly in the isolated environment.\n",
            encoding="utf-8",
        )
        payload = {
            "default_model": self._selected_checkpoint,
            "default_refiner": "None",
            "path_checkpoints": str(self.settings.models_root / "checkpoints"),
            "path_loras": str(self.settings.models_root / "loras"),
            "path_embeddings": str(self.settings.models_root / "embeddings"),
            "path_vae_approx": str(self.settings.models_root / "vae_approx"),
            "path_vae": str(self.settings.models_root / "vae"),
            "path_upscale_models": str(self.settings.models_root / "upscale_models"),
            "path_inpaint": str(self.settings.models_root / "inpaint"),
            "path_controlnet": str(self.settings.models_root / "controlnet"),
            "path_clip_vision": str(self.settings.models_root / "clip_vision"),
            "path_fooocus_expansion": str(
                self.settings.models_root / "prompt_expansion/fooocus_expansion"
            ),
            "path_outputs": str(self.settings.staging_root),
        }
        destination = self.settings.runtime_root / "config.json"
        temporary = destination.with_name(destination.name + ".partial")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(destination)

    def _stage_prompt_expansion_support_files(self) -> None:
        """Complete the shared expansion directory from the pinned Fooocus source."""
        source_root = self.settings.home / "models/prompt_expansion/fooocus_expansion"
        destination_root = (
            self.settings.models_root / "prompt_expansion/fooocus_expansion"
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        for filename in self._PROMPT_EXPANSION_SUPPORT_FILES:
            source = source_root / filename
            destination = destination_root / filename
            if destination.is_file() and destination.read_bytes() == source.read_bytes():
                continue
            temporary = destination.with_name(destination.name + ".partial")
            shutil.copy2(source, temporary)
            temporary.replace(destination)

    @staticmethod
    def _terminate_tree(pid: int) -> None:
        try:
            parent = psutil.Process(pid)
            processes = parent.children(recursive=True)
            processes.append(parent)
            for process in processes:
                process.terminate()
            _, alive = psutil.wait_procs(processes, timeout=3)
            for process in alive:
                process.kill()
        except (psutil.Error, OSError):
            return
