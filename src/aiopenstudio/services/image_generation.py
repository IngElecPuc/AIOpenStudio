"""Queued Fooocus generation, run isolation and resource orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from aiopenstudio.core.contracts import (
    ArtifactRecord,
    ComputeDevice,
    ExecutionHistory,
    ExecutionRecord,
    ExecutionStatus,
    ImageArtifact,
    ImageGenerationCapabilities,
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageGenerationRuntime,
    ImageGenerationStage,
    ImageProgress,
    LoadPolicy,
    ModelCatalog,
    ModelDescriptor,
    ResidencyPolicy,
    ResourceMonitor,
    RuntimeHealth,
)
from aiopenstudio.core.errors import RuntimeRequestError
from aiopenstudio.services.device_leases import DeviceLease, DeviceLeaseCoordinator


@dataclass(slots=True)
class _QueuedImageJob:
    request: ImageGenerationRequest
    events: asyncio.Queue[ImageGenerationEvent | None] = field(default_factory=asyncio.Queue)
    cancel_requested: bool = False


class ImageRunStore:
    """Persist completed files and sidecar metadata under one operation directory."""

    def __init__(
        self,
        output_root: Path,
        *,
        allowed_source_roots: Sequence[Path],
        max_image_bytes: int = 256 * 1024 * 1024,
        max_input_pixels: int = 40_000_000,
    ) -> None:
        self._output_root = output_root.resolve()
        self._allowed_source_roots = tuple(path.resolve() for path in allowed_source_roots)
        self._max_image_bytes = max_image_bytes
        self._max_input_pixels = max_input_pixels
        self._gallery_settings_path = self._output_root / "gallery-settings.json"
        self._gallery_index_path = self._output_root / "gallery-index.json"

    async def begin(self, request: ImageGenerationRequest) -> Path:
        return await asyncio.to_thread(self._begin_blocking, request)

    async def prepare_inputs(self, request: ImageGenerationRequest) -> ImageGenerationRequest:
        return await asyncio.to_thread(self._prepare_inputs_blocking, request)

    async def gallery_memory_enabled(self) -> bool:
        return await asyncio.to_thread(self._gallery_memory_enabled_blocking)

    async def set_gallery_memory(self, enabled: bool) -> None:
        await asyncio.to_thread(self._set_gallery_memory_blocking, enabled)

    async def remember_gallery(self, paths: Sequence[Path]) -> None:
        await asyncio.to_thread(self._append_gallery_blocking, tuple(paths))

    async def list_gallery(self) -> tuple[Path, ...]:
        return await asyncio.to_thread(self._list_gallery_blocking)

    async def forget_gallery(self) -> None:
        await asyncio.to_thread(self._forget_gallery_blocking)

    async def add_image(
        self,
        request: ImageGenerationRequest,
        source: Path,
        index: int,
        seed: int | None,
    ) -> ImageArtifact:
        return await asyncio.to_thread(self._add_image_blocking, request, source, index, seed)

    async def record_event(self, event: ImageGenerationEvent) -> None:
        await asyncio.to_thread(self._record_event_blocking, event)

    async def finish(
        self,
        request: ImageGenerationRequest,
        images: Sequence[ImageArtifact],
        *,
        elapsed_seconds: float,
        status: str,
        warnings: Sequence[str] = (),
        error: str | None = None,
    ) -> ImageGenerationResult:
        return await asyncio.to_thread(
            self._finish_blocking,
            request,
            tuple(images),
            elapsed_seconds,
            status,
            tuple(warnings),
            error,
        )

    def _begin_blocking(self, request: ImageGenerationRequest) -> Path:
        run = self._run_directory(request.operation_id)
        (run / "images").mkdir(parents=True, exist_ok=True)
        (run / "inputs" / "originals").mkdir(parents=True, exist_ok=True)
        (run / "inputs" / "normalized").mkdir(parents=True, exist_ok=True)
        return run

    def _prepare_inputs_blocking(self, request: ImageGenerationRequest) -> ImageGenerationRequest:
        run = self._run_directory(request.operation_id)
        manifest: list[dict[str, object]] = []
        counter = 0

        def stage(source: Path | None, role: str) -> Path | None:
            nonlocal counter
            if source is None:
                return None
            counter += 1
            source = source.expanduser().resolve()
            if not source.is_file():
                raise RuntimeRequestError(f"No existe la imagen {role}: {source.name}")
            if source.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".bmp"}:
                raise RuntimeRequestError(
                    f"El formato de {source.name} no está habilitado; usa PNG, JPEG o BMP."
                )
            size_bytes = source.stat().st_size
            if size_bytes > self._max_image_bytes:
                raise RuntimeRequestError(f"La imagen {source.name} supera el límite configurado.")
            image_module = import_module("PIL.Image")
            image_ops = import_module("PIL.ImageOps")
            try:
                with image_module.open(source) as opened:
                    actual_format = str(opened.format or "").upper()
                    frames = int(getattr(opened, "n_frames", 1))
                    width, height = opened.size
                    opened.verify()
                if actual_format not in {"PNG", "JPEG", "BMP"}:
                    raise RuntimeRequestError(
                        f"El contenido real de {source.name} no es PNG, JPEG o BMP."
                    )
                if frames != 1:
                    raise RuntimeRequestError("No se admiten imágenes animadas o multipágina.")
                if width < 1 or height < 1 or width * height > self._max_input_pixels:
                    raise RuntimeRequestError(
                        f"La imagen {source.name} excede el límite de píxeles."
                    )
                with image_module.open(source) as opened:
                    transposed = image_ops.exif_transpose(opened)
                    bands = transposed.getbands()
                    normalized = transposed.convert(
                        "RGBA" if "A" in bands or "transparency" in transposed.info else "RGB"
                    )
            except RuntimeRequestError:
                raise
            except Exception as error:
                raise RuntimeRequestError(
                    f"No fue posible validar la imagen {source.name}: {error}"
                ) from error

            stem = f"{counter:02d}-{role}"
            original = run / "inputs" / "originals" / f"{stem}{source.suffix.casefold()}"
            normalized_path = run / "inputs" / "normalized" / f"{stem}.png"
            original_temporary = original.with_name(original.name + ".partial")
            normalized_temporary = normalized_path.with_name(normalized_path.name + ".partial")
            shutil.copy2(source, original_temporary)
            original_temporary.replace(original)
            normalized.save(normalized_temporary, format="PNG")
            normalized_temporary.replace(normalized_path)
            normalized.close()
            manifest.append(
                {
                    "role": role,
                    "source_name": source.name,
                    "source_format": actual_format,
                    "source_size_bytes": size_bytes,
                    "source_sha256": self._sha256(original),
                    "normalized_path": str(normalized_path),
                    "normalized_sha256": self._sha256(normalized_path),
                    "width": width,
                    "height": height,
                }
            )
            return normalized_path

        source_image = stage(request.source_image, "source")
        mask_image = stage(request.mask_image, "mask")
        references = []
        for index, reference in enumerate(request.references, start=1):
            if not reference.enabled:
                continue
            staged = stage(reference.path, f"reference-{index}")
            if staged is not None:
                references.append(reference.model_copy(update={"path": staged}))
        destination = run / "inputs" / "manifest.json"
        temporary = destination.with_name(destination.name + ".partial")
        temporary.write_text(
            json.dumps({"schema_version": 1, "inputs": manifest}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
        return request.model_copy(
            update={
                "source_image": source_image,
                "mask_image": mask_image,
                "references": tuple(references),
            }
        )

    def _add_image_blocking(
        self,
        request: ImageGenerationRequest,
        source: Path,
        index: int,
        seed: int | None,
    ) -> ImageArtifact:
        source = source.resolve()
        if not any(source.is_relative_to(root) for root in self._allowed_source_roots):
            raise RuntimeRequestError("Fooocus devolvió una imagen fuera del staging permitido.")
        if not source.is_file() or source.stat().st_size > self._max_image_bytes:
            raise RuntimeRequestError(
                "La imagen generada no existe o supera el límite configurado."
            )
        suffix = source.suffix.casefold()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise RuntimeRequestError("Fooocus devolvió un formato de imagen no permitido.")
        image_module = import_module("PIL.Image")
        with image_module.open(source) as image:
            image.verify()
        with image_module.open(source) as image:
            width, height = image.size
        destination = self._run_directory(request.operation_id) / "images" / f"{index:04d}{suffix}"
        temporary = destination.with_name(destination.name + ".partial")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
        digest = self._sha256(destination)
        return ImageArtifact(
            path=destination,
            metadata_path=self._run_directory(request.operation_id) / "metadata.json",
            seed=seed,
            width=width,
            height=height,
            sha256=digest,
        )

    def _record_event_blocking(self, event: ImageGenerationEvent) -> None:
        run = self._run_directory(event.operation_id)
        run.mkdir(parents=True, exist_ok=True)
        payload = event.model_dump(mode="json", exclude={"result"})
        payload["captured_at"] = datetime.now(UTC).isoformat()
        with (run / "events.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _finish_blocking(
        self,
        request: ImageGenerationRequest,
        images: tuple[ImageArtifact, ...],
        elapsed_seconds: float,
        status: str,
        warnings: tuple[str, ...],
        error: str | None,
    ) -> ImageGenerationResult:
        run = self._run_directory(request.operation_id)
        metadata = {
            "schema_version": 1,
            "operation_id": request.operation_id,
            "status": status,
            "request": request.model_dump(mode="json"),
            "elapsed_seconds": elapsed_seconds,
            "images": [image.model_dump(mode="json") for image in images],
            "warnings": list(warnings),
            "error": error,
            "finished_at": datetime.now(UTC).isoformat(),
        }
        destination = run / "metadata.json"
        temporary = destination.with_name(destination.name + ".partial")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
        if status == "completed" and self._gallery_memory_enabled_blocking():
            self._append_gallery_blocking(tuple(image.path for image in images))
        return ImageGenerationResult(
            operation_id=request.operation_id,
            model=request.model,
            run_directory=run,
            elapsed_seconds=elapsed_seconds,
            images=images,
            cancelled=status == "cancelled",
            warnings=warnings,
        )

    def _run_directory(self, operation_id: str) -> Path:
        safe_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        if not operation_id or any(character not in safe_characters for character in operation_id):
            raise RuntimeRequestError("El identificador de ejecución no es seguro para una ruta.")
        return self._output_root / operation_id

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _gallery_memory_enabled_blocking(self) -> bool:
        try:
            payload = json.loads(self._gallery_settings_path.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        return payload.get("remember_gallery") is True

    def _set_gallery_memory_blocking(self, enabled: bool) -> None:
        self._output_root.mkdir(parents=True, exist_ok=True)
        temporary = self._gallery_settings_path.with_name(
            self._gallery_settings_path.name + ".partial"
        )
        temporary.write_text(json.dumps({"remember_gallery": enabled}, indent=2), encoding="utf-8")
        temporary.replace(self._gallery_settings_path)
        if not enabled:
            self._forget_gallery_blocking()

    def _append_gallery_blocking(self, paths: tuple[Path, ...]) -> None:
        current = list(self._list_gallery_blocking())
        for path in paths:
            resolved = path.resolve()
            if resolved not in current:
                current.append(resolved)
        temporary = self._gallery_index_path.with_name(self._gallery_index_path.name + ".partial")
        temporary.write_text(
            json.dumps(
                {"schema_version": 1, "images": [str(path) for path in current]},
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self._gallery_index_path)

    def _list_gallery_blocking(self) -> tuple[Path, ...]:
        if not self._gallery_memory_enabled_blocking():
            return ()
        try:
            payload = json.loads(self._gallery_index_path.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            return ()
        paths: list[Path] = []
        for raw_path in payload.get("images", []):
            if not isinstance(raw_path, str):
                continue
            path = Path(raw_path).resolve()
            if path.is_relative_to(self._output_root) and path.is_file():
                paths.append(path)
        return tuple(dict.fromkeys(paths))

    def _forget_gallery_blocking(self) -> None:
        try:
            self._gallery_index_path.unlink()
        except FileNotFoundError:
            pass


class ImageGenerationService:
    """Own the FIFO queue and expose a streaming use case to Tkinter or a future API."""

    def __init__(
        self,
        runtime: ImageGenerationRuntime,
        catalog: ModelCatalog,
        run_store: ImageRunStore,
        device_leases: DeviceLeaseCoordinator,
        *,
        residency_policy: ResidencyPolicy | None = None,
        resource_monitor: ResourceMonitor | None = None,
        execution_history: ExecutionHistory | None = None,
    ) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._run_store = run_store
        self._device_leases = device_leases
        self._residency_policy = residency_policy
        self._resource_monitor = resource_monitor
        self._execution_history = execution_history
        self._queue: asyncio.Queue[_QueuedImageJob | None] = asyncio.Queue()
        self._jobs: dict[str, _QueuedImageJob] = {}
        self._worker: asyncio.Task[None] | None = None
        self._active_operation: str | None = None

    @property
    def runtime(self) -> ImageGenerationRuntime:
        return self._runtime

    @staticmethod
    def create_operation_id() -> str:
        return str(uuid.uuid4())

    def preflight(self) -> tuple[str, ...]:
        return self._runtime.preflight()

    def preflight_for(self, request: ImageGenerationRequest) -> tuple[str, ...]:
        return self._runtime.preflight_for(request)

    async def image_capabilities(self) -> ImageGenerationCapabilities:
        return await self._runtime.image_capabilities()

    async def gallery_memory_enabled(self) -> bool:
        return await self._run_store.gallery_memory_enabled()

    async def set_gallery_memory(self, enabled: bool) -> None:
        await self._run_store.set_gallery_memory(enabled)

    async def remember_gallery(self, paths: Sequence[Path]) -> None:
        await self._run_store.remember_gallery(paths)

    async def list_gallery(self) -> tuple[Path, ...]:
        return await self._run_store.list_gallery()

    async def forget_gallery(self) -> None:
        await self._run_store.forget_gallery()

    @property
    def active_operation(self) -> str | None:
        return self._active_operation

    async def health(self) -> RuntimeHealth:
        return await self._runtime.health()

    async def refresh_models(self) -> Sequence[ModelDescriptor]:
        models = tuple(await self._runtime.list_models())
        live_keys = {descriptor.id.key for descriptor in models}
        for stale in self._catalog.list(runtime=self._runtime.name):
            if stale.id.key not in live_keys:
                self._catalog.remove(stale.id)
        for descriptor in models:
            self._catalog.save(descriptor)
        return models

    async def list_styles(self) -> Sequence[str]:
        return await self._runtime.list_styles()

    async def stream_generation(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        if request.operation_id in self._jobs:
            raise RuntimeRequestError("Ya existe una ejecución con ese identificador.")
        job = _QueuedImageJob(request=request)
        self._jobs[request.operation_id] = job
        await self._queue.put(job)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work())
        await self._emit(
            job,
            ImageGenerationEvent(
                operation_id=request.operation_id,
                kind=ImageGenerationEventKind.QUEUED,
                progress=ImageProgress(
                    stage=ImageGenerationStage.QUEUED,
                    queue_position=self._queue.qsize() - 1,
                    detail="Trabajo añadido a la cola local.",
                ),
            ),
            persist=False,
        )
        try:
            while True:
                event = await job.events.get()
                if event is None:
                    return
                yield event
        finally:
            self._jobs.pop(request.operation_id, None)

    async def cancel(self, operation_id: str) -> None:
        job = self._jobs.get(operation_id)
        if job is None:
            return
        job.cancel_requested = True
        if self._active_operation == operation_id:
            await self._runtime.cancel(operation_id)

    async def close(self) -> None:
        if self._active_operation is not None:
            await self._runtime.cancel(self._active_operation)
        await self._queue.put(None)
        if self._worker is not None:
            await asyncio.gather(self._worker, return_exceptions=True)
        await self._runtime.stop()

    async def _work(self) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                return
            try:
                await self._execute(job)
            except Exception as error:
                await self._emit(
                    job,
                    ImageGenerationEvent(
                        operation_id=job.request.operation_id,
                        kind=ImageGenerationEventKind.ERROR,
                        progress=ImageProgress(stage=ImageGenerationStage.FAILED),
                        message=str(error),
                    ),
                    persist=False,
                )
            finally:
                await job.events.put(None)
                self._queue.task_done()

    async def _execute(self, job: _QueuedImageJob) -> None:
        request = job.request
        started = time.perf_counter()
        started_at = datetime.now(UTC)
        await self._run_store.begin(request)
        await self._save_execution(
            request,
            ExecutionStatus.RUNNING,
            started_at=started_at,
        )
        if job.cancel_requested:
            result = await self._run_store.finish(
                request, (), elapsed_seconds=0, status="cancelled"
            )
            await self._save_execution(
                request,
                ExecutionStatus.CANCELLED,
                started_at=started_at,
                result=result,
            )
            await self._emit(job, self._terminal_event(result))
            return
        self._active_operation = request.operation_id
        images: list[ImageArtifact] = []
        warnings: list[str] = []
        lease = self._device_leases.lease(request.model)
        entered = False
        loaded = False
        cancelled = False
        try:
            request = await self._run_store.prepare_inputs(request)
            request_issues = self._runtime.preflight_for(request)
            if request_issues:
                raise RuntimeRequestError(" ".join(request_issues))
            await self._emit(
                job,
                ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.PROGRESS,
                    progress=ImageProgress(
                        stage=ImageGenerationStage.WAITING_FOR_DEVICE,
                        detail="Esperando exclusividad de GPU y suites activas…",
                    ),
                ),
            )
            await lease.__aenter__()
            entered = True
            descriptor = self._catalog.get(request.model)
            policy = LoadPolicy(device=ComputeDevice.GPU)
            if self._residency_policy is not None:
                await self._residency_policy.before_load(
                    request.model,
                    policy,
                    descriptor.size_bytes if descriptor else None,
                )
            try:
                state = await self._runtime.load(request.model, policy)
                loaded = True
            except Exception:
                if self._residency_policy is not None:
                    self._residency_policy.model_load_failed(request.model)
                raise
            if self._residency_policy is not None:
                self._residency_policy.model_loaded(state, policy)
            if self._resource_monitor is not None:
                await self._resource_monitor.snapshot()
            if job.cancel_requested:
                cancelled = True
            else:
                await self._emit(
                    job,
                    ImageGenerationEvent(
                        operation_id=request.operation_id,
                        kind=ImageGenerationEventKind.STARTED,
                        progress=ImageProgress(stage=ImageGenerationStage.STARTING_RUNTIME),
                    ),
                )
                async for event in self._runtime.generate(request):
                    if (
                        event.kind is ImageGenerationEventKind.IMAGE
                        and event.source_path is not None
                    ):
                        index = len(images) + 1
                        seed = (
                            request.options.seed + index - 1
                            if request.options.seed is not None
                            else event.seed
                        )
                        artifact = await self._run_store.add_image(
                            request, event.source_path, index, seed
                        )
                        images.append(artifact)
                        event = event.model_copy(
                            update={"source_path": artifact.path, "seed": seed}
                        )
                    elif event.kind is ImageGenerationEventKind.CANCELLED:
                        cancelled = True
                        continue
                    await self._emit(job, event)
            cancelled = cancelled or job.cancel_requested
        except Exception as error:
            await self._runtime.cancel(request.operation_id)
            if entered:
                await self._release_runtime(request, loaded, lease, warnings)
                entered = False
            elapsed = time.perf_counter() - started
            result = await self._run_store.finish(
                request,
                images,
                elapsed_seconds=elapsed,
                status="failed",
                warnings=warnings,
                error=str(error),
            )
            await self._save_execution(
                request,
                ExecutionStatus.FAILED,
                started_at=started_at,
                result=result,
                error=str(error),
            )
            await self._emit(
                job,
                ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.ERROR,
                    progress=ImageProgress(stage=ImageGenerationStage.FAILED),
                    message=str(error),
                ),
            )
            return
        finally:
            self._active_operation = None
        if entered:
            await self._release_runtime(request, loaded, lease, warnings)
        elapsed = time.perf_counter() - started
        result = await self._run_store.finish(
            request,
            images,
            elapsed_seconds=elapsed,
            status="cancelled" if cancelled else "completed",
            warnings=warnings,
        )
        await self._save_execution(
            request,
            ExecutionStatus.CANCELLED if cancelled else ExecutionStatus.COMPLETED,
            started_at=started_at,
            result=result,
        )
        await self._emit(job, self._terminal_event(result))

    async def _save_execution(
        self,
        request: ImageGenerationRequest,
        status: ExecutionStatus,
        *,
        started_at: datetime,
        result: ImageGenerationResult | None = None,
        error: str | None = None,
    ) -> None:
        if self._execution_history is None:
            return
        request_metadata = {
            "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
            "negative_prompt_sha256": hashlib.sha256(
                request.negative_prompt.encode("utf-8")
            ).hexdigest(),
            "options": request.options.model_dump(mode="json"),
            "operation": request.operation.value,
            "input_count": int(request.source_image is not None)
            + int(request.mask_image is not None)
            + len(request.references),
        }
        result_metadata: dict[str, object] = {}
        artifacts: tuple[ArtifactRecord, ...] = ()
        if result is not None:
            result_metadata = {
                "elapsed_seconds": result.elapsed_seconds,
                "cancelled": result.cancelled,
                "warnings": list(result.warnings),
                "image_count": len(result.images),
            }
            artifacts = tuple(
                ArtifactRecord(
                    artifact_id=f"{request.operation_id}-image-{index}",
                    operation_id=request.operation_id,
                    kind="image",
                    path=str(image.path),
                    mime_type={
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".webp": "image/webp",
                    }.get(image.path.suffix.casefold()),
                    size_bytes=image.path.stat().st_size if image.path.is_file() else None,
                    sha256=image.sha256,
                    metadata={
                        "width": image.width,
                        "height": image.height,
                        "seed": image.seed,
                        "metadata_path": str(image.metadata_path),
                    },
                )
                for index, image in enumerate(result.images, start=1)
            )
        await self._execution_history.save_execution(
            ExecutionRecord(
                operation_id=request.operation_id,
                suite="fooocus",
                operation_type="image_generation",
                status=status,
                runtime=request.model.runtime,
                model_key=request.model.key,
                started_at=started_at,
                finished_at=datetime.now(UTC) if status is not ExecutionStatus.RUNNING else None,
                request_metadata=request_metadata,
                result_metadata=result_metadata,
                error_message=error,
            ),
            artifacts,
        )

    async def _release_runtime(
        self,
        request: ImageGenerationRequest,
        loaded: bool,
        lease: DeviceLease,
        warnings: list[str],
    ) -> None:
        if loaded:
            try:
                await self._runtime.unload(request.model)
            except Exception as error:
                warnings.append(f"No fue posible detener Fooocus: {error}")
            finally:
                if self._residency_policy is not None:
                    self._residency_policy.model_unloaded(request.model)
        try:
            await lease.__aexit__(None, None, None)
        except BaseException as error:
            warnings.append(f"No fue posible restaurar todas las suites: {error}")
        if self._resource_monitor is not None:
            await self._resource_monitor.snapshot()

    async def _emit(
        self,
        job: _QueuedImageJob,
        event: ImageGenerationEvent,
        *,
        persist: bool = True,
    ) -> None:
        if persist:
            await self._run_store.record_event(event)
        await job.events.put(event)

    @staticmethod
    def _terminal_event(result: ImageGenerationResult) -> ImageGenerationEvent:
        return ImageGenerationEvent(
            operation_id=result.operation_id,
            kind=(
                ImageGenerationEventKind.CANCELLED
                if result.cancelled
                else ImageGenerationEventKind.COMPLETED
            ),
            progress=ImageProgress(
                stage=(
                    ImageGenerationStage.CANCELLED
                    if result.cancelled
                    else ImageGenerationStage.COMPLETED
                ),
                fraction=1,
            ),
            result=result,
        )
