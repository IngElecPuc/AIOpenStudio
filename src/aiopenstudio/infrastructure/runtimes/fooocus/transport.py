"""Gradio transport isolated behind the Fooocus runtime contract."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Event, RLock
from typing import Any, Protocol

import httpx

from aiopenstudio.core.contracts import (
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationRequest,
    ImageGenerationStage,
    ImagePerformance,
    ImageProgress,
)
from aiopenstudio.core.errors import RuntimeRequestError, RuntimeUnavailableError


class FooocusTransport(Protocol):
    def preflight(self) -> tuple[str, ...]: ...

    async def health(self) -> bool: ...

    async def list_models(self) -> Sequence[str]: ...

    async def list_styles(self) -> Sequence[str]: ...

    def generate(self, request: ImageGenerationRequest) -> AsyncIterator[ImageGenerationEvent]: ...

    async def cancel(self, operation_id: str) -> None: ...

    async def close(self) -> None: ...


class GradioFooocusTransport:
    """Discover Fooocus component IDs at runtime and submit one Gradio session."""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout_seconds: float = 5.0,
        download_root: Path | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._client_factory = client_factory
        self._download_root = download_root
        self._active_jobs: dict[str, Any] = {}
        self._cancelled: dict[str, Event] = {}
        self._lock = RLock()

    def preflight(self) -> tuple[str, ...]:
        if self._client_factory is not None:
            return ()
        if importlib.util.find_spec("gradio_client") is None:
            return ("Falta la dependencia opcional gradio-client del entorno principal.",)
        try:
            installed = version("gradio-client")
        except PackageNotFoundError:
            return ("Falta la dependencia opcional gradio-client del entorno principal.",)
        if installed != "0.5.0":
            return (
                "Fooocus v2.5.5 requiere gradio-client 0.5.0 en el entorno principal; "
                f"se detectó {installed}.",
            )
        return ()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout_seconds) as client:
                response = await client.get(f"{self._base_url}/config")
                return response.status_code == 200 and "components" in response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def list_models(self) -> Sequence[str]:
        config = await self._configuration()
        return self._choices(config, ("base model", "checkpoint"))

    async def list_styles(self) -> Sequence[str]:
        config = await self._configuration()
        return self._choices(config, ("selected styles", "styles"))

    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[ImageGenerationEvent | BaseException | None] = asyncio.Queue()
        cancelled = Event()
        self._cancelled[request.operation_id] = cancelled

        def emit(event: ImageGenerationEvent | BaseException | None) -> None:
            loop.call_soon_threadsafe(events.put_nowait, event)

        worker = asyncio.create_task(
            asyncio.to_thread(self._generate_blocking, request, cancelled, emit)
        )
        try:
            while True:
                item = await events.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            await asyncio.gather(worker, return_exceptions=True)
            self._cancelled.pop(request.operation_id, None)
            self._active_jobs.pop(request.operation_id, None)

    async def cancel(self, operation_id: str) -> None:
        cancelled = self._cancelled.get(operation_id)
        if cancelled is not None:
            cancelled.set()
        job = self._active_jobs.get(operation_id)
        if job is not None:
            await asyncio.to_thread(job.cancel)

    async def close(self) -> None:
        for operation_id in tuple(self._cancelled):
            await self.cancel(operation_id)

    async def _configuration(self) -> Mapping[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout_seconds) as client:
                response = await client.get(f"{self._base_url}/config")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise RuntimeUnavailableError(
                f"Fooocus no expone una configuración Gradio: {error}"
            ) from error
        if not isinstance(payload, Mapping):
            raise RuntimeUnavailableError("La configuración Gradio de Fooocus no es válida.")
        return payload

    def _generate_blocking(
        self,
        request: ImageGenerationRequest,
        cancelled: Event,
        emit: Callable[[ImageGenerationEvent | BaseException | None], None],
    ) -> None:
        try:
            client = self._make_client()
            config = getattr(client, "config", None)
            if not isinstance(config, Mapping):
                raise RuntimeUnavailableError("gradio_client no entregó el esquema de Fooocus.")
            prepare_index, generate_index = self._generation_indices(config)
            arguments = self._generation_arguments(config, prepare_index, request)
            emit(
                ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.PROGRESS,
                    progress=ImageProgress(
                        stage=ImageGenerationStage.LOADING,
                        detail="Aplicando parámetros y cargando el checkpoint…",
                    ),
                )
            )
            client.predict(*arguments, fn_index=prepare_index)
            if cancelled.is_set():
                emit(self._cancelled_event(request.operation_id))
                return
            emit(
                ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.PROGRESS,
                    progress=ImageProgress(
                        stage=ImageGenerationStage.GENERATING,
                        detail="Fooocus está generando…",
                    ),
                )
            )
            job = client.submit(fn_index=generate_index)
            with self._lock:
                self._active_jobs[request.operation_id] = job
            result = job.result()
            if cancelled.is_set():
                emit(self._cancelled_event(request.operation_id))
                return
            paths = self._extract_paths(result)
            if not paths:
                raise RuntimeRequestError("Fooocus terminó sin entregar rutas de imágenes.")
            for path in paths:
                emit(
                    ImageGenerationEvent(
                        operation_id=request.operation_id,
                        kind=ImageGenerationEventKind.IMAGE,
                        source_path=path,
                    )
                )
        except BaseException as error:
            emit(error)
        finally:
            emit(None)

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            client = self._client_factory(self._base_url)
        else:
            if importlib.util.find_spec("gradio_client") is None:
                raise RuntimeUnavailableError(
                    "El cliente de la API Fooocus requiere la dependencia opcional gradio_client."
                )
            client_type = import_module("gradio_client").Client
            if self._download_root is None:
                client = client_type(self._base_url, verbose=False)
            else:
                self._download_root.mkdir(parents=True, exist_ok=True)
                client = client_type(
                    self._base_url,
                    serialize=False,
                    output_dir=str(self._download_root),
                    verbose=False,
                )
        config = getattr(client, "config", None)
        if isinstance(config, Mapping) and self._download_root is not None:
            destination = self._download_root.parent / "gradio-config.json"
            temporary = destination.with_name(destination.name + ".partial")
            temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
            temporary.replace(destination)
        return client

    @staticmethod
    def _generation_indices(config: Mapping[str, Any]) -> tuple[int, int]:
        components = GradioFooocusTransport._component_map(config)
        dependencies = config.get("dependencies")
        if not isinstance(dependencies, list):
            raise RuntimeUnavailableError("Fooocus no publicó dependencias Gradio.")
        prepare_index: int | None = None
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, Mapping):
                continue
            labels = {
                GradioFooocusTransport._label(components.get(component_id))
                for component_id in dependency.get("inputs", [])
            }
            if {"prompt", "negative prompt", "performance", "image number"} <= labels:
                prepare_index = index
                break
        if prepare_index is None:
            raise RuntimeUnavailableError(
                "El esquema Fooocus no contiene la operación de preparación esperada."
            )
        for index in range(prepare_index + 1, len(dependencies)):
            dependency = dependencies[index]
            if not isinstance(dependency, Mapping):
                continue
            output_labels = {
                GradioFooocusTransport._label(components.get(component_id))
                for component_id in dependency.get("outputs", [])
            }
            if "gallery" in output_labels and len(dependency.get("inputs", [])) <= 1:
                return prepare_index, index
        if prepare_index + 1 < len(dependencies):
            return prepare_index, prepare_index + 1
        raise RuntimeUnavailableError("El esquema Fooocus no contiene la operación de generación.")

    @staticmethod
    def _generation_arguments(
        config: Mapping[str, Any],
        dependency_index: int,
        request: ImageGenerationRequest,
    ) -> tuple[Any, ...]:
        components = GradioFooocusTransport._component_map(config)
        dependencies = config.get("dependencies", [])
        dependency = dependencies[dependency_index]
        values: list[Any] = []
        for component_id in dependency.get("inputs", []):
            component = components.get(component_id, {})
            props = component.get("props", {}) if isinstance(component, Mapping) else {}
            if isinstance(component, Mapping) and component.get("type") == "state":
                # gradio-client inserts session State values before serialization.
                continue
            label = GradioFooocusTransport._label(component)
            value = props.get("value") if isinstance(props, Mapping) else None
            if label == "prompt":
                value = request.prompt
            elif label == "negative prompt":
                value = request.negative_prompt
            elif label in {"selected styles", "styles"}:
                value = list(request.options.styles)
            elif label == "performance":
                value = GradioFooocusTransport._performance(request.options.performance)
            elif label.startswith("aspect ratios"):
                requested = f"{request.options.width}×{request.options.height}"
                choices = props.get("choices", []) if isinstance(props, Mapping) else []
                values_by_choice = [
                    choice[1] if isinstance(choice, list) and len(choice) > 1 else choice
                    for choice in choices
                ]
                value = next(
                    (
                        candidate
                        for candidate in values_by_choice
                        if isinstance(candidate, str) and candidate.startswith(requested)
                    ),
                    requested,
                )
            elif label == "image number":
                value = request.options.image_count
            elif label == "output format":
                value = request.options.output_format
            elif label == "seed":
                value = str(request.options.seed if request.options.seed is not None else -1)
            elif label == "guidance scale":
                value = request.options.guidance_scale
            elif label == "image sharpness":
                value = request.options.sharpness
            elif label in {"base model (sdxl only)", "base model"}:
                value = request.model.name
            elif label in {"input image", "enhance", "save metadata to images"}:
                value = False
            values.append(value)
        return tuple(values)

    @staticmethod
    def _component_map(config: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
        components = config.get("components", [])
        return {
            int(component["id"]): component
            for component in components
            if isinstance(component, Mapping) and isinstance(component.get("id"), int)
        }

    @staticmethod
    def _label(component: Mapping[str, Any] | None) -> str:
        if not isinstance(component, Mapping):
            return ""
        props = component.get("props")
        if not isinstance(props, Mapping):
            return ""
        label = str(props.get("label") or "").strip().casefold()
        if label:
            return label
        elem_id = str(props.get("elem_id") or "").strip().casefold()
        return {
            "positive_prompt": "prompt",
            "negative_prompt": "negative prompt",
        }.get(elem_id, "")

    @staticmethod
    def _choices(config: Mapping[str, Any], labels: tuple[str, ...]) -> tuple[str, ...]:
        for component in GradioFooocusTransport._component_map(config).values():
            if not any(label in GradioFooocusTransport._label(component) for label in labels):
                continue
            props = component.get("props", {})
            choices = props.get("choices", []) if isinstance(props, Mapping) else []
            values = []
            for choice in choices:
                value = choice[1] if isinstance(choice, list) and len(choice) > 1 else choice
                if isinstance(value, str):
                    values.append(value)
            if values:
                return tuple(values)
        return ()

    @staticmethod
    def _performance(performance: ImagePerformance) -> str:
        return {
            ImagePerformance.SPEED: "Speed",
            ImagePerformance.QUALITY: "Quality",
            ImagePerformance.EXTREME_SPEED: "Extreme Speed",
        }[performance]

    @staticmethod
    def _extract_paths(value: Any) -> tuple[Path, ...]:
        found: list[Path] = []

        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key in ("path", "name"):
                    candidate = item.get(key)
                    if isinstance(candidate, str):
                        visit(candidate)
                for nested in item.values():
                    if isinstance(nested, (list, tuple)):
                        visit(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)
            elif isinstance(item, str):
                candidate = Path(item)
                if candidate.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}:
                    found.append(candidate)

        visit(value)
        return tuple(dict.fromkeys(found))

    @staticmethod
    def _cancelled_event(operation_id: str) -> ImageGenerationEvent:
        return ImageGenerationEvent(
            operation_id=operation_id,
            kind=ImageGenerationEventKind.CANCELLED,
            progress=ImageProgress(stage=ImageGenerationStage.CANCELLED),
        )
