"""Supervise an isolated Fooocus process without invoking an updater."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import psutil  # type: ignore[import-untyped]

from aiopenstudio.core.contracts import (
    DescribeContent,
    ImageGenerationRequest,
    ImageOperation,
    ImagePromptKind,
)
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
    restart_limit: int = 3
    restart_window_seconds: float = 300.0

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
    _ADVANCED_ASSET_PATHS = (
        "upscale_models/fooocus_upscaler_s409985e5.bin",
        "inpaint/fooocus_inpaint_head.pth",
        "inpaint/inpaint_v26.fooocus.patch",
        "inpaint/groundingdino_swint_ogc.pth",
        "controlnet/control-lora-canny-rank128.safetensors",
        "controlnet/fooocus_xl_cpds_128.safetensors",
        "controlnet/fooocus_ip_negative.safetensors",
        "controlnet/ip-adapter-plus_sdxl_vit-h.bin",
        "controlnet/ip-adapter-plus-face_sdxl_vit-h.bin",
        "clip_vision/clip_vision_vit_h.safetensors",
        "clip_vision/model_base_caption_capfilt_large.pth",
        "clip_vision/wd-v1-4-moat-tagger-v2.onnx",
        "clip_vision/wd-v1-4-moat-tagger-v2.csv",
        "sam/sam_vit_b_01ec64.pth",
        "sam/sam_vit_l_0b3195.pth",
        "sam/sam_vit_h_4b8939.pth",
        "rembg/u2net.onnx",
        "rembg/u2netp.onnx",
        "rembg/u2net_human_seg.onnx",
        "rembg/u2net_cloth_seg.onnx",
        "rembg/silueta.onnx",
        "rembg/isnet-general-use.onnx",
        "rembg/isnet-anime.onnx",
    )

    def __init__(self, settings: FooocusProcessSettings) -> None:
        self.settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._output_task: asyncio.Task[None] | None = None
        self._logs: deque[str] = deque(maxlen=200)
        self._selected_checkpoint: str | None = None
        self._restart_times: deque[float] = deque()
        self._logger = logging.getLogger("aiopenstudio.runtime.fooocus")

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
            self.settings.models_root / "vae_approx/xl-to-v1_interposer-v4.0.safetensors",
            self.settings.models_root / "prompt_expansion/fooocus_expansion/pytorch_model.bin",
        )
        for asset in required_assets:
            if not asset.is_file():
                issues.append(f"Falta el activo auxiliar Fooocus local: {asset}")
        bundled_expansion = self.settings.home / "models/prompt_expansion/fooocus_expansion"
        for filename in self._PROMPT_EXPANSION_SUPPORT_FILES:
            source = bundled_expansion / filename
            if not source.is_file():
                issues.append(
                    f"La fuente Fooocus no contiene el archivo de expansión incluido {source}."
                )
        issues.extend(self._dependency_issues())
        return tuple(issues)

    def preflight_for(self, request: ImageGenerationRequest) -> tuple[str, ...]:
        """Block operations whose upstream helper would otherwise download an asset."""
        required: set[Path] = set()
        issues: list[str] = []
        models = self.settings.models_root
        if request.operation in {
            ImageOperation.UPSCALE_1_5,
            ImageOperation.UPSCALE_2,
            ImageOperation.UPSCALE_FAST_2,
        } or (
            request.enhance is not None
            and request.enhance.uov_operation
            in {
                ImageOperation.UPSCALE_1_5,
                ImageOperation.UPSCALE_2,
                ImageOperation.UPSCALE_FAST_2,
            }
        ):
            required.add(models / "upscale_models/fooocus_upscaler_s409985e5.bin")
        if request.operation in {
            ImageOperation.INPAINT,
            ImageOperation.OUTPAINT,
            ImageOperation.ENHANCE,
        }:
            required.update(
                {
                    models / "inpaint/fooocus_inpaint_head.pth",
                    models / "inpaint/inpaint_v26.fooocus.patch",
                }
            )
        reference_kinds = {reference.kind for reference in request.references if reference.enabled}
        if ImagePromptKind.PYRA_CANNY in reference_kinds:
            required.add(models / "controlnet/control-lora-canny-rank128.safetensors")
        if ImagePromptKind.CPDS in reference_kinds:
            required.add(models / "controlnet/fooocus_xl_cpds_128.safetensors")
        if reference_kinds & {ImagePromptKind.IMAGE_PROMPT, ImagePromptKind.FACE_SWAP}:
            required.update(
                {
                    models / "clip_vision/clip_vision_vit_h.safetensors",
                    models / "controlnet/fooocus_ip_negative.safetensors",
                }
            )
        if ImagePromptKind.IMAGE_PROMPT in reference_kinds:
            required.add(models / "controlnet/ip-adapter-plus_sdxl_vit-h.bin")
        if ImagePromptKind.FACE_SWAP in reference_kinds:
            required.add(models / "controlnet/ip-adapter-plus-face_sdxl_vit-h.bin")
        if request.operation is ImageOperation.DESCRIBE:
            if DescribeContent.PHOTOGRAPH in request.describe_content:
                required.add(models / "clip_vision/model_base_caption_capfilt_large.pth")
            if DescribeContent.ART_ANIME in request.describe_content:
                required.update(
                    {
                        models / "clip_vision/wd-v1-4-moat-tagger-v2.onnx",
                        models / "clip_vision/wd-v1-4-moat-tagger-v2.csv",
                    }
                )
        if request.operation is ImageOperation.ENHANCE and request.enhance is not None:
            for step in request.enhance.steps:
                if not step.enabled:
                    continue
                if step.detection_prompt:
                    required.add(models / "inpaint/groundingdino_swint_ogc.pth")
                if step.mask_model == "sam":
                    sam_files = {
                        "vit_b": "sam_vit_b_01ec64.pth",
                        "vit_l": "sam_vit_l_0b3195.pth",
                        "vit_h": "sam_vit_h_4b8939.pth",
                    }
                    required.add(models / "sam" / sam_files.get(step.sam_model, "missing"))
                else:
                    rembg_models = {
                        "u2net",
                        "u2netp",
                        "u2net_human_seg",
                        "u2net_cloth_seg",
                        "silueta",
                        "isnet-general-use",
                        "isnet-anime",
                    }
                    if step.mask_model not in rembg_models:
                        issues.append(
                            f"El modelo de máscara Enhance {step.mask_model!r} no pertenece "
                            "al esquema Fooocus v2.5.5 fijado."
                        )
                    else:
                        required.add(models / "rembg" / f"{step.mask_model}.onnx")
        issues.extend(
            "Falta el activo avanzado Fooocus local "
            f"{path}; la descarga automática permanece bloqueada."
            for path in sorted(required)
            if not path.is_file()
        )
        return tuple(issues)

    def advanced_asset_inventory(self) -> tuple[dict[str, object], ...]:
        inventory: list[dict[str, object]] = []
        for relative_path in self._ADVANCED_ASSET_PATHS:
            path = self.settings.models_root / relative_path
            exists = path.is_file()
            inventory.append(
                {
                    "relative_path": relative_path,
                    "exists": exists,
                    "size_bytes": path.stat().st_size if exists else None,
                    "sha256": self._sha256(path) if exists else None,
                }
            )
        return tuple(inventory)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

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
        if self._process is not None:
            exit_code = self._process.returncode
            if self._output_task is not None:
                await asyncio.gather(self._output_task, return_exceptions=True)
            self._process = None
            self._output_task = None
            self._register_restart(asyncio.get_running_loop().time())
            self._logger.warning(
                "runtime.process_restarting",
                extra={
                    "component": "fooocus",
                    "runtime": "fooocus",
                    "previous_exit_code": exit_code,
                    "restart_count": len(self._restart_times),
                },
            )
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
                "U2NET_HOME": str(self.settings.models_root / "rembg"),
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

    def _register_restart(self, now: float) -> None:
        while (
            self._restart_times
            and now - self._restart_times[0] > self.settings.restart_window_seconds
        ):
            self._restart_times.popleft()
        if len(self._restart_times) >= self.settings.restart_limit:
            raise RuntimeUnavailableError(
                "Fooocus superó el límite de reinicios; revisa Diagnósticos."
            )
        self._restart_times.append(now)

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
            "path_sam": str(self.settings.models_root / "sam"),
            "path_safety_checker": str(self.settings.models_root / "safety_checker"),
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
        destination_root = self.settings.models_root / "prompt_expansion/fooocus_expansion"
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
